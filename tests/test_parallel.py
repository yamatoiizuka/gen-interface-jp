"""Tests for the child-interpreter job runner in src/parallel.py."""

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import parallel
from parallel import deferred_signals, run_parallel

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _sleeper(pid_dir: Path, ignore_term: bool = False) -> list[str]:
    """Command for a child that records its PID, then sleeps."""
    code = (
        "import os, signal, sys, time\n"
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "")
        + "open(os.path.join(sys.argv[1], str(os.getpid())), 'w').close()\n"
        "time.sleep(60)\n"
    )
    return [sys.executable, "-c", code, str(pid_dir)]


def _wait_for_pids(pid_dir: Path, count: int) -> list[int]:
    deadline = time.monotonic() + 30
    while len(list(pid_dir.iterdir())) < count:
        assert time.monotonic() < deadline, "children did not start"
        time.sleep(0.05)
    return [int(path.name) for path in pid_dir.iterdir()]


def _kill_all(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_sigterm_to_parent_stops_children(tmp_path):
    parent = textwrap.dedent(f"""
        from parallel import run_parallel
        command = {_sleeper(tmp_path)!r}
        run_parallel([command, command], 2)
    """)
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    # A new session lets the test kill the whole tree if an assertion fails.
    process = subprocess.Popen([sys.executable, "-c", parent], env=env, start_new_session=True)
    try:
        child_pids = _wait_for_pids(tmp_path, 2)

        process.send_signal(signal.SIGTERM)

        assert process.wait(timeout=30) == 128 + signal.SIGTERM
        assert not any(_pid_alive(pid) for pid in child_pids)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def test_failing_child_raises_and_stops_siblings(tmp_path):
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    # Fail only once the sibling is running, so the test proves it is stopped.
    failing = [
        sys.executable,
        "-c",
        "import os, sys, time\n"
        "while not os.listdir(sys.argv[1]): time.sleep(0.05)\n"
        "raise SystemExit(3)\n",
        str(pid_dir),
    ]
    try:
        with pytest.raises(subprocess.CalledProcessError) as excinfo:
            run_parallel([_sleeper(pid_dir), failing], 2)

        assert excinfo.value.returncode == 3
        assert not any(_pid_alive(pid) for pid in _wait_for_pids(pid_dir, 1))
    finally:
        _kill_all([int(path.name) for path in pid_dir.iterdir()])


def test_signal_during_spawn_still_reaps_the_new_child(tmp_path, monkeypatch):
    real_popen = subprocess.Popen
    started: list[subprocess.Popen] = []

    def popen_then_sigterm(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        started.append(process)
        os.kill(os.getpid(), signal.SIGTERM)  # lands before the child is tracked
        return process

    monkeypatch.setattr(parallel.subprocess, "Popen", popen_then_sigterm)
    try:
        with pytest.raises(SystemExit) as excinfo:
            run_parallel([_sleeper(tmp_path), _sleeper(tmp_path)], 2)

        assert excinfo.value.code == 128 + signal.SIGTERM
        assert len(started) == 1  # the second child was never started
        assert started[0].returncode is not None  # terminated and reaped
    finally:
        for process in started:
            if process.poll() is None:
                process.kill()
                process.wait()


def test_signal_during_cleanup_does_not_skip_kill(tmp_path, monkeypatch):
    monkeypatch.setattr(parallel, "TERMINATE_TIMEOUT", 0.5)
    real_stop = parallel._stop

    def stop_with_signal(processes):
        os.kill(os.getpid(), signal.SIGTERM)
        real_stop(processes)

    monkeypatch.setattr(parallel, "_stop", stop_with_signal)
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    failing = [
        sys.executable,
        "-c",
        "import os, sys, time\n"
        "while not os.listdir(sys.argv[1]): time.sleep(0.05)\n"
        "raise SystemExit(3)\n",
        str(pid_dir),
    ]
    try:
        with pytest.raises(subprocess.CalledProcessError):
            run_parallel([_sleeper(pid_dir, ignore_term=True), failing], 2)

        assert not any(_pid_alive(pid) for pid in _wait_for_pids(pid_dir, 1))
    finally:
        _kill_all([int(path.name) for path in pid_dir.iterdir()])


@pytest.mark.parametrize(
    "signum,expected",
    [(signal.SIGTERM, SystemExit), (signal.SIGINT, KeyboardInterrupt)],
)
def test_deferred_signals_reraise_on_exit_and_restore_handlers(signum, expected):
    before = {sig: signal.getsignal(sig) for sig in parallel.CANCEL_SIGNALS}
    reached_end_of_block = False

    with pytest.raises(expected):
        with deferred_signals() as outer:
            with deferred_signals() as inner:
                assert inner is outer
                os.kill(os.getpid(), signum)
                assert outer["signum"] == signum
            reached_end_of_block = True

    assert reached_end_of_block
    assert {sig: signal.getsignal(sig) for sig in parallel.CANCEL_SIGNALS} == before
