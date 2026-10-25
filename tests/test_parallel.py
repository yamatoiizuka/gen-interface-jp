"""Tests for the child-interpreter job runner in src/parallel.py."""

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_sigterm_to_parent_stops_children(tmp_path):
    child = (
        "import os, sys, time; "
        "open(os.path.join(sys.argv[1], str(os.getpid())), 'w').close(); "
        "time.sleep(60)"
    )
    parent = textwrap.dedent(f"""
        import sys
        from parallel import run_parallel
        command = [sys.executable, "-c", {child!r}, {str(tmp_path)!r}]
        run_parallel([command, command], 2)
    """)
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    process = subprocess.Popen([sys.executable, "-c", parent], env=env)
    try:
        deadline = time.monotonic() + 30
        while len(list(tmp_path.iterdir())) < 2:
            assert time.monotonic() < deadline, "children did not start"
            time.sleep(0.05)
        child_pids = [int(path.name) for path in tmp_path.iterdir()]

        process.send_signal(signal.SIGTERM)

        assert process.wait(timeout=30) == 128 + signal.SIGTERM
        assert not any(_pid_alive(pid) for pid in child_pids)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_failing_child_raises_and_stops_siblings(tmp_path):
    marker = tmp_path / "sibling-finished"
    failing = [sys.executable, "-c", "raise SystemExit(3)"]
    slow = [sys.executable, "-c", f"import time; time.sleep(30); open({str(marker)!r}, 'w').close()"]

    from parallel import run_parallel

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        run_parallel([slow, failing], 2)

    assert excinfo.value.returncode == 3
    assert not marker.exists()
