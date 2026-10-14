"""
Chronos Context Compactor.
Token-aware context compression engine that condenses multi-step agent histories
into structured summaries, file diffs, and bounded context representations.
"""

from __future__ import annotations

from typing import List
from chronos.models import CompactionSummary, ExecutionSnapshot


class ContextCompactor:
    def __init__(self, max_token_budget: int = 4000, retain_recent_steps: int = 3):
        self.max_token_budget = max_token_budget
        self.retain_recent_steps = retain_recent_steps

    @staticmethod
    def estimate_tokens(text: str) -> int:
        # Standard rough heuristic: ~4 characters per token
        return max(1, len(text) // 4)

    def compact_history(
        self,
        task_objective: str,
        history: List[ExecutionSnapshot],
    ) -> CompactionSummary:
        if not history:
            summary_text = f"Objective: {task_objective}\nNo execution steps recorded yet."
            tokens = self.estimate_tokens(summary_text)
            return CompactionSummary(
                original_token_count=tokens,
                compacted_token_count=tokens,
                compression_ratio=1.0,
                retained_step_count=0,
                condensed_step_count=0,
                distilled_context=summary_text,
            )

        # Estimate uncompressed raw token count
        raw_text_parts = [f"Objective: {task_objective}"]
        for snap in history:
            raw_text_parts.append(
                f"Node: {snap.node_id} | Status: {snap.status.value}\n"
                f"Stdout:\n{snap.stdout}\nStderr:\n{snap.stderr}"
            )
        raw_full_text = "\n\n".join(raw_text_parts)
        original_tokens = self.estimate_tokens(raw_full_text)

        # Split history into condensed earlier steps and full recent steps
        condensed_steps = history[: -self.retain_recent_steps] if len(history) > self.retain_recent_steps else []
        recent_steps = history[-self.retain_recent_steps :] if len(history) > self.retain_recent_steps else history

        distilled_lines: List[str] = [
            f"# Active Goal: {task_objective}",
            "\n## Historical Execution Summary (Compacted)",
        ]

        if condensed_steps:
            for step in condensed_steps:
                status_symbol = "✓" if step.status.value == "COMPLETED" else "✗"
                file_changes = len(step.file_manifest)
                distilled_lines.append(
                    f"- [{status_symbol}] Node '{step.node_id}' ({step.status.value}): "
                    f"Exit {step.exit_code}, {file_changes} files affected."
                )
        else:
            distilled_lines.append("- (Beginning of workflow, no prior steps to condense)")

        distilled_lines.append("\n## Recent Action Details (High Fidelity)")
        for step in recent_steps:
            distilled_lines.append(f"### Step: {step.node_id} [{step.status.value}]")
            if step.stdout:
                truncated_stdout = self._truncate_output(step.stdout, max_lines=15)
                distilled_lines.append(f"Stdout:\n```\n{truncated_stdout}\n```")
            if step.stderr:
                truncated_stderr = self._truncate_output(step.stderr, max_lines=15)
                distilled_lines.append(f"Stderr:\n```\n{truncated_stderr}\n```")
            if step.error_summary:
                distilled_lines.append(f"Error Note: {step.error_summary}")

        distilled_context = "\n".join(distilled_lines)
        compacted_tokens = self.estimate_tokens(distilled_context)
        compression_ratio = round(compacted_tokens / max(1, original_tokens), 3)

        return CompactionSummary(
            original_token_count=original_tokens,
            compacted_token_count=compacted_tokens,
            compression_ratio=compression_ratio,
            retained_step_count=len(recent_steps),
            condensed_step_count=len(condensed_steps),
            distilled_context=distilled_context,
        )

    def _truncate_output(self, text: str, max_lines: int) -> str:
        lines = text.strip().splitlines()
        if len(lines) <= max_lines:
            return text.strip()
        head = lines[: max_lines // 2]
        tail = lines[-(max_lines // 2) :]
        return "\n".join(head) + f"\n... [{len(lines) - max_lines} lines compressed] ...\n" + "\n".join(tail)
