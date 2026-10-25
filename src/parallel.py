"""Run build subcommands in parallel child interpreters.

fontTools work is pure Python, so threads serialize on the GIL, and
``ProcessPoolExecutor``'s spawn start method has hung on macOS/pyenv
(issue #33). Plain ``python -m ...`` child processes avoid both problems.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")


def child_env() -> dict[str, str]:
    """Return the current environment with ``src/`` on PYTHONPATH."""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    if not existing:
        env["PYTHONPATH"] = SRC
    elif SRC not in existing.split(os.pathsep):
        env["PYTHONPATH"] = os.pathsep.join([SRC, existing])
    return env


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

    try:
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
    finally:
        for _command, _label, process in active:
            process.terminate()
        for _command, _label, process in active:
            process.wait()
