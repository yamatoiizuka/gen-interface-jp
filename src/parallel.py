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
CANCEL_SIGNALS = (signal.SIGINT, signal.SIGTERM)

# State of the outermost active ``deferred_signals`` block, if any.
_deferred: dict[str, int | None] | None = None


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
def _signals_blocked():
    """Hold SIGINT / SIGTERM pending while handlers are swapped (POSIX).

    A blocked signal is delivered when the mask is restored, so it reaches
    either the old or the new handler set — never a half-swapped one.
    """
    if not hasattr(signal, "pthread_sigmask"):
        yield
        return
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, CANCEL_SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def _restore_handlers(previous: dict[int, object]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _cancellation(signum: int | None) -> BaseException | None:
    if signum == signal.SIGINT:
        return KeyboardInterrupt()
    if signum is not None:
        return SystemExit(128 + signum)
    return None


@contextmanager
def deferred_signals():
    """Record SIGINT / SIGTERM inside the block and re-raise them on exit.

    Python's default SIGTERM action exits without unwinding ``finally``
    blocks, and SIGINT can raise ``KeyboardInterrupt`` between spawning a
    child and tracking it. Deferring both lets callers notice cancellation at
    safe points (``state["signum"]``), finish cleanup, and only then exit:
    ``KeyboardInterrupt`` for SIGINT, ``SystemExit(128 + signum)`` otherwise.
    A recorded signal wins over an exception from the block, which is kept
    as its ``__cause__``. Nested blocks on the main thread share the
    outermost block's state. Off the main thread, signals cannot be handled
    and each block gets its own inert state.
    """
    global _deferred
    if threading.current_thread() is not threading.main_thread():
        yield {"signum": None}
        return
    if _deferred is not None:
        yield _deferred
        return

    state: dict[str, int | None] = {"signum": None}

    def record(signum, _frame):
        if state["signum"] is None:
            state["signum"] = signum

    previous: dict[int, object] = {}
    with _signals_blocked():
        try:
            for signum in CANCEL_SIGNALS:
                previous[signum] = signal.signal(signum, record)
        except BaseException:
            _restore_handlers(previous)
            raise
        _deferred = state

    error: BaseException | None = None
    try:
        yield state
    except BaseException as exc:
        error = exc
    finally:
        with _signals_blocked():
            _deferred = None
            _restore_handlers(previous)

    cancellation = _cancellation(state["signum"])
    if cancellation is not None:
        raise cancellation from error
    if error is not None:
        raise error


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
    """Run *commands* with at most *jobs* children; stop all on first failure.

    On a child failure, SIGINT, or SIGTERM every started child is terminated
    and reaped before the error or signal propagates.
    """
    labels = labels or [" ".join(command) for command in commands]
    pending = list(zip(commands, labels))
    active: list[tuple[list[str], str, subprocess.Popen]] = []
    env = child_env()

    with deferred_signals() as cancel:
        try:
            _run(pending, active, jobs, env, cancel)
        finally:
            _stop([process for _command, _label, process in active])


def _run(
    pending: list[tuple[list[str], str]],
    active: list[tuple[list[str], str, subprocess.Popen]],
    jobs: int,
    env: dict[str, str],
    cancel: dict[str, int | None],
) -> None:
    while (pending or active) and cancel["signum"] is None:
        while pending and len(active) < jobs and cancel["signum"] is None:
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

        if active and cancel["signum"] is None:
            time.sleep(0.2)
