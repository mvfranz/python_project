import pytest

from modplus import ast_nodes as A
from modplus.errors import ParseError
from modplus.parser import parse


def test_parses_minimal_module():
    mod = parse("MODULE Empty; BEGIN END Empty.")
    assert mod.name == "Empty"
    assert mod.consts == []
    assert mod.body == []


def test_mismatched_module_name_is_rejected():
    with pytest.raises(ParseError):
        parse("MODULE A; BEGIN END B.")


def test_parses_generic_procedure_and_type_params():
    mod = parse(
        """
        MODULE M;
        GENERIC PROCEDURE Max<T>(a, b: T): T;
        BEGIN
          RETURN a;
        END Max;
        BEGIN
        END M.
        """
    )
    (proc,) = mod.procs
    assert proc.is_generic
    assert [p.name for p in proc.type_params] == ["T"]
    assert not proc.type_params[0].is_const


def test_parses_non_type_template_parameter():
    mod = parse(
        """
        MODULE M;
        TYPE
          Buf<T, N: CONST INTEGER> = RECORD
            items: ARRAY[N] OF T;
          END;
        BEGIN
        END M.
        """
    )
    (td,) = mod.types
    assert [p.name for p in td.type_params] == ["T", "N"]
    assert td.type_params[1].is_const


def test_parses_explicit_specialization():
    mod = parse(
        """
        MODULE M;
        GENERIC PROCEDURE Max<T>(a, b: T): T;
        BEGIN
          RETURN a;
        END Max;
        PROCEDURE Max<INTEGER>(a, b: INTEGER): INTEGER;
        BEGIN
          RETURN a;
        END Max;
        BEGIN
        END M.
        """
    )
    generic_proc, specialization = mod.procs
    assert generic_proc.is_generic
    assert specialization.is_specialization
    assert isinstance(specialization.specializes_args[0], A.NamedType)
    assert specialization.specializes_args[0].name == "INTEGER"


def test_generic_call_vs_comparison_disambiguation():
    # `Max<INTEGER>(3, 4)` must parse as a generic call, not as a chained
    # relational expression; a bare comparison must still parse normally.
    mod = parse(
        """
        MODULE M;
        VAR x, a, b: INTEGER;
        BEGIN
          x := Max<INTEGER>(3, 4);
          x := a < b;
        END M.
        """
    )
    assign1, assign2 = mod.body
    assert isinstance(assign1.value, A.Call)
    assert assign1.value.type_args is not None
    assert isinstance(assign2.value, A.BinOp)
    assert assign2.value.op == "<"


def test_bare_identifier_statement_is_zero_arg_call():
    mod = parse("MODULE M; BEGIN WriteLn; END M.")
    (stmt,) = mod.body
    assert isinstance(stmt, A.CallStmt)
    assert stmt.call.name == "WriteLn"
    assert stmt.call.args == []


def test_designator_chain_field_index_deref():
    mod = parse(
        """
        MODULE M;
        VAR p: POINTER TO INTEGER;
        BEGIN
          p^ := 1;
        END M.
        """
    )
    (stmt,) = mod.body
    assert isinstance(stmt, A.Assign)
    assert isinstance(stmt.target.parts[0], A.Deref)


def test_nested_procedures_are_parsed_but_flagged_for_sema():
    mod = parse(
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
        """
    )
    (proc,) = mod.procs
    assert len(proc.nested_procs) == 1


def test_qualified_type_name_parses():
    mod = parse(
        """
        MODULE M;
        VAR p: Shapes.Point;
        BEGIN
        END M.
        """
    )
    (vd,) = mod.vars
    assert isinstance(vd.type, A.NamedType)
    assert vd.type.qualifier == "Shapes"
    assert vd.type.name == "Point"


def test_qualified_generic_type_instantiation_parses():
    mod = parse(
        """
        MODULE M;
        VAR b: Shapes.Box<INTEGER>;
        BEGIN
        END M.
        """
    )
    (vd,) = mod.vars
    assert isinstance(vd.type, A.GenericInstanceType)
    assert vd.type.qualifier == "Shapes"
    assert vd.type.name == "Box"


def test_string_literal_parses_as_an_expression():
    mod = parse(
        """
        MODULE M;
        VAR s: ARRAY[10] OF CHAR;
        BEGIN
          s := "hello";
        END M.
        """
    )
    (stmt,) = mod.body
    assert isinstance(stmt, A.Assign)
    assert isinstance(stmt.value, A.StringLit)
    assert stmt.value.value == "hello"
