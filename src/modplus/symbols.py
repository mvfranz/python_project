"""Symbol tables and lexical scoping.

Modplus uses simple block/module scoping, same as Modula-2: a module has one
top-level scope, each procedure (including nested procedures) opens a child
scope for its parameters and local `CONST`/`TYPE`/`VAR` sections, and name
lookup walks outward through enclosing scopes. There is no separate
namespace per declaration kind -- one flat dict per scope -- so shadowing a
name (e.g. redeclaring a global as a local) is just a normal, explicit,
innermost-wins lookup.
"""

from __future__ import annotations

from typing import Any

from .errors import SemaError, SourcePos
from .types import Type


class Symbol:
    def __init__(self, name: str) -> None:
        self.name = name


class ConstSymbol(Symbol):
    """`value` holds a plain Python int/float/bool/str -- whichever matches
    `type_` -- computed once by `sema.eval_const_expr`'s constant folder.
    Typed as `Any` rather than a Union: the folder's own type-checking
    (`_check_binop`) is what guarantees the two operands of an operation
    agree, and that dynamic guarantee isn't something a Union can express
    without a wall of casts that would just restate it."""

    def __init__(self, name: str, type_: Type, value: Any) -> None:
        super().__init__(name)
        self.type = type_
        self.value = value


class TypeSymbol(Symbol):
    def __init__(self, name: str, type_: Type) -> None:
        super().__init__(name)
        self.type = type_


class VarSymbol(Symbol):
    """Also doubles as codegen's storage slot: `llvm_ptr` is filled in by
    codegen.py once the alloca/param slot has been emitted.

    `mangled_name` is the actual LLVM symbol name for module-level globals
    (module-qualified, e.g. "Foo$x", so two modules can each declare a `x`
    without colliding) -- for a procedure's params/locals it's just `name`
    again, since those only ever become function-local allocas, which
    llvmlite disambiguates on its own and never share a process-wide
    namespace with anything else."""

    def __init__(
        self, name: str, type_: Type, by_ref: bool, owning: bool, mangled_name: str
    ) -> None:
        super().__init__(name)
        self.type = type_
        self.by_ref = by_ref
        self.owning = owning
        self.mangled_name = mangled_name
        self.llvm_ptr = None


class ImportedModuleSymbol(Symbol):
    """A marker declared into a module's scope for each name in its
    `IMPORT` list -- occupying the same flat namespace as everything else
    so `_check_name_free`/`Scope.declare` catch a local CONST/TYPE/VAR/
    PROCEDURE that collides with an imported module name for free, and so
    a qualified reference (`Foo.Bar`) used inside a nested procedure scope
    resolves by walking the normal scope chain up to the module scope."""


class ProcSymbol(Symbol):
    def __init__(
        self,
        name: str,
        param_types: list[Type],
        param_by_ref: list[bool],
        ret_type: Type | None,
        mangled_name: str,
    ) -> None:
        super().__init__(name)
        self.param_types = param_types
        self.param_by_ref = param_by_ref
        self.ret_type = ret_type
        self.mangled_name = mangled_name


class Scope:
    def __init__(self, parent: Scope | None, kind: str) -> None:
        self.parent = parent
        self.kind = kind
        self.symbols: dict[str, Symbol] = {}
        # Own (RAII) pointer locals declared directly in this scope, in
        # declaration order; codegen auto-DISPOSE()s them, in reverse order,
        # on every path leaving the scope (fallthrough, RETURN, etc).
        self.own_vars: list[VarSymbol] = []

    def declare(self, symbol: Symbol, pos: SourcePos) -> None:
        if symbol.name in self.symbols:
            raise SemaError(f"'{symbol.name}' is already declared in this scope", pos)
        self.symbols[symbol.name] = symbol
        if isinstance(symbol, VarSymbol) and symbol.owning:
            self.own_vars.append(symbol)

    def lookup(self, name: str) -> Symbol | None:
        scope: Scope | None = self
        while scope is not None:
            sym = scope.symbols.get(name)
            if sym is not None:
                return sym
            scope = scope.parent
        return None

    def lookup_required(self, name: str, pos: SourcePos) -> Symbol:
        sym = self.lookup(name)
        if sym is None:
            raise SemaError(f"undeclared identifier '{name}'", pos)
        return sym

    def child(self, kind: str) -> Scope:
        return Scope(self, kind)
