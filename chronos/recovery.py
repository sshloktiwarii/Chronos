"""
Chronos Recovery & Rollback Engine.
Handles Git content-addressable tree snapshotting, atomic rollback to prior snapshots,
and error log extraction.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set
from chronos.checkpoint import CheckpointStore
from chronos.models import ExecutionSnapshot, FileState


class RecoveryManager:
    def __init__(self, workspace_dir: Path, checkpoint_store: CheckpointStore):
        self.workspace_dir = Path(workspace_dir)
        self.store = checkpoint_store

    def _git_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        env["GIT_CONFIG_SYSTEM"] = "/dev/null"
        return env

    def is_git_workspace(self) -> bool:
        return (self.workspace_dir / ".git").is_dir()

    def capture_tree_sha(self) -> Optional[str]:
        """Captures Git content-addressable tree SHA without creating full commits."""
        if not self.is_git_workspace():
            return None

        try:
            # Stage current working tree
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(self.workspace_dir),
                env=self._git_env(),
                check=True,
                capture_output=True,
            )
            # Write index into a tree object and return its SHA
            proc = subprocess.run(
                ["git", "write-tree"],
                cwd=str(self.workspace_dir),
                env=self._git_env(),
                check=True,
                capture_output=True,
                text=True,
            )
            return proc.stdout.strip()
        except Exception:
            return None

    def capture_workspace(self, tracked_extensions: Set[str] = None) -> Dict[str, FileState]:
        manifest: Dict[str, FileState] = {}
        if not self.workspace_dir.exists():
            return manifest

        for path in self.workspace_dir.rglob("*"):
            if not path.is_file():
                continue
            # Skip hidden files or database files
            rel_path = str(path.relative_to(self.workspace_dir))
            if any(part.startswith(".") for part in path.parts) or rel_path.endswith((".db", ".db-wal", ".db-shm")):
                continue

            if tracked_extensions and not any(rel_path.endswith(ext) for ext in tracked_extensions):
                continue

            try:
                content_bytes = path.read_bytes()
                content_hash = hashlib.sha256(content_bytes).hexdigest()
                content_str = content_bytes.decode("utf-8", errors="replace")
                manifest[rel_path] = FileState(
                    relative_path=rel_path,
                    content_hash=content_hash,
                    content=content_str,
                    is_deleted=False,
                )
            except Exception:
                continue

        return manifest

    def rollback_to_snapshot(self, target_snapshot_id: str) -> List[str]:
        target_snapshot = self.store.get_snapshot(target_snapshot_id)
        if not target_snapshot:
            raise ValueError(f"Snapshot with ID '{target_snapshot_id}' not found in store.")

        restored_files: List[str] = []

        # 1. Fast path: Git content-addressable tree rollback if tree_sha exists
        if target_snapshot.tree_sha and self.is_git_workspace():
            try:
                subprocess.run(
                    ["git", "read-tree", "-u", "--reset", target_snapshot.tree_sha],
                    cwd=str(self.workspace_dir),
                    env=self._git_env(),
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "clean", "-fd"],
                    cwd=str(self.workspace_dir),
                    env=self._git_env(),
                    check=True,
                    capture_output=True,
                )
                restored_files.append(f"git_tree_reset: {target_snapshot.tree_sha}")
                return restored_files
            except Exception:
                # Fall through to manifest rollback if git command fails
                pass

        # 2. Manifest-based rollback (non-git environments or fallback)
        current_manifest = self.capture_workspace()
        target_manifest = target_snapshot.file_manifest

        # Restore files that existed in target snapshot
        for rel_path, file_state in target_manifest.items():
            target_path = self.workspace_dir / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if file_state.content is not None:
                target_path.write_text(file_state.content, encoding="utf-8")
                restored_files.append(f"restored: {rel_path}")

        # Delete files that were created after target snapshot
        for rel_path in current_manifest:
            if rel_path not in target_manifest:
                target_path = self.workspace_dir / rel_path
                if target_path.exists():
                    target_path.unlink()
                    restored_files.append(f"removed_new_file: {rel_path}")

        return restored_files

    def formulate_recovery_prompt(self, failed_snapshot: ExecutionSnapshot) -> str:
        error_context = failed_snapshot.stderr or failed_snapshot.stdout or "Command failed with non-zero exit code"
        return (
            f"--- EXECUTION FAILURE RECOVERY GATE ---\n"
            f"Node '{failed_snapshot.node_id}' failed with exit code {failed_snapshot.exit_code}.\n"
            f"Error Diagnostics:\n{error_context.strip()}\n"
            f"Action: Workspace rolled back to checkpoint '{failed_snapshot.parent_id}'.\n"
            f"Instruction: Formulate an alternative strategy addressing the error above."
        )
