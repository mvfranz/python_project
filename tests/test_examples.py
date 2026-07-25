"""End-to-end tests: run each example program as a real `modplusc` subprocess
and check its actual console output.

Spawned as a subprocess rather than called in-process (see
`subprocess_helpers.py` for why): the compiled code prints via a direct
call to libc's `printf`, which needs a real, OS-redirected stdout to be
captured reliably across platforms.
"""

from pathlib import Path

from .subprocess_helpers import run_file

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def run_example(name: str) -> str:
    result = run_file(EXAMPLES_DIR / name)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_hello():
    out = run_example("hello.m2p")
    assert out.splitlines() == ["25", "55"]


def test_generics_max():
    out = run_example("generics_max.m2p").splitlines()
    assert out[0] == "7"
    assert out[1] == "9.750000"
    assert out[2] == "z"


def test_generic_stack():
    out = [int(line) for line in run_example("generic_stack.m2p").splitlines()]
    assert out == [25, 16, 9, 4, 1]


def test_linked_list():
    out = [int(line) for line in run_example("linked_list.m2p").splitlines()]
    assert out == [50, 40, 30, 20, 10, 150]


def test_own_pointer():
    out = [int(line) for line in run_example("own_pointer.m2p").splitlines()]
    assert out == [60]
