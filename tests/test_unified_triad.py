"""
Unit / Integration Test for Unified Triad.
Validates end-to-end integration across:
- Inferno Telemetry Proxy (HTTP JSON API + TTFT/Queue headers)
- Aegis Sandbox (Blake3 audit receipt generation + isolated execution)
- Chronos Orchestrator (SQLite WAL ledger + isolated Git tree snapshot & rollback)
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from chronos.checkpoint import CheckpointStore
from chronos.models import ExecutionSnapshot, NodeStatus
from chronos.recovery import RecoveryManager


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_unified_triad_integration():
    workspace_root = Path(__file__).resolve().parent.parent.parent
    aegis_bin = workspace_root / "aegis-sandbox" / "target" / "debug" / "aegis"
    inferno_dir = workspace_root / "inferno-telemetry-proxy"
    inferno_bin = inferno_dir / "inferno"

    # Verify binaries exist
    if not aegis_bin.exists():
        subprocess.run(["cargo", "build", "--offline"], cwd=str(workspace_root / "aegis-sandbox"), check=True)
    if not inferno_bin.exists():
        subprocess.run(["go", "build", "-buildvcs=false", "-o", "inferno", "."], cwd=str(inferno_dir), check=True)

    # 1. Start Inferno on dynamic port
    port = find_free_port()
    proc = subprocess.Popen(
        [str(inferno_bin), "-port", str(port)],
        cwd=str(inferno_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="triad_test_"))

    try:
        # Wait for health
        healthy = False
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.0) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(0.1)

        assert healthy, f"Inferno proxy did not start on port {port}"

        # 2. Call Inferno LLM completion
        payload = json.dumps({
            "model": "inferno-llama-3-8b",
            "messages": [{"role": "user", "content": "Generate a test file"}],
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            assert resp.status == 200
            headers = dict(resp.headers)
            assert "X-Inferno-Prefill-Ms" in headers, "Missing TTFT prefill header"
            assert "X-Inferno-Queue-Ms" in headers, "Missing queue latency header"

        # 3. Execute command through Aegis sandbox
        db_path = tmp_dir / ".chronos" / "checkpoints.db"
        store = CheckpointStore(db_path)
        recovery = RecoveryManager(tmp_dir, store)

        cmd = "echo 'initial_clean_state' > module.py"
        aegis_proc = subprocess.run(
            [str(aegis_bin), "exec", "--command", cmd, "--workspace", str(tmp_dir), "--backend", "process", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        res_data = json.loads(aegis_proc.stdout)
        assert res_data["exit_code"] == 0
        receipt = res_data["receipt"]
        assert "execution_receipt_hash" in receipt
        blake3_receipt = receipt["execution_receipt_hash"]

        # Capture snapshot with Blake3 receipt
        tree_sha_1 = recovery.capture_tree_sha()
        assert tree_sha_1 is not None

        snap_1 = ExecutionSnapshot(
            snapshot_id="snap-1",
            node_id="init_node",
            status=NodeStatus.COMPLETED,
            inputs={"cmd": cmd},
            outputs={"file": "module.py"},
            stdout=res_data["stdout"],
            stderr=res_data["stderr"],
            exit_code=0,
            tree_sha=tree_sha_1,
            blake3_receipt_hash=blake3_receipt,
            file_manifest=recovery.capture_workspace(),
        )
        store.record_snapshot(snap_1)

        # 4. Trigger failure & verify rollback
        corrupt_cmd = "echo 'dirty_modification' >> module.py && exit 1"
        aegis_fail = subprocess.run(
            [str(aegis_bin), "exec", "--command", corrupt_cmd, "--workspace", str(tmp_dir), "--backend", "process", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert aegis_fail.returncode != 0
        fail_data = json.loads(aegis_fail.stdout)

        # Rollback via Chronos
        recovery.rollback_to_snapshot("snap-1")
        restored_content = (tmp_dir / "module.py").read_text().strip()
        assert restored_content == "initial_clean_state", "Rollback failed to restore module.py"

        # 5. Verify SQLite WAL audit trail
        history = store.get_history()
        assert len(history) == 1
        recorded_snap = history[0]
        assert recorded_snap.blake3_receipt_hash == blake3_receipt
        assert recorded_snap.tree_sha == tree_sha_1

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp_dir, ignore_errors=True)
