import pytest

from modplus import types
from modplus.errors import SemaError
from modplus.parser import parse
from modplus.sema import analyze


def check(src: str):
    return analyze(parse(src))


def expect_error(src: str, match: str | None = None):
    with pytest.raises(SemaError, match=match) as exc_info:
        check(src)
    return exc_info.value


def test_strong_typing_rejects_int_real_mix():
    expect_error(
        """
        MODULE M;
        VAR i: INTEGER; r: REAL;
        BEGIN
          i := r;
        END M.
        """,
        "cannot assign",
    )


def test_float_and_trunc_conversions_are_required():
    prog = check(
        """
        MODULE M;
        VAR i: INTEGER; r: REAL;
        BEGIN
          r := FLOAT(i);
          i := TRUNC(r);
        END M.
        """
    )
    assert prog.main_module == "M"


def test_undeclared_identifier():
    expect_error(
        """
        MODULE M;
        BEGIN
          x := 1;
        END M.
        """,
        "undeclared",
    )


def test_duplicate_declaration_rejected():
    expect_error(
        """
        MODULE M;
        VAR x: INTEGER; x: REAL;
        BEGIN
        END M.
        """,
        "already declared",
    )


def test_division_operator_requires_real():
    expect_error(
        """
        MODULE M;
        VAR i: INTEGER;
        BEGIN
          i := 4 / 2;
        END M.
        """,
    )


def test_div_mod_require_integer():
    prog = check(
        """
        MODULE M;
        VAR i: INTEGER;
        BEGIN
          i := 7 DIV 2;
          i := 7 MOD 2;
        END M.
        """
    )
    assert prog is not None


def test_function_must_return_on_all_paths():
    expect_error(
        """
        MODULE M;
        PROCEDURE F(): INTEGER;
        BEGIN
          IF TRUE THEN
            RETURN 1;
          END;
        END F;
        BEGIN
        END M.
        """,
        "return a value on all paths",
    )


def test_function_returning_on_all_if_else_branches_is_ok():
    prog = check(
        """
        MODULE M;
        PROCEDURE F(): INTEGER;
        BEGIN
          IF TRUE THEN
            RETURN 1;
          ELSE
            RETURN 2;
          END;
        END F;
        BEGIN
        END M.
        """
    )
    assert prog is not None


def test_own_pointer_cannot_be_manually_disposed():
    expect_error(
        """
        MODULE M;
        TYPE Node = RECORD x: INTEGER; END;
        VAR p: OWN POINTER TO Node;
        BEGIN
          NEW(p);
          DISPOSE(p);
        END M.
        """,
        "OWN pointer",
    )


def test_own_pointer_cannot_be_a_parameter():
    expect_error(
        """
        MODULE M;
        TYPE Node = RECORD x: INTEGER; END;
        PROCEDURE F(p: OWN POINTER TO Node);
        BEGIN
        END F;
        BEGIN
        END M.
        """,
        "OWN pointer",
    )


def test_own_pointer_cannot_be_a_return_type():
    expect_error(
        """
        MODULE M;
        TYPE Node = RECORD x: INTEGER; END;
        PROCEDURE F(): OWN POINTER TO Node;
        BEGIN
        END F;
        BEGIN
        END M.
        """,
        "OWN pointer",
    )


def test_forward_reference_via_pointer_in_record():
    prog = check(
        """
        MODULE M;
        TYPE
          Node = RECORD
            value: INTEGER;
            next: POINTER TO Node;
          END;
        VAR n: Node;
        BEGIN
          n.next := NIL;
        END M.
        """
    )
    assert prog is not None


def test_generic_arity_mismatch():
    expect_error(
        """
        MODULE M;
        GENERIC PROCEDURE Id<T>(a: T): T;
        BEGIN
          RETURN a;
        END Id;
        VAR i: INTEGER;
        BEGIN
          i := Id<INTEGER, REAL>(1);
        END M.
        """,
    )


def test_generic_instantiation_produces_distinct_mangled_names():
    prog = check(
        """
        MODULE M;
        GENERIC PROCEDURE Id<T>(a: T): T;
        BEGIN
          RETURN a;
        END Id;
        VAR i: INTEGER; r: REAL;
        BEGIN
          i := Id(1);
          r := Id(2.0);
        END M.
        """
    )
    names = sorted(inst.mangled_name for inst in prog.proc_instances)
    assert names == ["M$Id$INTEGER", "M$Id$REAL"]


def test_specialization_is_used_instead_of_template():
    prog = check(
        """
        MODULE M;
        GENERIC PROCEDURE Choose<T>(a, b: T): T;
        BEGIN
          RETURN a;
        END Choose;
        PROCEDURE Choose<INTEGER>(a, b: INTEGER): INTEGER;
        BEGIN
          RETURN b;
        END Choose;
        VAR i: INTEGER;
        BEGIN
          i := Choose<INTEGER>(1, 2);
        END M.
        """
    )
    (inst,) = prog.proc_instances
    assert inst.mangled_name == "M$Choose$INTEGER"
    # the specialization's body ("RETURN b") was used, not the template's.
    ret_stmt = inst.decl.body[0]
    assert ret_stmt.value.name == "b"


def test_generic_record_with_non_type_param():
    prog = check(
        """
        MODULE M;
        TYPE
          Buf<T, N: CONST INTEGER> = RECORD
            items: ARRAY[N] OF T;
          END;
        VAR b: Buf<INTEGER, 4>;
        BEGIN
          b.items[0] := 1;
        END M.
        """
    )
    sym = prog.module_scopes[prog.main_module].lookup("b")
    assert isinstance(sym.type, types.RecordType)
    assert sym.type.fields[0].type == types.ArrayType(types.INTEGER, 4)


def test_self_referential_generic_record_type():
    prog = check(
        """
        MODULE M;
        TYPE
          Node<T> = RECORD
            value: T;
            next: POINTER TO Node<T>;
          END;
        VAR head, cur: POINTER TO Node<INTEGER>;
        BEGIN
          NEW(head);
          head^.value := 1;
          head^.next := NIL;
          NEW(cur);
          cur^.value := 2;
          cur^.next := head;
        END M.
        """
    )
    sym = prog.module_scopes[prog.main_module].lookup("head")
    assert isinstance(sym.type, types.PointerType)
    node_t = sym.type.base
    assert isinstance(node_t, types.RecordType)
    next_field = node_t.field("next")
    assert isinstance(next_field.type, types.PointerType)
    # the self-reference resolves back to the very same monomorphized
    # RecordType instance, not a distinct copy of it.
    assert next_field.type.base is node_t


def test_self_referential_generic_type_two_instantiations_are_distinct():
    prog = check(
        """
        MODULE M;
        TYPE
          Node<T> = RECORD
            value: T;
            next: POINTER TO Node<T>;
          END;
        VAR ihead: POINTER TO Node<INTEGER>;
        VAR rhead: POINTER TO Node<REAL>;
        BEGIN
          NEW(ihead);
          ihead^.value := 1;
          ihead^.next := NIL;
          NEW(rhead);
          rhead^.value := 1.5;
          rhead^.next := NIL;
        END M.
        """
    )
    isym = prog.module_scopes[prog.main_module].lookup("ihead")
    rsym = prog.module_scopes[prog.main_module].lookup("rhead")
    assert isym.type.base.name != rsym.type.base.name
    assert isym.type.base is not rsym.type.base


def test_nested_procedures_rejected():
    expect_error(
        """
        MODULE M;
        PROCEDURE Outer();
        PROCEDURE Inner();
        BEGIN
        END Inner;
        BEGIN
        END Outer;
        BEGIN
        END M.
        """,
        "nested procedures",
    )


def test_var_param_rejects_constant_argument():
    expect_error(
        """
        MODULE M;
        CONST Five = 5;
        PROCEDURE Bump(VAR x: INTEGER);
        BEGIN
          x := x + 1;
        END Bump;
        BEGIN
          Bump(Five);
        END M.
        """,
        "VAR parameter",
    )


def test_nil_can_only_compare_to_pointer():
    expect_error(
        """
        MODULE M;
        VAR i: INTEGER;
        BEGIN
          IF i = NIL THEN END;
        END M.
        """,
    )


def test_record_equality_is_rejected():
    expect_error(
        """
        MODULE M;
        TYPE P = RECORD x: INTEGER; END;
        VAR a, b: P;
        BEGIN
          IF a = b THEN END;
        END M.
        """,
        "RECORD or ARRAY",
    )


def test_string_literal_assigns_to_a_large_enough_char_array():
    prog = check(
        """
        MODULE M;
        VAR s: ARRAY[10] OF CHAR;
        BEGIN
          s := "hello";
        END M.
        """
    )
    assert prog is not None


def test_string_literal_too_long_for_target_array_rejected():
    expect_error(
        """
        MODULE M;
        VAR s: ARRAY[3] OF CHAR;
        BEGIN
          s := "hello";
        END M.
        """,
        "cannot assign",
    )


def test_string_literal_with_out_of_range_char_rejected():
    expect_error(
        'MODULE M; VAR s: ARRAY[4] OF CHAR; BEGIN s := "' + chr(300) + '"; END M.',
        "code point > 255",
    )


def test_write_string_accepts_literal_and_char_array():
    prog = check(
        """
        MODULE M;
        VAR s: ARRAY[10] OF CHAR;
        BEGIN
          s := "hi";
          WriteString(s);
          WriteString("hi");
        END M.
        """
    )
    assert prog is not None


def test_write_string_rejects_non_char_argument():
    expect_error(
        """
        MODULE M;
        VAR i: INTEGER;
        BEGIN
          WriteString(i);
        END M.
        """,
        "expects an ARRAY OF CHAR or a string literal",
    )


def test_string_literal_cannot_be_a_var_argument():
    expect_error(
        """
        MODULE M;
        PROCEDURE F(VAR s: ARRAY[10] OF CHAR);
        BEGIN
        END F;
        BEGIN
          F("hello");
        END M.
        """,
        "VAR parameter",
    )


def test_string_literal_as_ordinary_procedure_argument():
    prog = check(
        """
        MODULE M;
        PROCEDURE Greet(name: ARRAY[10] OF CHAR);
        BEGIN
          WriteString(name);
        END Greet;
        BEGIN
          Greet("Ada");
        END M.
        """
    )
    assert prog is not None


def test_write_string_rejects_reserved_name_redeclaration():
    expect_error(
        """
        MODULE M;
        PROCEDURE WriteString(x: INTEGER);
        BEGIN
        END WriteString;
        BEGIN
        END M.
        """,
        "reserved",
    )


def test_string_equality_between_char_array_variables():
    prog = check(
        """
        MODULE M;
        VAR a, b: ARRAY[10] OF CHAR; flag: BOOLEAN;
        BEGIN
          a := "hello";
          b := "hello";
          flag := a = b;
          flag := a # b;
        END M.
        """
    )
    assert prog is not None


def test_string_equality_between_variable_and_literal():
    prog = check(
        """
        MODULE M;
        VAR a: ARRAY[10] OF CHAR; flag: BOOLEAN;
        BEGIN
          a := "hello";
          flag := a = "hello";
          flag := "hello" # a;
        END M.
        """
    )
    assert prog is not None


def test_string_ordering_comparison():
    prog = check(
        """
        MODULE M;
        VAR flag: BOOLEAN;
        BEGIN
          flag := "abc" < "abd";
          flag := "abc" <= "abc";
          flag := "b" > "a";
          flag := "b" >= "b";
        END M.
        """
    )
    assert prog is not None


def test_string_comparison_against_non_char_array_rejected():
    expect_error(
        """
        MODULE M;
        VAR a: ARRAY[10] OF CHAR; i: INTEGER; flag: BOOLEAN;
        BEGIN
          flag := a = i;
        END M.
        """,
        "cannot compare",
    )


def test_string_comparison_against_record_rejected():
    expect_error(
        """
        MODULE M;
        TYPE P = RECORD x: INTEGER; END;
        VAR a: ARRAY[10] OF CHAR; p: P; flag: BOOLEAN;
        BEGIN
          flag := a = p;
        END M.
        """,
        "cannot compare",
    )


def test_char_arrays_of_different_sizes_can_be_compared():
    # Comparison follows WriteString's NUL-terminated-bytes convention,
    # not exact-array-type equality, so mismatched declared sizes are fine.
    prog = check(
        """
        MODULE M;
        VAR a: ARRAY[5] OF CHAR; b: ARRAY[20] OF CHAR; flag: BOOLEAN;
        BEGIN
          a := "hi";
          b := "hi";
          flag := a = b;
        END M.
        """
    )
    assert prog is not None


def test_strcmp_rejects_reserved_name_redeclaration():
    expect_error(
        """
        MODULE M;
        PROCEDURE strcmp(x: INTEGER);
        BEGIN
        END strcmp;
        BEGIN
        END M.
        """,
        "reserved",
    )
