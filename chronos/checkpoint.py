"""
Chronos Checkpoint Store.
SQLite WAL-backed immutable snapshot ledger adhering strictly to systems invariants.
Stores execution metadata, DAG transitions, Git content-addressable tree SHAs,
and Aegis Blake3 cryptographic audit receipts.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional
from chronos.models import ExecutionSnapshot, FileState, NodeStatus


class CheckpointStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        # Systems Engineering Invariants:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA wal_autocheckpoint=1000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    parent_id TEXT,
                    node_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    status TEXT NOT NULL,
                    inputs_json TEXT NOT NULL,
                    outputs_json TEXT NOT NULL,
                    stdout TEXT NOT NULL,
                    stderr TEXT NOT NULL,
                    exit_code INTEGER NOT NULL,
                    tree_sha TEXT,
                    blake3_receipt_hash TEXT,
                    blake3_stdout_hash TEXT,
                    blake3_stderr_hash TEXT,
                    file_manifest_json TEXT NOT NULL,
                    error_summary TEXT,
                    FOREIGN KEY (parent_id) REFERENCES snapshots(snapshot_id)
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_snapshots_node_id ON snapshots(node_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON snapshots(timestamp);"
            )
            conn.commit()

    def record_snapshot(self, snapshot: ExecutionSnapshot) -> None:
        with self._get_connection() as conn:
            manifest_serialized = {
                path: file_state.model_dump()
                for path, file_state in snapshot.file_manifest.items()
            }
            conn.execute(
                """
                INSERT INTO snapshots (
                    snapshot_id, parent_id, node_id, timestamp, status,
                    inputs_json, outputs_json, stdout, stderr, exit_code,
                    tree_sha, blake3_receipt_hash, blake3_stdout_hash, blake3_stderr_hash,
                    file_manifest_json, error_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.parent_id,
                    snapshot.node_id,
                    snapshot.timestamp.isoformat(),
                    snapshot.status.value,
                    json.dumps(snapshot.inputs),
                    json.dumps(snapshot.outputs),
                    snapshot.stdout,
                    snapshot.stderr,
                    snapshot.exit_code,
                    snapshot.tree_sha,
                    snapshot.blake3_receipt_hash,
                    snapshot.blake3_stdout_hash,
                    snapshot.blake3_stderr_hash,
                    json.dumps(manifest_serialized),
                    snapshot.error_summary,
                ),
            )
            conn.commit()

    def get_snapshot(self, snapshot_id: str) -> Optional[ExecutionSnapshot]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM snapshots WHERE snapshot_id = ?;", (snapshot_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_snapshot(row)

    def get_latest_snapshot(self) -> Optional[ExecutionSnapshot]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT 1;"
            )
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_snapshot(row)

    def get_history(self) -> List[ExecutionSnapshot]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM snapshots ORDER BY timestamp ASC;")
            return [self._row_to_snapshot(row) for row in cur.fetchall()]

    def _row_to_snapshot(self, row: sqlite3.Row) -> ExecutionSnapshot:
        manifest_raw = json.loads(row["file_manifest_json"])
        manifest = {
            path: FileState.model_validate(data) for path, data in manifest_raw.items()
        }
        keys = row.keys()
        tree_sha = row["tree_sha"] if "tree_sha" in keys else None
        blake3_receipt = row["blake3_receipt_hash"] if "blake3_receipt_hash" in keys else None
        blake3_stdout = row["blake3_stdout_hash"] if "blake3_stdout_hash" in keys else None
        blake3_stderr = row["blake3_stderr_hash"] if "blake3_stderr_hash" in keys else None

        return ExecutionSnapshot(
            snapshot_id=row["snapshot_id"],
            parent_id=row["parent_id"],
            node_id=row["node_id"],
            timestamp=row["timestamp"],
            status=NodeStatus(row["status"]),
            inputs=json.loads(row["inputs_json"]),
            outputs=json.loads(row["outputs_json"]),
            stdout=row["stdout"],
            stderr=row["stderr"],
            exit_code=row["exit_code"],
            tree_sha=tree_sha,
            blake3_receipt_hash=blake3_receipt,
            blake3_stdout_hash=blake3_stdout,
            blake3_stderr_hash=blake3_stderr,
            file_manifest=manifest,
            error_summary=row["error_summary"],
        )
