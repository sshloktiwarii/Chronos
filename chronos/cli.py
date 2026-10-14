"""
Chronos CLI.
Command-line interface to inspect checkpoints, run DAGs, and trigger rollbacks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from chronos.checkpoint import CheckpointStore
from chronos.compactor import ContextCompactor
from chronos.dag import DAGWorkflow
from chronos.recovery import RecoveryManager


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="chronos",
        description="Stateful Agent Orchestration & Failure Recovery Engine",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: history
    history_parser = subparsers.add_parser("history", help="Show checkpoint history")
    history_parser.add_argument(
        "--db", default=".chronos/checkpoints.db", help="Path to SQLite database"
    )

    # Subcommand: rollback
    rollback_parser = subparsers.add_parser("rollback", help="Roll back workspace to snapshot")
    rollback_parser.add_argument("snapshot_id", help="Target snapshot ID")
    rollback_parser.add_argument(
        "--workspace", default=".", help="Workspace directory to restore"
    )
    rollback_parser.add_argument(
        "--db", default=".chronos/checkpoints.db", help="Path to SQLite database"
    )

    # Subcommand: compact
    compact_parser = subparsers.add_parser("compact", help="Compact agent execution logs")
    compact_parser.add_argument("--objective", required=True, help="Task objective statement")
    compact_parser.add_argument(
        "--db", default=".chronos/checkpoints.db", help="Path to SQLite database"
    )

    args = parser.parse_args()

    if args.command == "history":
        store = CheckpointStore(Path(args.db))
        history = store.get_history()
        print(f"Total Checkpoints in {args.db}: {len(history)}\n")
        print(f"{'TIMESTAMP':<28} | {'NODE ID':<20} | {'STATUS':<12} | {'SNAPSHOT ID'}")
        print("-" * 85)
        for snap in history:
            print(
                f"{snap.timestamp.isoformat():<28} | {snap.node_id:<20} | {snap.status.value:<12} | {snap.snapshot_id}"
            )

    elif args.command == "rollback":
        workspace_path = Path(args.workspace)
        store = CheckpointStore(Path(args.db))
        recovery = RecoveryManager(workspace_path, store)
        try:
            restored = recovery.rollback_to_snapshot(args.snapshot_id)
            print(f"Successfully rolled back to snapshot {args.snapshot_id}!")
            for item in restored:
                print(f"  {item}")
        except Exception as e:
            print(f"Rollback failed: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "compact":
        store = CheckpointStore(Path(args.db))
        history = store.get_history()
        compactor = ContextCompactor()
        summary = compactor.compact_history(args.objective, history)
        print("=== CONTEXT COMPACTION REPORT ===")
        print(f"Original Tokens:  {summary.original_token_count}")
        print(f"Compacted Tokens: {summary.compacted_token_count}")
        print(f"Compression:      {summary.compression_ratio * 100:.1f}%")
        print("\n--- DISTILLED CONTEXT ---\n")
        print(summary.distilled_context)


if __name__ == "__main__":
    main()
