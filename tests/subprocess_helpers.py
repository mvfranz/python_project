"""Shared helper for invoking modplusc as a real subprocess.

Capturing stdout via pytest's `capfd` (in-process fd-dup2 redirection)
turned out to be unreliable on Windows specifically for output written by
a JIT-compiled program's own libc `printf` calls: in CI, the JIT-executed
program's real output reached the raw console (visible directly in the
Actions log) and the run's exit code was correct, but `capfd` itself saw
nothing. Spawning modplusc as an actual subprocess sidesteps this
entirely -- the child process's stdout is redirected by the OS at
process-creation time, before its C runtime ever initializes, which every
platform handles the same well-understood way.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_file(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "modplus.cli", "run", str(path)],
        capture_output=True,
        text=True,
    )


def run_source(tmp_path: Path, source: str, name: str = "t.m2p") -> subprocess.CompletedProcess:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return run_file(path)
