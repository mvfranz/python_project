"""Feature-focused JIT tests that aren't already covered by an example
program: REPEAT, descending FOR, VAR (by-reference) parameters, BOOLEAN/CHAR
comparisons, and the ORD/CHR/FLOAT/TRUNC builtin conversions.
"""

from modplus.jit import run


def test_repeat_until(capfd):
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
    assert run(src) == 0
    assert capfd.readouterr().out.splitlines() == ["0", "1", "2"]


def test_descending_for_loop(capfd):
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
    assert run(src) == 0
    assert capfd.readouterr().out.splitlines() == ["3", "2", "1"]


def test_var_parameter_mutates_caller_variable(capfd):
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
    assert run(src) == 0
    assert capfd.readouterr().out.splitlines() == ["42"]


def test_ord_and_chr_roundtrip(capfd):
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
    assert run(src) == 0
    assert capfd.readouterr().out.splitlines() == ["65", "B"]


def test_boolean_and_char_comparisons(capfd):
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
    assert run(src) == 0
    assert capfd.readouterr().out.splitlines() == ["TRUE", "FALSE"]


def test_pointer_equality_and_nil(capfd):
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
    assert run(src) == 0
    assert capfd.readouterr().out.splitlines() == ["TRUE", "TRUE"]


def test_generic_explicit_instantiation_and_inference_share_cache(capfd):
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
    assert run(src) == 0
    assert capfd.readouterr().out.splitlines() == ["8", "10"]


def test_recursive_procedure(capfd):
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
    assert run(src) == 0
    assert capfd.readouterr().out.splitlines() == ["720"]


def test_recursive_generic_instantiation(capfd):
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
    assert run(src) == 0
    assert capfd.readouterr().out.splitlines() == ["720"]
