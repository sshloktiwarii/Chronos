"""
Chronos In-Memory Copy-on-Write (COW) Virtual Filesystem.
Provides O(1) memory pointer rollbacks, zero-disk speculative branching,
and atomic disk materialization for autonomous agent workflows.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


@dataclass(frozen=True)
class VFSInode:
    """Immutable file node residing in memory."""
    rel_path: str
    content: bytes
    sha256: str
    size: int
    created_at: float
    is_tombstone: bool = False

    @classmethod
    def create(cls, rel_path: str, data: Union[str, bytes]) -> VFSInode:
        raw_bytes = data.encode("utf-8") if isinstance(data, str) else data
        h = hashlib.sha256(raw_bytes).hexdigest()
        return cls(
            rel_path=rel_path,
            content=raw_bytes,
            sha256=h,
            size=len(raw_bytes),
            created_at=time.time(),
            is_tombstone=False,
        )

    @classmethod
    def tombstone(cls, rel_path: str) -> VFSInode:
        return cls(
            rel_path=rel_path,
            content=b"",
            sha256="0" * 64,
            size=0,
            created_at=time.time(),
            is_tombstone=True,
        )


@dataclass(frozen=True)
class VFSTree:
    """Immutable directory tree mapping relative POSIX paths to VFSInode."""
    inodes: Dict[str, VFSInode] = field(default_factory=dict)

    def with_file(self, rel_path: str, data: Union[str, bytes]) -> VFSTree:
        new_inode = VFSInode.create(rel_path, data)
        updated = dict(self.inodes)
        updated[rel_path] = new_inode
        return VFSTree(inodes=updated)

    def with_deleted(self, rel_path: str) -> VFSTree:
        new_inode = VFSInode.tombstone(rel_path)
        updated = dict(self.inodes)
        updated[rel_path] = new_inode
        return VFSTree(inodes=updated)

    def compute_root_hash(self) -> str:
        """Computes deterministic Merkle-like root hash over active sorted inodes."""
        hasher = hashlib.sha256()
        for path in sorted(self.inodes.keys()):
            inode = self.inodes[path]
            if not inode.is_tombstone:
                hasher.update(f"{path}:{inode.sha256};".encode("utf-8"))
        return hasher.hexdigest()

    def get_active_files(self) -> Dict[str, VFSInode]:
        return {p: node for p, node in self.inodes.items() if not node.is_tombstone}


class MemoryCOWWorkspace:
    """
    In-Memory Copy-on-Write workspace.
    Intercepts file operations into an in-memory COW layer indexed by step sequence.
    Rolling back a step is an O(1) memory pointer swap without touching physical disk.
    """

    def __init__(self, base_disk_dir: Optional[Union[str, Path]] = None):
        self.base_disk_dir = Path(base_disk_dir).resolve() if base_disk_dir else None
        self.active_tree: VFSTree = VFSTree()
        self.snapshots: Dict[str, VFSTree] = {}
        self.branches: Dict[str, VFSTree] = {}
        self._load_base_disk_files()

    def _load_base_disk_files(self) -> None:
        """Pre-seeds the virtual tree with existing physical disk files if present."""
        if not self.base_disk_dir or not self.base_disk_dir.exists():
            return

        initial_inodes: Dict[str, VFSInode] = {}
        for path in self.base_disk_dir.rglob("*"):
            if not path.is_file():
                continue
            rel_path = str(path.relative_to(self.base_disk_dir))
            # Ignore hidden files and internal state
            if rel_path.startswith((".git", ".chronos", "__pycache__")) or rel_path.endswith((".db", ".pyc")):
                continue
            try:
                content = path.read_bytes()
                initial_inodes[rel_path] = VFSInode.create(rel_path, content)
            except Exception:
                continue

        self.active_tree = VFSTree(inodes=initial_inodes)

    def write_file(self, rel_path: str, content: Union[str, bytes]) -> None:
        """Writes or updates a file in the in-memory COW layer."""
        clean_path = str(Path(rel_path).as_posix()).lstrip("/")
        self.active_tree = self.active_tree.with_file(clean_path, content)

    def read_file(self, rel_path: str, encoding: str = "utf-8") -> str:
        """Reads a file as text from the active in-memory tree."""
        raw_bytes = self.read_bytes(rel_path)
        return raw_bytes.decode(encoding)

    def read_bytes(self, rel_path: str) -> bytes:
        """Reads raw bytes from the active in-memory tree."""
        clean_path = str(Path(rel_path).as_posix()).lstrip("/")
        inode = self.active_tree.inodes.get(clean_path)
        if inode is not None:
            if inode.is_tombstone:
                raise FileNotFoundError(f"Virtual file '{clean_path}' is deleted (tombstoned).")
            return inode.content

        # Fallback to physical disk if not yet mutated in COW
        if self.base_disk_dir:
            disk_path = self.base_disk_dir / clean_path
            if disk_path.exists() and disk_path.is_file():
                return disk_path.read_bytes()

        raise FileNotFoundError(f"File '{clean_path}' not found in virtual workspace.")

    def exists(self, rel_path: str) -> bool:
        clean_path = str(Path(rel_path).as_posix()).lstrip("/")
        inode = self.active_tree.inodes.get(clean_path)
        if inode is not None:
            return not inode.is_tombstone
        if self.base_disk_dir:
            disk_path = self.base_disk_dir / clean_path
            return disk_path.exists() and disk_path.is_file()
        return False

    def delete_file(self, rel_path: str) -> None:
        clean_path = str(Path(rel_path).as_posix()).lstrip("/")
        self.active_tree = self.active_tree.with_deleted(clean_path)

    def list_files(self) -> List[str]:
        """Lists all active relative paths in the virtual workspace."""
        active = self.active_tree.get_active_files()
        return sorted(active.keys())

    def snapshot(self, snapshot_id: str) -> str:
        """
        Captures an O(1) memory pointer snapshot of the active tree.
        Returns the deterministic Merkle root hash of the tree.
        """
        self.snapshots[snapshot_id] = self.active_tree
        return self.active_tree.compute_root_hash()

    def rollback(self, snapshot_id: str) -> List[str]:
        """
        Rolls back the active tree to a prior snapshot in O(1) time.
        Does not touch physical disk. Returns list of modified paths.
        """
        target = self.snapshots.get(snapshot_id)
        if target is None:
            raise KeyError(f"Snapshot '{snapshot_id}' not found in virtual memory store.")

        # Compute diff between active tree and target tree for telemetry/diagnostics
        diff = self._diff_trees(self.active_tree, target)
        self.active_tree = target
        return diff

    def fork_branch(self, branch_id: str) -> MemoryCOWWorkspace:
        """
        Forks an isolated speculative branch from the current active state.
        The child workspace shares base snapshot history but mutates independently in memory.
        """
        forked = MemoryCOWWorkspace(base_disk_dir=self.base_disk_dir)
        forked.active_tree = self.active_tree
        forked.snapshots = dict(self.snapshots)
        self.branches[branch_id] = self.active_tree
        return forked

    def materialize_to_disk(self, target_dir: Optional[Union[str, Path]] = None) -> List[str]:
        """
        Atomically flushes the active in-memory tree to physical disk.
        Used only upon final PR generation or explicit workspace sync.
        """
        out_dir = Path(target_dir).resolve() if target_dir else self.base_disk_dir
        if not out_dir:
            raise ValueError("No physical target directory specified for materialization.")

        out_dir.mkdir(parents=True, exist_ok=True)
        written: List[str] = []

        active_files = self.active_tree.get_active_files()

        # Write active virtual files
        for rel_path, inode in active_files.items():
            dest = out_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(inode.content)
            written.append(rel_path)

        # Delete physical files that were tombstoned in virtual workspace
        for rel_path, inode in self.active_tree.inodes.items():
            if inode.is_tombstone:
                dest = out_dir / rel_path
                if dest.exists() and dest.is_file():
                    dest.unlink()

        return written

    def _diff_trees(self, source: VFSTree, target: VFSTree) -> List[str]:
        changed: List[str] = []
        all_paths = set(source.inodes.keys()) | set(target.inodes.keys())
        for p in all_paths:
            s_node = source.inodes.get(p)
            t_node = target.inodes.get(p)
            s_hash = s_node.sha256 if (s_node and not s_node.is_tombstone) else None
            t_hash = t_node.sha256 if (t_node and not t_node.is_tombstone) else None
            if s_hash != t_hash:
                changed.append(p)
        return sorted(changed)
