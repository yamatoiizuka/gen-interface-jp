"""Shared project metadata readers."""

from __future__ import annotations

import re
from pathlib import Path


def project_version(root: str | Path) -> str:
    """Read the canonical project version from pyproject.toml."""
    pyproject = Path(root) / "pyproject.toml"
    match = re.search(
        r'^version\s*=\s*"([^"]+)"',
        pyproject.read_text(encoding="utf-8"),
        re.M,
    )
    if not match:
        raise ValueError(f"Could not read project version from {pyproject}")
    return match.group(1)
