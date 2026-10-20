import tempfile
from pathlib import Path
from chronos.checkpoint import CheckpointStore
from chronos.dag import DAGWorkflow
from chronos.models import NodeStatus


def test_dag_successful_linear_execution():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test_checkpoints.db"
        store = CheckpointStore(db_path)

        workflow = DAGWorkflow(
            name="test_linear",
            workspace_dir=tmp_path,
            checkpoint_store=store,
        )

        workflow.add_node(
            node_id="step_1",
            name="Initialize Files",
            description="Creates initial test file",
            command="echo 'step 1 data' > file1.txt",
        )
        workflow.add_node(
            node_id="step_2",
            name="Process Files",
            description="Appends to file",
            dependencies=["step_1"],
            command="echo 'step 2 data' >> file1.txt",
        )

        results = workflow.run()

        assert "step_1" in results
        assert "step_2" in results
        assert results["step_1"].status == NodeStatus.COMPLETED
        assert results["step_2"].status == NodeStatus.COMPLETED

        # Check workspace file contents
        content = (tmp_path / "file1.txt").read_text()
        assert "step 1 data" in content
        assert "step 2 data" in content

        # Check database records
        history = store.get_history()
        assert len(history) == 3  # BASELINE + step_1 + step_2


def test_dag_cycle_detection_error():
    import pytest
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        store = CheckpointStore(tmp_path / "test_cycle.db")
        workflow = DAGWorkflow(name="test_cycle", workspace_dir=tmp_path, checkpoint_store=store)
        workflow.add_node("A", "Node A", "A", dependencies=["B"], command="echo A")
        workflow.add_node("B", "Node B", "B", dependencies=["A"], command="echo B")

        with pytest.raises(ValueError, match="Cycle detected"):
            workflow.run()


def test_dag_missing_dependency_error():
    import pytest
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        store = CheckpointStore(tmp_path / "test_missing.db")
        workflow = DAGWorkflow(name="test_missing", workspace_dir=tmp_path, checkpoint_store=store)
        workflow.add_node("A", "Node A", "A", dependencies=["non_existent_node"], command="echo A")

        with pytest.raises(ValueError, match="does not exist"):
            workflow.run()


def test_dag_dynamic_backpressure_throttling():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        store = CheckpointStore(tmp_path / "test_bp.db")
        workflow = DAGWorkflow(name="test_backpressure", workspace_dir=tmp_path, checkpoint_store=store)
        assert workflow.max_concurrency == 4
        assert not workflow.is_throttled

        workflow.apply_backpressure(True, "High Mach memory swap pressure")
        assert workflow.is_throttled
        assert workflow.max_concurrency == 1
        assert "Mach" in workflow.throttle_reason

        workflow.apply_backpressure(False)
        assert not workflow.is_throttled
        assert workflow.max_concurrency == 4

