"""
Chronos Recovery & Rollback Engine.
Handles isolated Git content-addressable tree snapshotting, atomic rollback to prior snapshots,
and error log extraction.
Enforces strict separation between workspace Git repositories and Chronos internal snapshot state.
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
        # Isolated Chronos internal shadow repository
        self.chronos_dir = self.workspace_dir / ".chronos"
        self.git_dir = self.chronos_dir / "git"
        self.git_index_file = self.git_dir / "index"
        self._ensure_isolated_git_repo()

    def _ensure_isolated_git_repo(self) -> None:
        """Initializes a private, isolated Git object store strictly for Chronos snapshots."""
        if not (self.git_dir / "HEAD").exists():
            self.git_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "--git-dir", str(self.git_dir), "init"],
                env=self._git_env(),
                capture_output=True,
                check=False,
            )

    def _git_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env["GIT_DIR"] = str(self.git_dir)
        env["GIT_WORK_TREE"] = str(self.workspace_dir)
        env["GIT_INDEX_FILE"] = str(self.git_index_file)
        env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        env["GIT_CONFIG_SYSTEM"] = "/dev/null"
        env["PATH"] = env.get("PATH", "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin")
        return env

    def capture_tree_sha(self) -> Optional[str]:
        """Captures Git content-addressable tree SHA using isolated .chronos/git index."""
        try:
            # Stage current working tree into isolated Chronos index
            # Exclude .chronos directory from snapshot
            subprocess.run(
                ["git", "add", "--all", "--", ":!.chronos"],
                cwd=str(self.workspace_dir),
                env=self._git_env(),
                check=True,
                capture_output=True,
            )
            # Write index into an isolated tree object and return its SHA
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
            rel_path = str(path.relative_to(self.workspace_dir))
            # Skip Chronos internal directory, hidden files, and database files
            if rel_path.startswith(".chronos") or rel_path.endswith((".db", ".db-wal", ".db-shm")):
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

        # 1. Fast path: Isolated Git content-addressable tree rollback if tree_sha exists
        if target_snapshot.tree_sha:
            try:
                subprocess.run(
                    ["git", "read-tree", "-u", "--reset", target_snapshot.tree_sha],
                    cwd=str(self.workspace_dir),
                    env=self._git_env(),
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "clean", "-fd", "-e", ".chronos"],
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

        # 2. Manifest-based rollback (fallback)
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
        receipt_info = ""
        if failed_snapshot.blake3_receipt_hash:
            receipt_info = f"\nAegis Blake3 Verification Receipt: {failed_snapshot.blake3_receipt_hash[:16]}..."

        return (
            f"--- EXECUTION FAILURE RECOVERY GATE ---\n"
            f"Node '{failed_snapshot.node_id}' failed with exit code {failed_snapshot.exit_code}.{receipt_info}\n"
            f"Error Diagnostics:\n{error_context.strip()}\n"
            f"Action: Workspace rolled back to checkpoint '{failed_snapshot.parent_id}'.\n"
            f"Instruction: Formulate an alternative strategy addressing the error above."
        )
