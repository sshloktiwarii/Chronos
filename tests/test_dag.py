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
