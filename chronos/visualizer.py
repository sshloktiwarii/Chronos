"""
Chronos Terminal DAG Visualizer.
Renders interactive/terminal visual DAG execution status trees with color coding
and cryptographic audit hashes.
"""

from __future__ import annotations

from typing import Dict, List
from chronos.models import DAGNode, ExecutionSnapshot, NodeStatus


class DAGVisualizer:
    # ANSI color escape codes
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"

    @classmethod
    def render_tree(
        cls,
        nodes: Dict[str, DAGNode],
        results: Dict[str, ExecutionSnapshot],
    ) -> str:
        lines: List[str] = [
            f"{cls.BOLD}=== Chronos DAG Execution Topology ==={cls.RESET}\n"
        ]

        for node_id, node in nodes.items():
            status = node.status
            res = results.get(node_id)

            if status == NodeStatus.COMPLETED:
                badge = f"{cls.GREEN}[✓ COMPLETED]{cls.RESET}"
            elif status in (NodeStatus.FAILED, NodeStatus.ROLLED_BACK):
                badge = f"{cls.RED}[✗ {status.value}]{cls.RESET}"
            elif status == NodeStatus.RUNNING:
                badge = f"{cls.YELLOW}[⏳ RUNNING]{cls.RESET}"
            else:
                badge = f"{cls.CYAN}[○ {status.value}]{cls.RESET}"

            deps_str = f" <- ({', '.join(node.dependencies)})" if node.dependencies else ""
            lines.append(f"• {cls.BOLD}{node.node_id:<20}{cls.RESET} {badge}{deps_str}")
            lines.append(f"  {cls.GRAY}Description: {node.description}{cls.RESET}")

            if res:
                if res.tree_sha:
                    lines.append(f"  {cls.GRAY}Git Tree SHA: {res.tree_sha[:12]}...{cls.RESET}")
                if res.blake3_receipt_hash:
                    lines.append(f"  {cls.CYAN}Aegis Blake3 Receipt: {res.blake3_receipt_hash[:16]}...{cls.RESET}")
                if res.error_summary:
                    lines.append(f"  {cls.RED}Error Note: {res.error_summary}{cls.RESET}")

            lines.append("")

        return "\n".join(lines)
