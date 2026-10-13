"""Static type check: `mypy src/meridian` (configured in pyproject.toml [tool.mypy]) must report zero errors."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy is not installed")
def test_mypy_clean() -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run([sys.executable, "-m", "mypy", "src/meridian"], cwd=ROOT, env=env,  # noqa: S603
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr
