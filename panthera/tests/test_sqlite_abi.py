"""SQLite handles and four-byte guest outputs; no vendor database required."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("host", [
    Path(os.environ.get("PANTHERA_TEST_HOST", ROOT / "build/tiger_host.exe")),
    Path(os.environ.get("PANTHERA_TEST_UC_HOST", ROOT / "build/uc/tiger_host_uc.exe")),
], ids=["native", "emulated"])
@pytest.mark.parametrize("disabled", [False, True])
def test_sqlite_guest_outputs(host, disabled):
    if not host.is_file():
        pytest.skip(f"build the host first: {host}")
    env = os.environ.copy()
    env["TIGER_SQLITE"] = "0" if disabled else "1"
    run = subprocess.run([str(host), "--sqlite-check"], env=env,
                         capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "PASS" in run.stdout
    if disabled:
        assert "unavailable outputs PASS" in run.stdout
