"""
Chronos Core Data Models.
Defines schemas for DAG execution, state snapshots, and context compactions.
Engineered with standard library dataclasses for zero-dependency reliability.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class NodeStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    SKIPPED = "SKIPPED"


@dataclass
class FileState:
    relative_path: str
    content_hash: str
    content: Optional[str] = None
    is_deleted: bool = False

    def model_dump(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def model_validate(cls, data: Dict[str, Any]) -> FileState:
        return cls(
            relative_path=data["relative_path"],
            content_hash=data["content_hash"],
            content=data.get("content"),
            is_deleted=data.get("is_deleted", False),
        )


@dataclass
class ExecutionSnapshot:
    snapshot_id: str
    node_id: str
    parent_id: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: NodeStatus = NodeStatus.PENDING
    inputs: Dict[str, str] = field(default_factory=dict)
    outputs: Dict[str, str] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    tree_sha: Optional[str] = None
    file_manifest: Dict[str, FileState] = field(default_factory=dict)
    error_summary: Optional[str] = None

    def model_dump(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        d["status"] = self.status.value
        d["file_manifest"] = {k: v.model_dump() for k, v in self.file_manifest.items()}
        return d

    @classmethod
    def model_validate(cls, data: Dict[str, Any]) -> ExecutionSnapshot:
        ts = data["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        manifest = {
            k: FileState.model_validate(v)
            for k, v in data.get("file_manifest", {}).items()
        }
        return cls(
            snapshot_id=data["snapshot_id"],
            node_id=data["node_id"],
            parent_id=data.get("parent_id"),
            timestamp=ts,
            status=NodeStatus(data["status"]),
            inputs=data.get("inputs", {}),
            outputs=data.get("outputs", {}),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            exit_code=data.get("exit_code", 0),
            tree_sha=data.get("tree_sha"),
            file_manifest=manifest,
            error_summary=data.get("error_summary"),
        )


@dataclass
class DAGNode:
    node_id: str
    name: str
    description: str
    dependencies: List[str] = field(default_factory=list)
    command: Optional[str] = None
    retry_limit: int = 2
    retry_count: int = 0
    status: NodeStatus = NodeStatus.PENDING
    fallback_node_id: Optional[str] = None


@dataclass
class CompactionSummary:
    original_token_count: int
    compacted_token_count: int
    compression_ratio: float
    retained_step_count: int
    condensed_step_count: int
    distilled_context: str
