import tempfile
from pathlib import Path
from chronos.checkpoint import CheckpointStore
from chronos.dag import DAGWorkflow
from chronos.models import NodeStatus
from chronos.recovery import RecoveryManager


def test_automatic_rollback_on_failure():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test_checkpoints.db"
        store = CheckpointStore(db_path)

        workflow = DAGWorkflow(
            name="test_rollback",
            workspace_dir=tmp_path,
            checkpoint_store=store,
        )

        # Step 1: creates good file
        workflow.add_node(
            node_id="setup",
            name="Setup Baseline",
            description="Good file setup",
            command="echo 'stable code v1' > app.py",
        )

        # Step 2: corrupts file and fails (exit code 1)
        workflow.add_node(
            node_id="broken_patch",
            name="Corrupt Code",
            description="Applies broken code and fails",
            dependencies=["setup"],
            command="echo 'syntax_error!@#' > app.py && exit 1",
            retry_limit=0,
        )

        results = workflow.run()

        assert results["setup"].status == NodeStatus.COMPLETED
        assert results["broken_patch"].status == NodeStatus.FAILED

        # Workspace should have been restored automatically to prior checkpoint ('setup')
        content = (tmp_path / "app.py").read_text()
        assert "stable code v1" in content
        assert "syntax_error" not in content


def test_manual_rollback_to_arbitrary_snapshot():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test_checkpoints.db"
        store = CheckpointStore(db_path)
        recovery = RecoveryManager(tmp_path, store)

        # Create file A
        (tmp_path / "fileA.txt").write_text("version 1")
        manifest_1 = recovery.capture_workspace()

        from chronos.models import ExecutionSnapshot
        import uuid

        snap1 = ExecutionSnapshot(
            snapshot_id="snap-1",
            parent_id=None,
            node_id="node_1",
            status=NodeStatus.COMPLETED,
            file_manifest=manifest_1,
        )
        store.record_snapshot(snap1)

        # Create file B and edit file A
        (tmp_path / "fileA.txt").write_text("version 2 corrupted")
        (tmp_path / "fileB.txt").write_text("unwanted new file")

        # Rollback to snap-1
        restored = recovery.rollback_to_snapshot("snap-1")

        assert any("restored: fileA.txt" in r for r in restored)
        assert any("removed_new_file: fileB.txt" in r for r in restored)

        # Verify filesystem state
        assert (tmp_path / "fileA.txt").read_text() == "version 1"
        assert not (tmp_path / "fileB.txt").exists()
