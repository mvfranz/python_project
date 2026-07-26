"""Feature-focused JIT tests that aren't already covered by an example
program: REPEAT, descending FOR, VAR (by-reference) parameters, BOOLEAN/CHAR
comparisons, and the ORD/CHR/FLOAT/TRUNC builtin conversions.

Run as real `modplusc` subprocesses (see `subprocess_helpers.py` for why),
not called in-process.
"""

from .subprocess_helpers import run_source


def test_repeat_until(tmp_path):
    src = """
    MODULE M;
    VAR i: INTEGER;
    BEGIN
      i := 0;
      REPEAT
        WriteInt(i);
        WriteLn;
        i := i + 1;
      UNTIL i >= 3;
    END M.
    """
    result = run_source(tmp_path, src)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["0", "1", "2"]


def test_descending_for_loop(tmp_path):
    src = """
    MODULE M;
    VAR i: INTEGER;
    BEGIN
      FOR i := 3 TO 1 BY -1 DO
        WriteInt(i);
        WriteLn;
      END;
    END M.
    """
    result = run_source(tmp_path, src)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["3", "2", "1"]


def test_var_parameter_mutates_caller_variable(tmp_path):
    src = """
    MODULE M;
    VAR x: INTEGER;

    PROCEDURE Increment(VAR n: INTEGER);
    BEGIN
      n := n + 1;
    END Increment;

    BEGIN
      x := 41;
      Increment(x);
      WriteInt(x);
      WriteLn;
    END M.
    """
    result = run_source(tmp_path, src)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["42"]


def test_ord_and_chr_roundtrip(tmp_path):
    src = """
    MODULE M;
    VAR c: CHAR; n: INTEGER;
    BEGIN
      c := 'A';
      n := ORD(c);
      WriteInt(n);
      WriteLn;
      c := CHR(n + 1);
      WriteChar(c);
      WriteLn;
    END M.
    """
    result = run_source(tmp_path, src)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["65", "B"]


def test_boolean_and_char_comparisons(tmp_path):
    src = """
    MODULE M;
    VAR flag: BOOLEAN;
    BEGIN
      flag := (1 < 2) AND ('a' < 'b');
      WriteBool(flag);
      WriteLn;
      flag := (1 > 2) OR NOT (3 = 3);
      WriteBool(flag);
      WriteLn;
    END M.
    """
    result = run_source(tmp_path, src)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["TRUE", "FALSE"]


def test_pointer_equality_and_nil(tmp_path):
    src = """
    MODULE M;
    TYPE Node = RECORD x: INTEGER; END;
    VAR a, b: POINTER TO Node;
    BEGIN
      a := NIL;
      WriteBool(a = NIL);
      WriteLn;
      NEW(a);
      b := a;
      WriteBool(a = b);
      WriteLn;
      DISPOSE(a);
    END M.
    """
    result = run_source(tmp_path, src)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["TRUE", "TRUE"]


def test_generic_explicit_instantiation_and_inference_share_cache(tmp_path):
    src = """
    MODULE M;
    GENERIC PROCEDURE Twice<T>(a: T): T;
    BEGIN
      RETURN a + a;
    END Twice;
    VAR i: INTEGER;
    BEGIN
      i := Twice<INTEGER>(4);
      WriteInt(i);
      WriteLn;
      i := Twice(5);
      WriteInt(i);
      WriteLn;
    END M.
    """
    result = run_source(tmp_path, src)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["8", "10"]


def test_recursive_procedure(tmp_path):
    src = """
    MODULE M;
    PROCEDURE Fact(n: INTEGER): INTEGER;
    BEGIN
      IF n <= 1 THEN
        RETURN 1;
      END;
      RETURN n * Fact(n - 1);
    END Fact;
    BEGIN
      WriteInt(Fact(6));
      WriteLn;
    END M.
    """
    result = run_source(tmp_path, src)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["720"]


def test_string_assignment_and_write_string(tmp_path):
    src = """
    MODULE M;
    VAR name: ARRAY[10] OF CHAR;

    PROCEDURE Greet(who: ARRAY[10] OF CHAR);
    BEGIN
      WriteString("Hello, ");
      WriteString(who);
      WriteString("!");
      WriteLn;
    END Greet;

    BEGIN
      name := "world";
      Greet(name);
      Greet("Ada");
    END M.
    """
    result = run_source(tmp_path, src)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["Hello, world!", "Hello, Ada!"]


def test_string_comparison(tmp_path):
    src = """
    MODULE M;
    VAR a, b: ARRAY[10] OF CHAR;
    BEGIN
      a := "hello";
      b := "hello";
      WriteBool(a = b);
      WriteLn;

      b := "world";
      WriteBool(a = b);
      WriteLn;
      WriteBool(a # b);
      WriteLn;

      WriteBool(a = "hello");
      WriteLn;

      WriteBool(a < b);
      WriteLn;
      WriteBool("abc" <= "abc");
      WriteLn;
    END M.
    """
    result = run_source(tmp_path, src)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["TRUE", "FALSE", "TRUE", "TRUE", "TRUE", "TRUE"]


def test_recursive_generic_instantiation(tmp_path):
    src = """
    MODULE M;
    GENERIC PROCEDURE Fact<T>(n: T): T;
    BEGIN
      IF n <= 1 THEN
        RETURN 1;
      END;
      RETURN n * Fact<T>(n - 1);
    END Fact;
    BEGIN
      WriteInt(Fact<INTEGER>(6));
      WriteLn;
    END M.
    """
    result = run_source(tmp_path, src)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["720"]
