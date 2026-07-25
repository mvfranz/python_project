"""End-to-end tests: JIT-compile and run each example program, checking its
actual console output. Uses `capfd` (file-descriptor-level capture)
because the compiled code prints via a direct call to libc's `printf`,
bypassing Python's `sys.stdout` entirely -- `capsys` would not see it.
"""

from pathlib import Path

from modplus.jit import run

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def run_example(name: str) -> str:
    source = (EXAMPLES_DIR / name).read_text(encoding="utf-8")
    exit_code = run(source, module_name=name)
    assert exit_code == 0
    return source


def test_hello(capfd):
    run_example("hello.m2p")
    out = capfd.readouterr().out
    assert out.splitlines() == ["25", "55"]


def test_generics_max(capfd):
    run_example("generics_max.m2p")
    out = capfd.readouterr().out.splitlines()
    assert out[0] == "7"
    assert out[1] == "9.750000"
    assert out[2] == "z"


def test_generic_stack(capfd):
    run_example("generic_stack.m2p")
    out = [int(line) for line in capfd.readouterr().out.splitlines()]
    assert out == [25, 16, 9, 4, 1]


def test_linked_list(capfd):
    run_example("linked_list.m2p")
    out = [int(line) for line in capfd.readouterr().out.splitlines()]
    assert out == [50, 40, 30, 20, 10, 150]


def test_own_pointer(capfd):
    run_example("own_pointer.m2p")
    out = [int(line) for line in capfd.readouterr().out.splitlines()]
    assert out == [60]
