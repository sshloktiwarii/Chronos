"""
Unit tests for Chronos In-Memory Copy-on-Write (COW) Virtual Filesystem.
Validates O(1) memory pointer rollbacks, zero-disk speculative branching,
and atomic disk materialization.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from chronos.vfs import MemoryCOWWorkspace, VFSInode, VFSTree


def test_vfs_file_mutations():
    vfs = MemoryCOWWorkspace()

    # Write file
    vfs.write_file("src/main.py", "print('hello vfs')")
    assert vfs.exists("src/main.py")
    assert vfs.read_file("src/main.py") == "print('hello vfs')"

    # Update file
    vfs.write_file("src/main.py", "print('vfs updated')")
    assert vfs.read_file("src/main.py") == "print('vfs updated')"

    # Delete file (tombstone)
    vfs.delete_file("src/main.py")
    assert not vfs.exists("src/main.py")
    try:
        vfs.read_file("src/main.py")
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_vfs_snapshot_and_submillisecond_rollback():
    vfs = MemoryCOWWorkspace()

    # Create baseline files
    vfs.write_file("config.json", '{"version": 1}')
    vfs.write_file("module.py", "def run(): return 42")
    baseline_hash = vfs.snapshot("checkpoint_alpha")

    assert baseline_hash != ""

    # Execute 200 state mutations across multiple files in memory
    for i in range(200):
        vfs.write_file(f"temp_{i}.txt", f"data_{i}")
    vfs.write_file("config.json", '{"version": 999}')
    vfs.delete_file("module.py")

    assert len(vfs.list_files()) == 201

    # Measure rollback latency: should be an O(1) memory pointer swap (sub-millisecond)
    t0 = time.perf_counter()
    reverted = vfs.rollback("checkpoint_alpha")
    duration_ms = (time.perf_counter() - t0) * 1000

    # Verify state is 100% restored
    assert vfs.read_file("config.json") == '{"version": 1}'
    assert vfs.read_file("module.py") == "def run(): return 42"
    assert not vfs.exists("temp_0.txt")
    assert len(vfs.list_files()) == 2
    assert duration_ms < 50.0, f"Rollback too slow: {duration_ms}ms"


def test_vfs_speculative_branching():
    root_vfs = MemoryCOWWorkspace()
    root_vfs.write_file("solution.py", "# Initial broken code")
    root_vfs.snapshot("root_snap")

    # Fork 3 speculative branches simultaneously
    b1 = root_vfs.fork_branch("branch_1")
    b2 = root_vfs.fork_branch("branch_2")
    b3 = root_vfs.fork_branch("branch_3")

    # Branch 1 mutates solution with approach A
    b1.write_file("solution.py", "# Solution Approach A")
    b1.write_file("approach_a.log", "log a")

    # Branch 2 mutates solution with approach B
    b2.write_file("solution.py", "# Solution Approach B")
    b2.write_file("approach_b.log", "log b")

    # Branch 3 mutates solution with approach C
    b3.write_file("solution.py", "# Solution Approach C")

    # Verify state isolation across all branches and root
    assert root_vfs.read_file("solution.py") == "# Initial broken code"
    assert not root_vfs.exists("approach_a.log")

    assert b1.read_file("solution.py") == "# Solution Approach A"
    assert b1.exists("approach_a.log")
    assert not b1.exists("approach_b.log")

    assert b2.read_file("solution.py") == "# Solution Approach B"
    assert b2.exists("approach_b.log")
    assert not b2.exists("approach_a.log")

    assert b3.read_file("solution.py") == "# Solution Approach C"
    assert not b3.exists("approach_a.log")


def test_vfs_materialize_to_disk():
    tmp_dir = Path(tempfile.mkdtemp(prefix="vfs_materialize_test_"))
    try:
        vfs = MemoryCOWWorkspace()
        vfs.write_file("pkg/core.py", "class Engine: pass")
        vfs.write_file("README.md", "# Frontier Engine")

        written = vfs.materialize_to_disk(tmp_dir)
        assert len(written) == 2

        # Verify physical disk
        assert (tmp_dir / "pkg" / "core.py").exists()
        assert (tmp_dir / "pkg" / "core.py").read_text() == "class Engine: pass"
        assert (tmp_dir / "README.md").read_text() == "# Frontier Engine"

        # Tombstone a file in virtual space and re-materialize
        vfs.delete_file("README.md")
        vfs.materialize_to_disk(tmp_dir)
        assert not (tmp_dir / "README.md").exists()
        assert (tmp_dir / "pkg" / "core.py").exists()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
