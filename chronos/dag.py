"""
Chronos Directed Acyclic Graph (DAG) Execution Engine.
Orchestrates multi-step workflows with dependency resolution, retry budgets,
and failure-triggered state rollback.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Dict, List, Optional
from chronos.checkpoint import CheckpointStore
from chronos.models import DAGNode, ExecutionSnapshot, NodeStatus
from chronos.recovery import RecoveryManager


class DAGWorkflow:
    def __init__(
        self,
        name: str,
        workspace_dir: Path,
        checkpoint_store: CheckpointStore,
        max_concurrency: int = 4,
    ):
        self.name = name
        self.workspace_dir = Path(workspace_dir)
        self.store = checkpoint_store
        self.recovery = RecoveryManager(self.workspace_dir, self.store)
        self.nodes: Dict[str, DAGNode] = {}
        self.execution_order: List[str] = []
        self.max_concurrency = max_concurrency
        self.is_throttled: bool = False
        self.throttle_reason: str = ""

    def apply_backpressure(self, throttle: bool, reason: str = "") -> None:
        """Dynamically adjusts DAG execution concurrency based on hardware telemetry pressure."""
        self.is_throttled = throttle
        self.throttle_reason = reason
        if throttle:
            self.max_concurrency = 1
        else:
            self.max_concurrency = 4

    def add_node(
        self,
        node_id: str,
        name: str,
        description: str,
        dependencies: Optional[List[str]] = None,
        command: Optional[str] = None,
        retry_limit: int = 2,
        fallback_node_id: Optional[str] = None,
    ) -> None:
        self.nodes[node_id] = DAGNode(
            node_id=node_id,
            name=name,
            description=description,
            dependencies=dependencies or [],
            command=command,
            retry_limit=retry_limit,
            fallback_node_id=fallback_node_id,
        )

    def _resolve_topological_sort(self) -> List[str]:
        visited: Dict[str, bool] = {}
        order: List[str] = []

        def dfs(node_id: str, path: List[str]) -> None:
            if node_id in path:
                raise ValueError(f"Cycle detected in DAG: {' -> '.join(path + [node_id])}")
            if visited.get(node_id, False):
                return
            path.append(node_id)
            node = self.nodes.get(node_id)
            if not node:
                raise ValueError(f"Dependency node '{node_id}' does not exist.")
            for dep in node.dependencies:
                dfs(dep, path)
            path.pop()
            visited[node_id] = True
            order.append(node_id)

        for nid in self.nodes:
            if not visited.get(nid, False):
                dfs(nid, [])

        return order

    def run(self) -> Dict[str, ExecutionSnapshot]:
        execution_order = self._resolve_topological_sort()
        results: Dict[str, ExecutionSnapshot] = {}
        parent_snapshot_id: Optional[str] = None

        # Take initial clean baseline snapshot
        baseline_manifest = self.recovery.capture_workspace()
        baseline_tree_sha = self.recovery.capture_tree_sha()
        baseline_snapshot = ExecutionSnapshot(
            snapshot_id=str(uuid.uuid4()),
            parent_id=None,
            node_id="BASELINE_ROOT",
            status=NodeStatus.COMPLETED,
            stdout="Workspace baseline initialized.",
            tree_sha=baseline_tree_sha,
            file_manifest=baseline_manifest,
        )
        self.store.record_snapshot(baseline_snapshot)
        parent_snapshot_id = baseline_snapshot.snapshot_id

        i = 0
        while i < len(execution_order):
            node_id = execution_order[i]
            node = self.nodes[node_id]

            # Verify dependencies succeeded
            deps_ok = all(
                results.get(dep) and results[dep].status == NodeStatus.COMPLETED
                for dep in node.dependencies
            )
            if not deps_ok:
                node.status = NodeStatus.SKIPPED
                i += 1
                continue

            node.status = NodeStatus.RUNNING
            pre_exec_parent = parent_snapshot_id

            # Execute command if defined
            stdout = ""
            stderr = ""
            exit_code = 0
            if node.command:
                proc = subprocess.run(
                    node.command,
                    shell=True,
                    cwd=str(self.workspace_dir),
                    capture_output=True,
                    text=True,
                )
                stdout = proc.stdout
                stderr = proc.stderr
                exit_code = proc.returncode

            current_manifest = self.recovery.capture_workspace()
            current_tree_sha = self.recovery.capture_tree_sha()

            if exit_code == 0:
                node.status = NodeStatus.COMPLETED
                snap = ExecutionSnapshot(
                    snapshot_id=str(uuid.uuid4()),
                    parent_id=parent_snapshot_id,
                    node_id=node.node_id,
                    status=NodeStatus.COMPLETED,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    tree_sha=current_tree_sha,
                    file_manifest=current_manifest,
                )
                self.store.record_snapshot(snap)
                results[node.node_id] = snap
                parent_snapshot_id = snap.snapshot_id
                i += 1
            else:
                # Failure detected: execute failure recovery
                node.status = NodeStatus.FAILED
                failed_snap = ExecutionSnapshot(
                    snapshot_id=str(uuid.uuid4()),
                    parent_id=parent_snapshot_id,
                    node_id=node.node_id,
                    status=NodeStatus.FAILED,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    tree_sha=current_tree_sha,
                    file_manifest=current_manifest,
                    error_summary=f"Execution error in node {node.node_id}",
                )
                self.store.record_snapshot(failed_snap)

                # Automatic Rollback to prior clean checkpoint
                if pre_exec_parent:
                    self.recovery.rollback_to_snapshot(pre_exec_parent)

                # Check if retry budget allows or fallback node is present
                if node.retry_count < node.retry_limit:
                    node.retry_count += 1
                    # In a live agent system, prompt would be adjusted with recovery diagnostics
                    continue
                elif node.fallback_node_id and node.fallback_node_id in self.nodes:
                    # Branch to fallback node
                    execution_order.insert(i + 1, node.fallback_node_id)
                    results[node.node_id] = failed_snap
                    i += 1
                else:
                    results[node.node_id] = failed_snap
                    i += 1

        return results
