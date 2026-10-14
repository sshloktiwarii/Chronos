from chronos.compactor import ContextCompactor
from chronos.models import ExecutionSnapshot, NodeStatus


def test_context_compaction_reduces_tokens():
    compactor = ContextCompactor(max_token_budget=1000, retain_recent_steps=2)

    # Simulate 8 sequential steps with verbose outputs
    history = []
    for i in range(1, 9):
        verbose_stdout = f"Step {i} stdout output:\n" + ("log line debug tracing info\n" * 50)
        snap = ExecutionSnapshot(
            snapshot_id=f"snap_{i}",
            parent_id=f"snap_{i-1}" if i > 1 else None,
            node_id=f"step_{i}",
            status=NodeStatus.COMPLETED,
            stdout=verbose_stdout,
            exit_code=0,
        )
        history.append(snap)

    summary = compactor.compact_history(
        task_objective="Refactor authentication middleware and fix unit tests",
        history=history,
    )

    assert summary.original_token_count > summary.compacted_token_count
    assert summary.compression_ratio < 0.5  # Substantial token compression
    assert summary.retained_step_count == 2
    assert summary.condensed_step_count == 6
    assert "Active Goal: Refactor authentication middleware" in summary.distilled_context
    assert "Historical Execution Summary (Compacted)" in summary.distilled_context
