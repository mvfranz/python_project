"""Unit tests for IMPORT / multi-module compilation: the import graph
(cycles, unknown/unused modules), qualified access (`Foo.Bar`) to another
module's CONST/VAR/PROCEDURE, and the namespace rules around it.

End-to-end JIT execution of a real two-module program lives in
test_examples.py::test_import_demo.
"""

import pytest

from modplus import types
from modplus.errors import SemaError
from modplus.parser import parse
from modplus.sema import analyze_program


def check(*srcs: str):
    return analyze_program([parse(s) for s in srcs])


def expect_error(srcs: list[str], match: str | None = None):
    with pytest.raises(SemaError, match=match) as exc_info:
        check(*srcs)
    return exc_info.value


MATH_UTILS = """
MODULE MathUtils;
CONST Limit = 100;
VAR CallCount: INTEGER;
PROCEDURE Square(x: INTEGER): INTEGER;
BEGIN
  CallCount := CallCount + 1;
  RETURN x * x;
END Square;
BEGIN
  CallCount := 0;
END MathUtils.
"""


def test_qualified_const_var_and_proc_access():
    main = """
    MODULE Main;
    IMPORT MathUtils;
    VAR total: INTEGER;
    BEGIN
      total := MathUtils.Square(3) + MathUtils.Limit;
      MathUtils.CallCount := MathUtils.CallCount + 1;
    END Main.
    """
    prog = check(main, MATH_UTILS)
    assert prog.modules_in_order == ["MathUtils", "Main"]
    assert prog.main_module == "Main"


def test_qualified_access_requires_import():
    # Even though MathUtils is part of the same compilation (transitively,
    # via Main), a module that didn't IMPORT it itself can't qualify-access it.
    other = """
    MODULE Other;
    VAR x: INTEGER;
    BEGIN
      x := MathUtils.Limit;
    END Other.
    """
    main = """
    MODULE Main;
    IMPORT MathUtils, Other;
    BEGIN
    END Main.
    """
    expect_error([main, MATH_UTILS, other], "undeclared identifier 'MathUtils'")


def test_unknown_imported_module():
    main = """
    MODULE Main;
    IMPORT DoesNotExist;
    BEGIN
    END Main.
    """
    expect_error([main], "was not given on the command line")


def test_import_cycle_detected():
    a = "MODULE A; IMPORT B; BEGIN END A."
    b = "MODULE B; IMPORT A; BEGIN END B."
    expect_error([a, b], "circular IMPORT")


def test_self_import_rejected():
    # Caught by the cycle detector, as a 1-node cycle.
    a = "MODULE A; IMPORT A; BEGIN END A."
    expect_error([a], "circular IMPORT")


def test_unused_module_rejected():
    a = "MODULE A; BEGIN END A."
    b = "MODULE B; BEGIN END B."
    expect_error([a, b], "not imported")


def test_duplicate_module_name_rejected():
    a1 = "MODULE A; BEGIN END A."
    a2 = "MODULE A; BEGIN END A."
    expect_error([a1, a2], "defined more than once")


def test_local_declaration_colliding_with_import_rejected():
    main = """
    MODULE Main;
    IMPORT MathUtils;
    VAR MathUtils: INTEGER;
    BEGIN
    END Main.
    """
    expect_error([main, MATH_UTILS], "already declared")


def test_qualified_call_to_a_generic_procedure_is_not_exported():
    # GENERIC PROCEDUREs are never declared into a module's scope (only
    # instantiations/specializations are, under their own mangled names),
    # so they're invisible to qualified access entirely -- which is
    # exactly "generics aren't importable yet", just surfaced as a plain
    # "no such export" rather than a template-specific complaint.
    generic_mod = """
    MODULE Generics;
    GENERIC PROCEDURE Id<T>(a: T): T;
    BEGIN
      RETURN a;
    END Id;
    BEGIN
    END Generics.
    """
    main = """
    MODULE Main;
    IMPORT Generics;
    VAR i: INTEGER;
    BEGIN
      i := Generics.Id<INTEGER>(1);
    END Main.
    """
    expect_error([main, generic_mod], "no exported procedure 'Id'")


def test_qualified_call_with_template_args_to_an_ordinary_procedure():
    # Here the target *is* exported (an ordinary, non-generic PROCEDURE),
    # so this hits the template-arguments-specific rejection instead.
    main = """
    MODULE Main;
    IMPORT MathUtils;
    VAR i: INTEGER;
    BEGIN
      i := MathUtils.Square<INTEGER>(3);
    END Main.
    """
    expect_error([main, MATH_UTILS], "cannot take template arguments")


def test_var_by_ref_qualified_argument():
    main = """
    MODULE Main;
    IMPORT MathUtils;
    PROCEDURE Bump(VAR n: INTEGER);
    BEGIN
      n := n + 1;
    END Bump;
    BEGIN
      Bump(MathUtils.CallCount);
    END Main.
    """
    prog = check(main, MATH_UTILS)
    assert prog.main_module == "Main"


SHAPES = """
MODULE Shapes;
TYPE Point = RECORD x, y: INTEGER; END;
BEGIN
END Shapes.
"""


def test_qualified_type_reference_for_var_declaration():
    main = """
    MODULE Main;
    IMPORT Shapes;
    VAR p: Shapes.Point;
    BEGIN
      p.x := 1;
      p.y := 2;
    END Main.
    """
    prog = check(main, SHAPES)
    sym = prog.module_scopes["Main"].lookup("p")
    assert isinstance(sym.type, types.RecordType)


def test_qualified_type_reference_as_procedure_parameter():
    main = """
    MODULE Main;
    IMPORT Shapes;
    PROCEDURE Distance2(a: Shapes.Point): INTEGER;
    BEGIN
      RETURN a.x * a.x + a.y * a.y;
    END Distance2;
    VAR p: Shapes.Point; d: INTEGER;
    BEGIN
      p.x := 3;
      p.y := 4;
      d := Distance2(p);
    END Main.
    """
    prog = check(main, SHAPES)
    assert prog.main_module == "Main"


def test_qualified_type_reference_as_generic_template_argument():
    main = """
    MODULE Main;
    IMPORT Shapes;
    TYPE Box<T> = RECORD v: T; END;
    VAR b: Box<Shapes.Point>;
    BEGIN
      b.v.x := 5;
    END Main.
    """
    prog = check(main, SHAPES)
    sym = prog.module_scopes["Main"].lookup("b")
    assert isinstance(sym.type, types.RecordType)
    assert isinstance(sym.type.fields[0].type, types.RecordType)


def test_qualified_type_reference_requires_import():
    other = """
    MODULE Other;
    VAR p: Shapes.Point;
    BEGIN
    END Other.
    """
    main = """
    MODULE Main;
    IMPORT Shapes, Other;
    BEGIN
    END Main.
    """
    expect_error([main, SHAPES, other], "'Shapes' is not an imported module")


def test_qualified_type_reference_to_unexported_name_rejected():
    main = """
    MODULE Main;
    IMPORT Shapes;
    VAR p: Shapes.NoSuchType;
    BEGIN
    END Main.
    """
    expect_error([main, SHAPES], "has no exported member 'NoSuchType'")


def test_qualified_reference_to_non_type_member_rejected():
    main = """
    MODULE Main;
    IMPORT MathUtils;
    VAR p: MathUtils.Square;
    BEGIN
    END Main.
    """
    expect_error([main, MATH_UTILS], "'MathUtils.Square' is not a type")


def test_qualified_generic_type_instantiation_rejected():
    generic_shapes = """
    MODULE GenericShapes;
    TYPE Box<T> = RECORD v: T; END;
    BEGIN
    END GenericShapes.
    """
    main = """
    MODULE Main;
    IMPORT GenericShapes;
    VAR b: GenericShapes.Box<INTEGER>;
    BEGIN
    END Main.
    """
    expect_error([main, generic_shapes], "generic types cannot")
