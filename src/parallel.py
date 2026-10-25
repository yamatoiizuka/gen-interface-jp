"""Run build subcommands in parallel child interpreters.

fontTools work is pure Python, so threads serialize on the GIL, and
``ProcessPoolExecutor``'s spawn start method has hung on macOS/pyenv
(issue #33). Plain ``python -m ...`` child processes avoid both problems.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
TERMINATE_TIMEOUT = 10.0


def child_env() -> dict[str, str]:
    """Return the current environment with ``src/`` on PYTHONPATH."""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    if not existing:
        env["PYTHONPATH"] = SRC
    elif SRC not in existing.split(os.pathsep):
        env["PYTHONPATH"] = os.pathsep.join([SRC, existing])
    return env


@contextmanager
def _sigterm_as_exit():
    """Turn SIGTERM into SystemExit so cleanup runs before the parent exits.

    Python's default SIGTERM action exits without unwinding ``finally``
    blocks, which would orphan the children and leave their temp files.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handle(signum, _frame):
        raise SystemExit(128 + signum)

    previous = signal.signal(signal.SIGTERM, handle)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _stop(processes: list[subprocess.Popen]) -> None:
    """Terminate *processes*, then kill any that outlive the grace period."""
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + TERMINATE_TIMEOUT
    for process in processes:
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run_parallel(
    commands: list[list[str]],
    jobs: int,
    labels: list[str] | None = None,
) -> None:
    """Run *commands* with at most *jobs* children; stop all on first failure."""
    labels = labels or [" ".join(command) for command in commands]
    pending = list(zip(commands, labels))
    active: list[tuple[list[str], str, subprocess.Popen]] = []
    env = child_env()

    with _sigterm_as_exit():
        try:
            _run(pending, active, jobs, env)
        finally:
            _stop([process for _command, _label, process in active])


def _run(
    pending: list[tuple[list[str], str]],
    active: list[tuple[list[str], str, subprocess.Popen]],
    jobs: int,
    env: dict[str, str],
) -> None:
    while pending or active:
        while pending and len(active) < jobs:
            command, label = pending.pop(0)
            print(f"  -> starting {label}", flush=True)
            active.append((command, label, subprocess.Popen(command, cwd=ROOT, env=env)))

        for entry in list(active):
            command, label, process = entry
            returncode = process.poll()
            if returncode is None:
                continue
            active.remove(entry)
            if returncode != 0:
                raise subprocess.CalledProcessError(returncode, command)
            print(f"  -> done {label}", flush=True)

        if active:
            time.sleep(0.2)
