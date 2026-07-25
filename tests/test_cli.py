from pathlib import Path

from modplus.cli import main

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def test_cli_run(capfd):
    code = main(["run", str(EXAMPLES_DIR / "hello.m2p")])
    assert code == 0
    assert capfd.readouterr().out.splitlines() == ["25", "55"]


def test_cli_emit_llvm_to_stdout(capfd):
    code = main(["emit-llvm", str(EXAMPLES_DIR / "hello.m2p")])
    assert code == 0
    out = capfd.readouterr().out
    assert 'define i32 @"main"' in out


def test_cli_emit_llvm_to_file(tmp_path):
    out_file = tmp_path / "hello.ll"
    code = main(["emit-llvm", str(EXAMPLES_DIR / "hello.m2p"), "-o", str(out_file)])
    assert code == 0
    assert 'define i32 @"main"' in out_file.read_text()


def test_cli_emit_object(tmp_path):
    out_file = tmp_path / "hello.o"
    code = main(["emit-object", str(EXAMPLES_DIR / "hello.m2p"), "-o", str(out_file)])
    assert code == 0
    assert out_file.exists()
    assert out_file.stat().st_size > 0


def test_cli_reports_compile_errors_cleanly(tmp_path, capfd):
    bad = tmp_path / "bad.m2p"
    bad.write_text("MODULE Bad; VAR i: INTEGER; r: REAL; BEGIN i := r; END Bad.")
    code = main(["run", str(bad)])
    assert code == 1
    err = capfd.readouterr().err
    assert "modplusc:" in err
    assert "cannot assign" in err
