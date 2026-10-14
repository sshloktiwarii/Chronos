import subprocess
import tempfile
from pathlib import Path
from chronos.checkpoint import CheckpointStore
from chronos.dag import DAGWorkflow
from chronos.models import ExecutionSnapshot, NodeStatus
from chronos.recovery import RecoveryManager


def test_automatic_rollback_on_failure():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / ".chronos" / "test_checkpoints.db"
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
        db_path = tmp_path / ".chronos" / "test_checkpoints.db"
        store = CheckpointStore(db_path)
        recovery = RecoveryManager(tmp_path, store)

        # Create file A
        (tmp_path / "fileA.txt").write_text("version 1")
        manifest_1 = recovery.capture_workspace()

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


def test_git_tree_content_addressable_rollback():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / ".chronos" / "test_checkpoints.db"
        store = CheckpointStore(db_path)
        recovery = RecoveryManager(tmp_path, store)

        # State 1: create core files
        (tmp_path / "main.py").write_text("def run(): pass\n")
        (tmp_path / "config.json").write_text('{"env": "prod"}')

        tree_sha_1 = recovery.capture_tree_sha()
        assert tree_sha_1 is not None

        snap1 = ExecutionSnapshot(
            snapshot_id="snap-git-1",
            node_id="baseline",
            status=NodeStatus.COMPLETED,
            tree_sha=tree_sha_1,
        )
        store.record_snapshot(snap1)

        # State 2: Mutate main.py and introduce unwanted temp file
        (tmp_path / "main.py").write_text("corrupted code syntax error")
        (tmp_path / "rogue.tmp").write_text("garbage")

        # Execute rollback to snap-git-1 using Git tree reset
        restored = recovery.rollback_to_snapshot("snap-git-1")
        assert any("git_tree_reset" in r for r in restored)

        # Verify filesystem restoration
        assert (tmp_path / "main.py").read_text() == "def run(): pass\n"
        assert not (tmp_path / "rogue.tmp").exists()


def test_isolated_git_immune_to_workspace_git_commands():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / ".chronos" / "test_checkpoints.db"
        store = CheckpointStore(db_path)
        recovery = RecoveryManager(tmp_path, store)

        git_env = dict(
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_CONFIG_SYSTEM="/dev/null",
            PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        )

        # Agent runs git init in its own workspace root
        subprocess.run(["git", "init"], cwd=str(tmp_path), env=git_env, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "AgentUser"], cwd=str(tmp_path), env=git_env, check=True)
        subprocess.run(["git", "config", "user.email", "agent@test.com"], cwd=str(tmp_path), env=git_env, check=True)

        # Create file and commit via agent Git
        (tmp_path / "solution.py").write_text("print('clean solution')\n")
        subprocess.run(["git", "add", "solution.py"], cwd=str(tmp_path), env=git_env, check=True)
        subprocess.run(["git", "commit", "-m", "agent commit 1"], cwd=str(tmp_path), env=git_env, check=True)

        # Chronos captures snapshot into .chronos/git
        tree_sha = recovery.capture_tree_sha()
        assert tree_sha is not None

        snap = ExecutionSnapshot(
            snapshot_id="chronos-snap-1",
            node_id="agent_checkpoint",
            status=NodeStatus.COMPLETED,
            tree_sha=tree_sha,
            blake3_receipt_hash="b3_dummy_receipt_hash_12345678",
        )
        store.record_snapshot(snap)

        # Agent corrupts solution.py and modifies workspace git index
        (tmp_path / "solution.py").write_text("corrupted solution")
        subprocess.run(["git", "add", "solution.py"], cwd=str(tmp_path), env=git_env, check=True)

        # Chronos rollback to snap-1
        recovery.rollback_to_snapshot("chronos-snap-1")

        # Solution file must be restored
        assert (tmp_path / "solution.py").read_text() == "print('clean solution')\n"
        # Database must have recorded the Blake3 receipt
        retrieved = store.get_snapshot("chronos-snap-1")
        assert retrieved.blake3_receipt_hash == "b3_dummy_receipt_hash_12345678"
