"""AST node definitions for the modplus language.

Plain dataclasses; generic declarations are monomorphized by deep-copying
the relevant subtree (see sema.py) so every node type here must stay
deepcopy-safe (no open file handles, no cyclic parent pointers, etc).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from . import types
from .errors import SourcePos

# ---------------------------------------------------------------------------
# Type expressions (syntax, not yet resolved to `types.Type`)
# ---------------------------------------------------------------------------


class TypeExpr:
    pos: SourcePos


@dataclass
class NamedType(TypeExpr):
    name: str
    pos: SourcePos
    qualifier: str | None = None


@dataclass
class ArrayType(TypeExpr):
    size: Expr
    elem: TypeExpr
    pos: SourcePos


@dataclass
class FieldDecl:
    names: list[str]
    type: TypeExpr
    pos: SourcePos


@dataclass
class RecordType(TypeExpr):
    fields: list[FieldDecl]
    pos: SourcePos


@dataclass
class PointerType(TypeExpr):
    base: TypeExpr
    owning: bool
    pos: SourcePos


@dataclass
class GenericInstanceType(TypeExpr):
    name: str
    type_args: list[TypeArg]
    pos: SourcePos
    qualifier: str | None = None


# A template/type argument is either a type expression or a constant expression
# (for non-type template parameters, e.g. `N` in `FixedArray<T, N: CONST INTEGER>`).
TypeArg = Union["TypeExpr", "Expr"]


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


class Expr:
    pos: SourcePos
    # Filled in by sema.py once the expression has been type-checked; every
    # Expr node gets one, even though only Call additionally needs the
    # generics/builtin bookkeeping fields declared on the Call class below.
    resolved_type: types.Type | None = None


@dataclass
class IntLit(Expr):
    value: int
    pos: SourcePos


@dataclass
class RealLit(Expr):
    value: float
    pos: SourcePos


@dataclass
class BoolLit(Expr):
    value: bool
    pos: SourcePos


@dataclass
class CharLit(Expr):
    value: str
    pos: SourcePos


@dataclass
class StringLit(Expr):
    value: str
    pos: SourcePos


@dataclass
class NilLit(Expr):
    pos: SourcePos


@dataclass
class DesignatorPart:
    """One step of a designator chain: `.field`, `[index]`, or `^`."""


@dataclass
class FieldAccess(DesignatorPart):
    name: str
    pos: SourcePos


@dataclass
class IndexAccess(DesignatorPart):
    index: Expr
    pos: SourcePos


@dataclass
class Deref(DesignatorPart):
    pos: SourcePos


@dataclass
class Designator(Expr):
    name: str
    parts: list[DesignatorPart]
    pos: SourcePos


@dataclass
class BinOp(Expr):
    op: str
    left: Expr
    right: Expr
    pos: SourcePos


@dataclass
class UnaryOp(Expr):
    op: str
    operand: Expr
    pos: SourcePos


@dataclass
class Call(Expr):
    name: str
    type_args: list[TypeArg] | None
    args: list[Expr]
    pos: SourcePos
    # Set only for a qualified call to an imported module's procedure,
    # e.g. `Foo.Bar(...)` -> qualifier="Foo", name="Bar".
    qualifier: str | None = None
    # The remaining fields are resolved by sema.py, once it has picked the
    # builtin/generic-instantiation/ordinary-procedure dispatch for this
    # call; codegen.py trusts them rather than re-deriving the same
    # information from scratch.
    resolved_mangled_name: str | None = field(default=None, init=False)
    resolved_param_types: list[types.Type] = field(default_factory=list, init=False)
    resolved_param_by_ref: list[bool] = field(default_factory=list, init=False)
    is_builtin_conversion: str | None = field(default=None, init=False)
    is_builtin_void_proc: str | None = field(default=None, init=False)


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


class Stmt:
    pos: SourcePos


@dataclass
class Assign(Stmt):
    target: Designator
    value: Expr
    pos: SourcePos


@dataclass
class CallStmt(Stmt):
    call: Call
    pos: SourcePos


@dataclass
class IfBranch:
    cond: Expr
    body: list[Stmt]


@dataclass
class If(Stmt):
    branches: list[IfBranch]
    else_body: list[Stmt] | None
    pos: SourcePos


@dataclass
class While(Stmt):
    cond: Expr
    body: list[Stmt]
    pos: SourcePos


@dataclass
class Repeat(Stmt):
    body: list[Stmt]
    cond: Expr
    pos: SourcePos


@dataclass
class For(Stmt):
    var: str
    start: Expr
    stop: Expr
    step: Expr | None
    body: list[Stmt]
    pos: SourcePos


@dataclass
class Return(Stmt):
    value: Expr | None
    pos: SourcePos


@dataclass
class NewStmt(Stmt):
    target: Designator
    pos: SourcePos


@dataclass
class DisposeStmt(Stmt):
    target: Designator
    pos: SourcePos


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


@dataclass
class ConstDecl:
    name: str
    value: Expr
    pos: SourcePos


@dataclass
class TypeParam:
    """A template parameter: either a type parameter (`T`) or a compile-time
    constant/non-type parameter (`N: CONST INTEGER`)."""

    name: str
    const_type: TypeExpr | None  # None => ordinary type parameter
    pos: SourcePos

    @property
    def is_const(self) -> bool:
        return self.const_type is not None


@dataclass
class TypeDecl:
    name: str
    type_params: list[TypeParam]
    type: TypeExpr
    pos: SourcePos

    @property
    def is_generic(self) -> bool:
        return len(self.type_params) > 0


@dataclass
class Param:
    names: list[str]
    type: TypeExpr
    by_ref: bool
    pos: SourcePos


@dataclass
class ProcDecl:
    name: str
    type_params: list[TypeParam]
    params: list[Param]
    ret_type: TypeExpr | None
    consts: list[ConstDecl]
    types: list[TypeDecl]
    vars: list[VarDecl]
    body: list[Stmt]
    pos: SourcePos
    # Present only for an explicit specialization: `PROCEDURE Name<INT>(...)`
    specializes_args: list[TypeArg] | None = None
    nested_procs: list[ProcDecl] = field(default_factory=list)

    @property
    def is_generic(self) -> bool:
        return len(self.type_params) > 0

    @property
    def is_specialization(self) -> bool:
        return self.specializes_args is not None


@dataclass
class VarDecl:
    names: list[str]
    type: TypeExpr
    pos: SourcePos


@dataclass
class ImportedName:
    name: str
    pos: SourcePos


@dataclass
class Module:
    name: str
    imports: list[ImportedName]
    consts: list[ConstDecl]
    types: list[TypeDecl]
    vars: list[VarDecl]
    procs: list[ProcDecl]
    body: list[Stmt]
    pos: SourcePos
