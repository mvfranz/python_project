"""Semantic analysis: name resolution, strong static type checking, and
compile-time monomorphization of `GENERIC PROCEDURE`/`TYPE <...>` templates.

Design notes
------------
* Scoping is exactly two levels deep: one module scope, and one scope per
  procedure for its parameters and locals. Nested procedures are rejected
  (see `_declare_locals`) so there is never a need for closures -- a
  procedure can only ever see its own locals/params and module-level
  globals, which keeps "where does this name come from" trivial to answer.
* Generics are monomorphized, not erased: instantiating `Stack<INTEGER>`
  binds the type parameter `T` to `INTEGER` in a scope, deep-copies the
  template body, and re-runs ordinary type checking/codegen against that
  scope. The result is a distinct LLVM function/struct per instantiation
  with no runtime type tag, dictionary, or indirection -- the same
  compile-time-only cost model as C++ templates.
* Explicit specializations (`PROCEDURE Max<INTEGER>(...)`) are baked into
  the very same `proc_instances` cache the generic path populates, keyed
  by (name, type_args), so a call site can't tell the difference between
  "a specialization existed" and "the template got instantiated" -- the
  cache lookup just finds the specialization first.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from . import ast_nodes as A
from . import types
from .errors import SemaError, SourcePos
from .symbols import (
    ConstSymbol,
    ImportedModuleSymbol,
    ProcSymbol,
    Scope,
    Symbol,
    TypeSymbol,
    VarSymbol,
)

BUILTIN_CONVERSIONS: dict[str, tuple[types.Type, types.Type]] = {
    "FLOAT": (types.INTEGER, types.REAL),
    "TRUNC": (types.REAL, types.INTEGER),
    "ORD": (types.CHAR, types.INTEGER),
    "CHR": (types.INTEGER, types.CHAR),
}

# Minimal console output, implemented directly in terms of libc's printf
# (see codegen.py); `None` means the builtin takes no arguments.
# `WriteString` takes an ARRAY OF CHAR of *any* size (or a string literal),
# not one fixed type, so it's checked separately in `_analyze_call` rather
# than fitting this dict's one-expected-type shape.
BUILTIN_VOID_PROCS: dict[str, types.Type | None] = {
    "WriteInt": types.INTEGER,
    "WriteReal": types.REAL,
    "WriteChar": types.CHAR,
    "WriteBool": types.BOOLEAN,
    "WriteLn": None,
}

RESERVED_NAMES = (
    set(BUILTIN_CONVERSIONS)
    | set(BUILTIN_VOID_PROCS)
    | {"WriteString", "malloc", "free", "printf", "main"}
)


def is_assignable(value_type: types.Type, target_type: types.Type) -> bool:
    if isinstance(value_type, types.NilType):
        return isinstance(target_type, types.PointerType)
    if isinstance(value_type, types.StringLitType):
        return (
            isinstance(target_type, types.ArrayType)
            and target_type.elem is types.CHAR
            and target_type.size >= value_type.length + 1
        )
    return value_type == target_type


def _c_div(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _c_mod(a: int, b: int) -> int:
    return a - _c_div(a, b) * b


@dataclass
class ProcInstance:
    mangled_name: str
    param_types: list[types.Type]
    param_by_ref: list[bool]
    ret_type: types.Type | None
    decl: A.ProcDecl
    scope: Scope


@dataclass
class AnalyzedProgram:
    modules_in_order: list[str]  # dependency order: imports before importers, main last
    module_scopes: dict[str, Scope]
    module_bodies: dict[str, list[A.Stmt]]
    proc_instances: list[ProcInstance]
    main_module: str


class Analyzer:
    """Analyzes one module at a time (`analyze_module`), in dependency
    order, accumulating cross-module state (`modules`, `module_bodies`,
    `codegen_queue`) across the calls. Everything specific to a single
    module's own declarations (its scope, its generic template/instance
    caches, its anonymous-type counter) is reset at the top of each
    `analyze_module` call -- generics are not (yet) importable, so there
    is no reason for two modules' template caches to ever interact; see
    `docs/language_spec.md`."""

    def __init__(self) -> None:
        self.modules: dict[str, Scope] = {}
        self.module_bodies: dict[str, list[A.Stmt]] = {}
        self.codegen_queue: list[ProcInstance] = []

        self.current_module = ""
        self.module_scope: Scope = Scope(None, "module")
        self.generic_proc_templates: dict[str, A.ProcDecl] = {}
        self.proc_specializations: dict[tuple, A.ProcDecl] = {}
        self.proc_instances: dict[tuple, ProcInstance] = {}
        self.generic_type_templates: dict[str, A.TypeDecl] = {}
        self.type_instances: dict[tuple, types.Type] = {}
        self._anon_counter = 0

    # -- top level -------------------------------------------------------

    def analyze_module(self, module: A.Module) -> None:
        self.current_module = module.name
        self.module_scope = Scope(None, "module")
        self.modules[module.name] = self.module_scope
        self.generic_proc_templates = {}
        self.proc_specializations = {}
        self.proc_instances = {}
        self.generic_type_templates = {}
        self.type_instances = {}
        self._anon_counter = 0

        for imp in module.imports:
            if imp.name == module.name:
                # Unreachable via analyze_program(): _order_modules's cycle
                # detection catches a self-import first, as a 1-node cycle.
                # Kept as a defensive check for `analyze_module` called
                # directly, outside that orchestration.
                raise SemaError(  # pragma: no cover
                    f"module '{module.name}' cannot IMPORT itself", imp.pos
                )
            if imp.name not in self.modules:
                raise SemaError(  # pragma: no cover - caught earlier by _order_modules
                    f"module '{imp.name}' must be analyzed before '{module.name}' imports it",
                    imp.pos,
                )
            self._check_name_free(imp.name, imp.pos)
            self.module_scope.declare(ImportedModuleSymbol(imp.name), imp.pos)

        for cd in module.consts:
            t, v = self.eval_const_expr(cd.value, self.module_scope)
            self._check_name_free(cd.name, cd.pos)
            self.module_scope.declare(ConstSymbol(cd.name, t, v), cd.pos)

        record_placeholders: dict[str, types.RecordType] = {}
        for td in module.types:
            self._check_name_free(td.name, td.pos)
            if td.is_generic:
                self.generic_type_templates[td.name] = td
            elif isinstance(td.type, A.RecordType):
                placeholder = types.RecordType(self._qualify(td.name))
                record_placeholders[td.name] = placeholder
                self.module_scope.declare(TypeSymbol(td.name, placeholder), td.pos)

        for td in module.types:
            if td.is_generic:
                continue
            if td.name in record_placeholders:
                placeholder = record_placeholders[td.name]
                assert isinstance(td.type, A.RecordType)
                self._resolve_record_fields(td.type, self.module_scope, placeholder)
            else:
                t = self.resolve_type_expr(
                    td.type, self.module_scope, name_hint=self._qualify(td.name)
                )
                self.module_scope.declare(TypeSymbol(td.name, t), td.pos)

        for vd in module.vars:
            t = self.resolve_type_expr(vd.type, self.module_scope)
            for n in vd.names:
                self._check_name_free(n, vd.pos)
                owning = isinstance(t, types.PointerType) and t.owning
                self.module_scope.declare(VarSymbol(n, t, False, owning, self._qualify(n)), vd.pos)

        # Pass 1: register every ordinary procedure's signature (and every
        # generic template, unanalyzed) so calls resolve regardless of the
        # order procedures appear in the file.
        for pd in module.procs:
            self._check_reserved(pd.name, pd.pos)
            if pd.nested_procs:
                raise SemaError(
                    "nested procedures are not supported in this prototype; "
                    "declare it as a separate top-level PROCEDURE instead",
                    pd.nested_procs[0].pos,
                )
            if pd.is_generic:
                self._check_name_free(pd.name, pd.pos)
                self.generic_proc_templates[pd.name] = pd
            elif not pd.is_specialization:
                self._check_name_free(pd.name, pd.pos)
                param_types, param_by_ref = self._signature_params(pd.params, self.module_scope)
                ret_type = self._resolve_ret_type(pd.ret_type, self.module_scope, pd.pos)
                self.module_scope.declare(
                    ProcSymbol(
                        pd.name, param_types, param_by_ref, ret_type, self._qualify(pd.name)
                    ),
                    pd.pos,
                )

        # Pass 1.5: bake explicit specializations in before any generic call
        # resolution happens, so specializations always win over the template.
        for pd in module.procs:
            if pd.specializes_args is None:
                continue
            template = self.generic_proc_templates.get(pd.name)
            if template is None:
                raise SemaError(
                    f"'{pd.name}<...>' specializes an undeclared generic procedure '{pd.name}'",
                    pd.pos,
                )
            resolved_args = [
                self.resolve_type_arg(a, self.module_scope) for a in pd.specializes_args
            ]
            if len(resolved_args) != len(template.type_params):
                raise SemaError(
                    f"specialization of '{pd.name}' expects {len(template.type_params)} "
                    f"template argument(s), got {len(resolved_args)}",
                    pd.pos,
                )
            key = (pd.name, tuple(resolved_args))
            if key in self.proc_specializations:
                raise SemaError(f"duplicate specialization of '{pd.name}'", pd.pos)
            self.proc_specializations[key] = pd
            mangled = self._mangle(pd.name, resolved_args)
            self._build_instance(pd, self.module_scope, mangled, cache=(self.proc_instances, key))

        # Pass 2: analyze ordinary procedure bodies. Generic templates are
        # compiled lazily, only when some call actually instantiates them.
        for pd in module.procs:
            if pd.is_generic or pd.is_specialization:
                continue
            sym = self.module_scope.lookup(pd.name)
            assert isinstance(sym, ProcSymbol)
            self._build_instance(pd, self.module_scope, sym.mangled_name)

        self._analyze_stmts(module.body, self.module_scope, ret_type=None, allow_return=False)
        self.module_bodies[module.name] = module.body

    def _qualify(self, name: str) -> str:
        return f"{self.current_module}${name}"

    def _check_name_free(self, name: str, pos: SourcePos) -> None:
        if (
            self.module_scope.lookup(name) is not None
            or name in self.generic_proc_templates
            or name in self.generic_type_templates
        ):
            raise SemaError(f"'{name}' is already declared", pos)

    def _check_reserved(self, name: str, pos: SourcePos) -> None:
        if name in RESERVED_NAMES:
            raise SemaError(f"'{name}' is reserved and cannot be redeclared", pos)

    # -- procedures --------------------------------------------------------

    def _signature_params(
        self, params: list[A.Param], type_scope: Scope
    ) -> tuple[list[types.Type], list[bool]]:
        param_types: list[types.Type] = []
        param_by_ref: list[bool] = []
        for p in params:
            t = self.resolve_type_expr(p.type, type_scope)
            if isinstance(t, types.PointerType) and t.owning:
                raise SemaError("OWN pointer types cannot be used as parameters", p.pos)
            for _ in p.names:
                param_types.append(t)
                param_by_ref.append(p.by_ref)
        return param_types, param_by_ref

    def _resolve_ret_type(
        self, ret_type_expr: A.TypeExpr | None, scope: Scope, pos: SourcePos
    ) -> types.Type | None:
        if ret_type_expr is None:
            return None
        t = self.resolve_type_expr(ret_type_expr, scope)
        if isinstance(t, types.PointerType) and t.owning:
            raise SemaError(
                "OWN pointer types cannot be used as a return type; an OWN pointer is "
                "freed before its owning procedure returns, so returning one would hand "
                "back a dangling pointer",
                pos,
            )
        return t

    def _declare_params(
        self, params: list[A.Param], type_scope: Scope, proc_scope: Scope
    ) -> tuple[list[types.Type], list[bool]]:
        param_types: list[types.Type] = []
        param_by_ref: list[bool] = []
        for p in params:
            t = self.resolve_type_expr(p.type, type_scope)
            if isinstance(t, types.PointerType) and t.owning:
                raise SemaError("OWN pointer types cannot be used as parameters", p.pos)
            for n in p.names:
                proc_scope.declare(VarSymbol(n, t, p.by_ref, owning=False, mangled_name=n), p.pos)
                param_types.append(t)
                param_by_ref.append(p.by_ref)
        return param_types, param_by_ref

    def _declare_locals(self, pd: A.ProcDecl, proc_scope: Scope) -> None:
        for cd in pd.consts:
            t, v = self.eval_const_expr(cd.value, proc_scope)
            proc_scope.declare(ConstSymbol(cd.name, t, v), cd.pos)
        for td in pd.types:
            # Qualified by module *and* procedure name: LLVM's identified
            # struct namespace is process-wide, so two different
            # procedures (in the same or different modules) each
            # declaring their own local `TYPE Point = RECORD ... END`
            # would otherwise collide.
            name_hint = f"{self.current_module}${pd.name}${td.name}"
            t = self.resolve_type_expr(td.type, proc_scope, name_hint=name_hint)
            proc_scope.declare(TypeSymbol(td.name, t), td.pos)
        for vd in pd.vars:
            t = self.resolve_type_expr(vd.type, proc_scope)
            for n in vd.names:
                owning = isinstance(t, types.PointerType) and t.owning
                proc_scope.declare(VarSymbol(n, t, False, owning, mangled_name=n), vd.pos)

    def _build_instance(
        self,
        pd: A.ProcDecl,
        type_scope: Scope,
        mangled_name: str,
        cache: tuple[dict, tuple] | None = None,
    ) -> ProcInstance:
        proc_scope = type_scope.child("proc")
        param_types, param_by_ref = self._declare_params(pd.params, type_scope, proc_scope)
        ret_type = self._resolve_ret_type(pd.ret_type, type_scope, pd.pos)
        inst = ProcInstance(mangled_name, param_types, param_by_ref, ret_type, pd, proc_scope)
        if cache is not None:
            cache_dict, cache_key = cache
            cache_dict[cache_key] = inst
        self.codegen_queue.append(inst)
        self._declare_locals(pd, proc_scope)
        self._analyze_stmts(pd.body, proc_scope, ret_type, allow_return=True)
        if ret_type is not None and not self._always_returns(pd.body):
            raise SemaError(f"'{pd.name}' must return a value on all paths", pd.pos)
        return inst

    def instantiate_generic_proc(self, name: str, type_args: list, pos: SourcePos) -> ProcInstance:
        key = (name, tuple(type_args))
        cached = self.proc_instances.get(key)
        if cached is not None:
            return cached
        template = self.generic_proc_templates.get(name)
        if template is None:
            raise SemaError(f"'{name}' is not a generic procedure", pos)
        if len(type_args) != len(template.type_params):
            raise SemaError(
                f"'{name}' expects {len(template.type_params)} template argument(s), "
                f"got {len(type_args)}",
                pos,
            )
        subst_scope = self._make_subst_scope(template.type_params, type_args, pos)
        mangled_name = self._mangle(name, type_args)
        decl_copy = copy.deepcopy(template)
        return self._build_instance(
            decl_copy, subst_scope, mangled_name, cache=(self.proc_instances, key)
        )

    def instantiate_generic_type(self, name: str, type_args: list, pos: SourcePos) -> types.Type:
        key = (name, tuple(type_args))
        cached = self.type_instances.get(key)
        if cached is not None:
            return cached
        template = self.generic_type_templates.get(name)
        if template is None:
            raise SemaError(f"'{name}' is not a generic type", pos)
        if len(type_args) != len(template.type_params):
            raise SemaError(
                f"'{name}' expects {len(template.type_params)} template argument(s), "
                f"got {len(type_args)}",
                pos,
            )
        subst_scope = self._make_subst_scope(template.type_params, type_args, pos)
        mangled_name = self._mangle(name, type_args)
        t = self.resolve_type_expr(template.type, subst_scope, name_hint=mangled_name)
        self.type_instances[key] = t
        return t

    def _make_subst_scope(
        self, type_params: list[A.TypeParam], type_args: list, pos: SourcePos
    ) -> Scope:
        subst_scope = self.module_scope.child("template-subst")
        for tp, arg in zip(type_params, type_args, strict=True):
            if tp.is_const:
                if not isinstance(arg, int):
                    raise SemaError(
                        f"template parameter '{tp.name}' requires a constant INTEGER argument", pos
                    )
                subst_scope.declare(ConstSymbol(tp.name, types.INTEGER, arg), tp.pos)
            else:
                if not isinstance(arg, types.Type):
                    raise SemaError(f"template parameter '{tp.name}' requires a type argument", pos)
                subst_scope.declare(TypeSymbol(tp.name, arg), tp.pos)
        return subst_scope

    def _mangle(self, name: str, args: list) -> str:
        qualified = self._qualify(name)
        return qualified + "".join("$" + self._mangle_value(a) for a in args)

    def _mangle_value(self, v: object) -> str:
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, int):
            return str(v) if v >= 0 else f"n{-v}"
        if v is types.INTEGER:
            return "INTEGER"
        if v is types.REAL:
            return "REAL"
        if v is types.BOOLEAN:
            return "BOOLEAN"
        if v is types.CHAR:
            return "CHAR"
        if isinstance(v, types.RecordType):
            return v.name
        if isinstance(v, types.ArrayType):
            return f"Arr{v.size}_{self._mangle_value(v.elem)}"
        if isinstance(v, types.PointerType):
            return ("Own" if v.owning else "Ptr") + self._mangle_value(v.base)
        return "T"  # pragma: no cover - unreachable with current type grammar

    # -- types ---------------------------------------------------------

    def _anon_name(self) -> str:
        self._anon_counter += 1
        return self._qualify(f"anon{self._anon_counter}")

    def _resolve_record_fields(
        self, node: A.RecordType, scope: Scope, rt: types.RecordType
    ) -> None:
        seen: set[str] = set()
        for f in node.fields:
            ft = self.resolve_type_expr(f.type, scope)
            for n in f.names:
                if n in seen:
                    raise SemaError(f"duplicate field '{n}' in RECORD", f.pos)
                seen.add(n)
                rt.add_field(n, ft)

    def resolve_type_expr(
        self, node: A.TypeExpr, scope: Scope, name_hint: str | None = None
    ) -> types.Type:
        if isinstance(node, A.NamedType):
            builtin = types.builtin_named_type(node.name)
            if builtin is not None:
                return builtin
            sym = scope.lookup(node.name)
            if sym is None:
                if node.name in self.generic_type_templates:
                    raise SemaError(
                        f"generic type '{node.name}' requires type arguments, "
                        f"e.g. '{node.name}<...>'",
                        node.pos,
                    )
                raise SemaError(f"undeclared type '{node.name}'", node.pos)
            if isinstance(sym, TypeSymbol):
                return sym.type
            raise SemaError(f"'{node.name}' is not a type", node.pos)
        if isinstance(node, A.ArrayType):
            size_t, size_v = self.eval_const_expr(node.size, scope)
            if size_t is not types.INTEGER:
                raise SemaError("array size must be an INTEGER constant expression", node.pos)
            if size_v <= 0:
                raise SemaError("array size must be a positive integer", node.pos)
            elem = self.resolve_type_expr(node.elem, scope)
            return types.ArrayType(elem, size_v)
        if isinstance(node, A.RecordType):
            rt = types.RecordType(name_hint or self._anon_name())
            self._resolve_record_fields(node, scope, rt)
            return rt
        if isinstance(node, A.PointerType):
            base = self.resolve_type_expr(node.base, scope)
            return types.PointerType(base, node.owning)
        if isinstance(node, A.GenericInstanceType):
            args = [self.resolve_type_arg(a, scope) for a in node.type_args]
            return self.instantiate_generic_type(node.name, args, node.pos)
        raise SemaError("invalid type expression", node.pos)  # pragma: no cover

    def resolve_type_arg(self, arg: A.TypeExpr | A.Expr, scope: Scope) -> types.Type | int:
        if isinstance(arg, A.IntLit):
            return arg.value
        if isinstance(arg, A.NamedType):
            builtin = types.builtin_named_type(arg.name)
            if builtin is not None:
                return builtin
            sym = scope.lookup(arg.name)
            if sym is None:
                raise SemaError(f"undeclared identifier '{arg.name}' in template argument", arg.pos)
            if isinstance(sym, ConstSymbol):
                if sym.type is not types.INTEGER:
                    raise SemaError(
                        "only INTEGER constants are supported as non-type template arguments",
                        arg.pos,
                    )
                return sym.value
            if isinstance(sym, TypeSymbol):
                return sym.type
            raise SemaError(f"'{arg.name}' is not a type or constant", arg.pos)
        # The parser only ever produces a bare `A.Expr` template argument as
        # an `A.IntLit` (handled above); anything else it parsed as a type.
        assert isinstance(arg, A.TypeExpr)
        return self.resolve_type_expr(arg, scope)

    # -- constant expressions -----------------------------------------

    def eval_const_expr(self, expr: A.Expr, scope: Scope) -> tuple[types.Type, Any]:
        if isinstance(expr, A.IntLit):
            return types.INTEGER, expr.value
        if isinstance(expr, A.RealLit):
            return types.REAL, expr.value
        if isinstance(expr, A.BoolLit):
            return types.BOOLEAN, expr.value
        if isinstance(expr, A.CharLit):
            return types.CHAR, expr.value
        if isinstance(expr, A.Designator):
            sym, parts = self._resolve_designator_base(expr.name, expr.parts, scope, expr.pos)
            if parts:
                raise SemaError("expected a constant expression", expr.pos)
            if not isinstance(sym, ConstSymbol):
                raise SemaError(f"'{expr.name}' is not a constant", expr.pos)
            return sym.type, sym.value
        if isinstance(expr, A.UnaryOp):
            t, v = self.eval_const_expr(expr.operand, scope)
            if expr.op == "-":
                if t not in (types.INTEGER, types.REAL):
                    raise SemaError("unary '-' requires an INTEGER or REAL constant", expr.pos)
                return t, -v
            if expr.op == "NOT":
                if t is not types.BOOLEAN:
                    raise SemaError("'NOT' requires a BOOLEAN constant", expr.pos)
                return t, not v
            raise SemaError("invalid constant expression", expr.pos)  # pragma: no cover
        if isinstance(expr, A.BinOp):
            lt, lv = self.eval_const_expr(expr.left, scope)
            rt, rv = self.eval_const_expr(expr.right, scope)
            result_type = self._check_binop(expr.op, lt, rt, expr.pos)
            return result_type, self._apply_const_binop(expr.op, lv, rv, expr.pos)
        raise SemaError("expected a constant expression", expr.pos)

    def _apply_const_binop(self, op: str, lv: Any, rv: Any, pos: SourcePos) -> Any:
        if op == "+":
            return lv + rv
        if op == "-":
            return lv - rv
        if op == "*":
            return lv * rv
        if op == "/":
            return lv / rv
        if op == "DIV":
            if rv == 0:
                raise SemaError("division by zero in constant expression", pos)
            return _c_div(lv, rv)
        if op == "MOD":
            if rv == 0:
                raise SemaError("division by zero in constant expression", pos)
            return _c_mod(lv, rv)
        if op in ("=",):
            return lv == rv
        if op in ("#", "<>"):
            return lv != rv
        if op == "<":
            return lv < rv
        if op == "<=":
            return lv <= rv
        if op == ">":
            return lv > rv
        if op == ">=":
            return lv >= rv
        if op == "AND":
            return bool(lv) and bool(rv)
        if op == "OR":
            return bool(lv) or bool(rv)
        raise SemaError(  # pragma: no cover
            f"unsupported operator '{op}' in constant expression", pos
        )

    # -- statements ------------------------------------------------------

    def _analyze_stmts(
        self, stmts: list[A.Stmt], scope: Scope, ret_type: types.Type | None, allow_return: bool
    ) -> None:
        for s in stmts:
            self._analyze_stmt(s, scope, ret_type, allow_return)

    def _analyze_stmt(
        self, s: A.Stmt, scope: Scope, ret_type: types.Type | None, allow_return: bool
    ) -> None:
        if isinstance(s, A.Assign):
            target_t = self._infer_designator(s.target, scope)
            value_t = self._require_expr_type(s.value, scope)
            if not is_assignable(value_t, target_t):
                raise SemaError(
                    f"cannot assign {types.type_name(value_t)} to {types.type_name(target_t)}",
                    s.pos,
                )
        elif isinstance(s, A.CallStmt):
            self.infer_expr(s.call, scope)
        elif isinstance(s, A.If):
            for branch in s.branches:
                ct = self._require_expr_type(branch.cond, scope)
                if ct is not types.BOOLEAN:
                    raise SemaError("IF condition must be BOOLEAN", branch.cond.pos)
                self._analyze_stmts(branch.body, scope, ret_type, allow_return)
            if s.else_body is not None:
                self._analyze_stmts(s.else_body, scope, ret_type, allow_return)
        elif isinstance(s, A.While):
            ct = self._require_expr_type(s.cond, scope)
            if ct is not types.BOOLEAN:
                raise SemaError("WHILE condition must be BOOLEAN", s.cond.pos)
            self._analyze_stmts(s.body, scope, ret_type, allow_return)
        elif isinstance(s, A.Repeat):
            self._analyze_stmts(s.body, scope, ret_type, allow_return)
            ct = self._require_expr_type(s.cond, scope)
            if ct is not types.BOOLEAN:
                raise SemaError("UNTIL condition must be BOOLEAN", s.cond.pos)
        elif isinstance(s, A.For):
            var_sym = scope.lookup_required(s.var, s.pos)
            if not isinstance(var_sym, VarSymbol) or var_sym.type is not types.INTEGER:
                raise SemaError(f"FOR loop variable '{s.var}' must be an INTEGER variable", s.pos)
            for bound in (s.start, s.stop, *([s.step] if s.step else [])):
                bt = self._require_expr_type(bound, scope)
                if bt is not types.INTEGER:
                    raise SemaError("FOR bounds must be INTEGER", bound.pos)
            self._analyze_stmts(s.body, scope, ret_type, allow_return)
        elif isinstance(s, A.Return):
            if not allow_return:
                raise SemaError("RETURN is not allowed here", s.pos)
            if ret_type is None:
                if s.value is not None:
                    raise SemaError("this procedure does not return a value", s.pos)
            else:
                if s.value is None:
                    raise SemaError("function must RETURN a value", s.pos)
                vt = self._require_expr_type(s.value, scope)
                if not is_assignable(vt, ret_type):
                    raise SemaError(
                        f"cannot RETURN {types.type_name(vt)} from a function returning "
                        f"{types.type_name(ret_type)}",
                        s.pos,
                    )
        elif isinstance(s, A.NewStmt):
            t = self._infer_designator(s.target, scope)
            if not isinstance(t, types.PointerType):
                raise SemaError("NEW requires a POINTER (or OWN POINTER) variable", s.pos)
        elif isinstance(s, A.DisposeStmt):
            t = self._infer_designator(s.target, scope)
            if not isinstance(t, types.PointerType):
                raise SemaError("DISPOSE requires a POINTER variable", s.pos)
            if t.owning:
                raise SemaError(
                    "cannot manually DISPOSE an OWN pointer; it is freed automatically "
                    "at scope exit",
                    s.pos,
                )
        else:
            raise SemaError("unsupported statement", s.pos)  # pragma: no cover

    def _always_returns(self, stmts: list[A.Stmt]) -> bool:
        if not stmts:
            return False
        last = stmts[-1]
        if isinstance(last, A.Return):
            return True
        if isinstance(last, A.If) and last.else_body is not None:
            branches_return = all(self._always_returns(b.body) for b in last.branches)
            return branches_return and self._always_returns(last.else_body)
        return False

    # -- expressions -----------------------------------------------------

    def _require_expr_type(self, expr: A.Expr, scope: Scope) -> types.Type:
        """Like `infer_expr`, but for the (common) case where the expression
        is used as a value -- an operand, an argument, a RETURN value, and
        so on -- where a void call (e.g. `WriteLn`) isn't a legal fit."""
        t = self.infer_expr(expr, scope)
        if t is None:
            name = expr.name if isinstance(expr, A.Call) else "expression"
            raise SemaError(f"'{name}' does not return a value and cannot be used here", expr.pos)
        return t

    def infer_expr(self, expr: A.Expr, scope: Scope) -> types.Type | None:
        if isinstance(expr, A.Designator):
            return self._infer_designator(expr, scope)
        if isinstance(expr, A.IntLit):
            expr.resolved_type = types.INTEGER
            return types.INTEGER
        if isinstance(expr, A.RealLit):
            expr.resolved_type = types.REAL
            return types.REAL
        if isinstance(expr, A.BoolLit):
            expr.resolved_type = types.BOOLEAN
            return types.BOOLEAN
        if isinstance(expr, A.CharLit):
            expr.resolved_type = types.CHAR
            return types.CHAR
        if isinstance(expr, A.StringLit):
            for ch in expr.value:
                if ord(ch) > 255:
                    raise SemaError(
                        f"string literal contains {ch!r}, which has no 8-bit CHAR "
                        "representation (code point > 255)",
                        expr.pos,
                    )
            string_t = types.StringLitType(len(expr.value))
            expr.resolved_type = string_t
            return string_t
        if isinstance(expr, A.NilLit):
            expr.resolved_type = types.NIL
            return types.NIL
        if isinstance(expr, A.BinOp):
            lt = self._require_expr_type(expr.left, scope)
            rt = self._require_expr_type(expr.right, scope)
            t = self._check_binop(expr.op, lt, rt, expr.pos)
            expr.resolved_type = t
            return t
        if isinstance(expr, A.UnaryOp):
            ot = self._require_expr_type(expr.operand, scope)
            t = self._check_unaryop(expr.op, ot, expr.pos)
            expr.resolved_type = t
            return t
        if isinstance(expr, A.Call):
            call_t = self._analyze_call(expr, scope)
            expr.resolved_type = call_t
            return call_t
        raise SemaError("unsupported expression", expr.pos)  # pragma: no cover

    def _resolve_designator_base(
        self, name: str, parts: list[A.DesignatorPart], scope: Scope, pos: SourcePos
    ) -> tuple[Symbol, list[A.DesignatorPart]]:
        """Resolves a designator's leading identifier, transparently
        stepping through one level of module qualification (`Foo.Bar`) if
        `name` turns out to be an imported module rather than an ordinary
        CONST/VAR -- the parser can't tell those apart on its own (see
        parser.py's comment on the same ambiguity for calls), so this is
        where `Foo.Bar` actually becomes "look up Bar in Foo's scope"
        instead of "field access .Bar on the value of Foo". Returns the
        symbol to actually use plus whichever designator parts remain to
        be walked normally (field/index/deref) after that resolution."""
        sym = scope.lookup_required(name, pos)
        if not isinstance(sym, ImportedModuleSymbol):
            return sym, parts
        if not parts or not isinstance(parts[0], A.FieldAccess):
            raise SemaError(
                f"module '{name}' must be qualified with '.', e.g. '{name}.Something'", pos
            )
        member = parts[0]
        member_sym = self.modules[name].symbols.get(member.name)
        if member_sym is None:
            raise SemaError(f"module '{name}' has no exported member '{member.name}'", member.pos)
        return member_sym, parts[1:]

    def _infer_designator(self, d: A.Designator, scope: Scope) -> types.Type:
        sym, parts = self._resolve_designator_base(d.name, d.parts, scope, d.pos)
        if isinstance(sym, ConstSymbol):
            if parts:
                raise SemaError("cannot use '.', '[]', or '^' on a constant", d.pos)
            d.resolved_type = sym.type
            return sym.type
        if not isinstance(sym, VarSymbol):
            raise SemaError(f"'{d.name}' is not a variable", d.pos)
        t: types.Type = sym.type
        for part in parts:
            if isinstance(part, A.FieldAccess):
                if not isinstance(t, types.RecordType):
                    raise SemaError(f"'.{part.name}' requires a RECORD value", part.pos)
                f = t.field(part.name)
                if f is None:
                    raise SemaError(f"RECORD '{t.name}' has no field '{part.name}'", part.pos)
                t = f.type
            elif isinstance(part, A.IndexAccess):
                if not isinstance(t, types.ArrayType):
                    raise SemaError("'[...]' requires an ARRAY value", part.pos)
                idx_t = self._require_expr_type(part.index, scope)
                if idx_t is not types.INTEGER:
                    raise SemaError("array index must be INTEGER", part.pos)
                t = t.elem
            elif isinstance(part, A.Deref):
                if not isinstance(t, types.PointerType):
                    raise SemaError("'^' requires a POINTER value", part.pos)
                assert t.base is not None
                t = t.base
        d.resolved_type = t
        return t

    def _check_binop(self, op: str, lt: types.Type, rt: types.Type, pos: SourcePos) -> types.Type:
        if op in ("+", "-", "*"):
            if lt not in (types.INTEGER, types.REAL) or lt != rt:
                raise SemaError(
                    f"'{op}' requires two matching INTEGER or REAL operands "
                    f"(got {types.type_name(lt)} and {types.type_name(rt)})",
                    pos,
                )
            return lt
        if op == "/":
            if lt is not types.REAL or rt is not types.REAL:
                raise SemaError("'/' requires REAL operands (use DIV for INTEGER division)", pos)
            return types.REAL
        if op in ("DIV", "MOD"):
            if lt is not types.INTEGER or rt is not types.INTEGER:
                raise SemaError(f"'{op}' requires INTEGER operands", pos)
            return types.INTEGER
        if op in ("=", "#", "<>"):
            if isinstance(lt, types.NilType) or isinstance(rt, types.NilType):
                other = rt if isinstance(lt, types.NilType) else lt
                if not isinstance(other, types.PointerType):
                    raise SemaError("NIL can only be compared to a POINTER", pos)
                return types.BOOLEAN
            if isinstance(lt, (types.RecordType, types.ArrayType)) or isinstance(
                rt, (types.RecordType, types.ArrayType)
            ):
                raise SemaError(f"'{op}' cannot compare RECORD or ARRAY values directly", pos)
            if lt != rt:
                raise SemaError(
                    f"'{op}' requires matching operand types "
                    f"(got {types.type_name(lt)} and {types.type_name(rt)})",
                    pos,
                )
            return types.BOOLEAN
        if op in ("<", "<=", ">", ">="):
            if lt not in (types.INTEGER, types.REAL, types.CHAR) or lt != rt:
                raise SemaError(f"'{op}' requires matching INTEGER, REAL, or CHAR operands", pos)
            return types.BOOLEAN
        if op in ("AND", "OR"):
            if lt is not types.BOOLEAN or rt is not types.BOOLEAN:
                raise SemaError(f"'{op}' requires BOOLEAN operands", pos)
            return types.BOOLEAN
        raise SemaError(f"unknown operator '{op}'", pos)  # pragma: no cover

    def _check_unaryop(self, op: str, ot: types.Type, pos: SourcePos) -> types.Type:
        if op == "-":
            if ot not in (types.INTEGER, types.REAL):
                raise SemaError("unary '-' requires an INTEGER or REAL operand", pos)
            return ot
        if op == "NOT":
            if ot is not types.BOOLEAN:
                raise SemaError("'NOT' requires a BOOLEAN operand", pos)
            return types.BOOLEAN
        raise SemaError(f"unknown unary operator '{op}'", pos)  # pragma: no cover

    def _check_call_args(
        self,
        call: A.Call,
        param_types: list[types.Type],
        param_by_ref: list[bool],
        scope: Scope,
    ) -> None:
        if len(call.args) != len(param_types):
            raise SemaError(
                f"'{call.name}' expects {len(param_types)} argument(s), got {len(call.args)}",
                call.pos,
            )
        for arg, pt, by_ref in zip(call.args, param_types, param_by_ref, strict=True):
            at = self._require_expr_type(arg, scope)
            if not is_assignable(at, pt):
                raise SemaError(
                    f"argument type mismatch: expected {types.type_name(pt)}, "
                    f"got {types.type_name(at)}",
                    arg.pos,
                )
            if by_ref:
                if not isinstance(arg, A.Designator):
                    raise SemaError("a VAR parameter requires a variable argument", arg.pos)
                base_sym, _ = self._resolve_designator_base(arg.name, arg.parts, scope, arg.pos)
                if not isinstance(base_sym, VarSymbol):
                    raise SemaError(
                        "a VAR parameter requires a variable argument, not a constant", arg.pos
                    )

    def _infer_type_args(
        self, template: A.ProcDecl, call: A.Call, scope: Scope
    ) -> list[types.Type | int]:
        const_param_names = {tp.name for tp in template.type_params if tp.is_const}
        if const_param_names:
            raise SemaError(
                f"template arguments for '{template.name}' cannot be inferred because it "
                "has non-type parameters; use explicit '<...>' instantiation",
                call.pos,
            )
        flat_param_types = [p.type for p in template.params for _ in p.names]
        if len(flat_param_types) != len(call.args):
            raise SemaError(
                f"'{template.name}' expects {len(flat_param_types)} argument(s), "
                f"got {len(call.args)}",
                call.pos,
            )
        type_param_names = {tp.name for tp in template.type_params}
        bindings: dict[str, types.Type] = {}
        for ptype_expr, arg in zip(flat_param_types, call.args, strict=True):
            at = self._require_expr_type(arg, scope)
            if isinstance(ptype_expr, A.NamedType) and ptype_expr.name in type_param_names:
                pname = ptype_expr.name
                if pname in bindings and bindings[pname] != at:
                    raise SemaError(
                        f"conflicting template argument deduction for '{pname}'", call.pos
                    )
                bindings[pname] = at
        missing = [tp.name for tp in template.type_params if tp.name not in bindings]
        if missing:
            raise SemaError(
                f"cannot deduce template argument(s) {missing} for '{template.name}'; "
                "use explicit '<...>' instantiation",
                call.pos,
            )
        return [bindings[tp.name] for tp in template.type_params]

    def _analyze_call(self, call: A.Call, scope: Scope) -> types.Type | None:
        if call.qualifier is not None:
            return self._analyze_qualified_call(call, scope)

        name = call.name

        if name == "WriteString":
            if call.type_args is not None:
                raise SemaError("'WriteString' is a builtin, not a template", call.pos)
            if len(call.args) != 1:
                raise SemaError("'WriteString' takes exactly one argument", call.pos)
            arg = call.args[0]
            at = self._require_expr_type(arg, scope)
            is_char_array = isinstance(at, types.ArrayType) and at.elem is types.CHAR
            is_string_lit = isinstance(at, types.StringLitType)
            if not (is_char_array or is_string_lit):
                raise SemaError(
                    f"'WriteString' expects an ARRAY OF CHAR or a string literal, "
                    f"got {types.type_name(at)}",
                    call.pos,
                )
            if is_char_array and not isinstance(arg, A.Designator):
                # Printing needs an address to read from; a string literal
                # is materialized into its own global instead (see
                # codegen.py), so only that case can be an arbitrary
                # non-addressable expression.
                raise SemaError("'WriteString' requires a string literal or a variable", call.pos)
            call.is_builtin_void_proc = "WriteString"
            return None

        if name in BUILTIN_VOID_PROCS:
            if call.type_args is not None:
                raise SemaError(f"'{name}' is a builtin, not a template", call.pos)
            expected = BUILTIN_VOID_PROCS[name]
            if expected is None:
                if call.args:
                    raise SemaError(f"'{name}' takes no arguments", call.pos)
            else:
                if len(call.args) != 1:
                    raise SemaError(f"'{name}' takes exactly one argument", call.pos)
                at = self._require_expr_type(call.args[0], scope)
                if at is not expected:
                    raise SemaError(
                        f"'{name}' expects a {types.type_name(expected)} argument, "
                        f"got {types.type_name(at)}",
                        call.pos,
                    )
            call.is_builtin_void_proc = name
            return None

        if name in BUILTIN_CONVERSIONS:
            if call.type_args is not None:
                raise SemaError(f"'{name}' is a builtin conversion, not a template", call.pos)
            if len(call.args) != 1:
                raise SemaError(f"'{name}' takes exactly one argument", call.pos)
            arg_expected, ret_t = BUILTIN_CONVERSIONS[name]
            at = self._require_expr_type(call.args[0], scope)
            if at is not arg_expected:
                raise SemaError(
                    f"'{name}' expects a {types.type_name(arg_expected)} argument, "
                    f"got {types.type_name(at)}",
                    call.pos,
                )
            call.is_builtin_conversion = name
            return ret_t

        if name in self.generic_proc_templates:
            template = self.generic_proc_templates[name]
            resolved_args: list[types.Type | int]
            if call.type_args is not None:
                resolved_args = [self.resolve_type_arg(a, scope) for a in call.type_args]
                if len(resolved_args) != len(template.type_params):
                    raise SemaError(
                        f"'{name}' expects {len(template.type_params)} template argument(s), "
                        f"got {len(resolved_args)}",
                        call.pos,
                    )
            else:
                resolved_args = self._infer_type_args(template, call, scope)
            inst = self.instantiate_generic_proc(name, resolved_args, call.pos)
            self._check_call_args(call, inst.param_types, inst.param_by_ref, scope)
            call.resolved_mangled_name = inst.mangled_name
            call.resolved_param_by_ref = inst.param_by_ref
            call.resolved_param_types = inst.param_types
            return inst.ret_type

        sym = scope.lookup(name)
        if sym is None:
            raise SemaError(f"undeclared identifier '{name}'", call.pos)
        if not isinstance(sym, ProcSymbol):
            raise SemaError(f"'{name}' is not a procedure", call.pos)
        if call.type_args is not None:
            raise SemaError(f"'{name}' is not a generic procedure", call.pos)
        self._check_call_args(call, sym.param_types, sym.param_by_ref, scope)
        call.resolved_mangled_name = sym.mangled_name
        call.resolved_param_by_ref = sym.param_by_ref
        call.resolved_param_types = sym.param_types
        return sym.ret_type

    def _analyze_qualified_call(self, call: A.Call, scope: Scope) -> types.Type | None:
        assert call.qualifier is not None
        qualifier_sym = scope.lookup(call.qualifier)
        if not isinstance(qualifier_sym, ImportedModuleSymbol):
            raise SemaError(f"'{call.qualifier}' is not an imported module", call.pos)
        member_sym = self.modules[call.qualifier].symbols.get(call.name)
        if member_sym is None:
            raise SemaError(
                f"module '{call.qualifier}' has no exported procedure '{call.name}'", call.pos
            )
        if not isinstance(member_sym, ProcSymbol):
            raise SemaError(f"'{call.qualifier}.{call.name}' is not a procedure", call.pos)
        if call.type_args is not None:
            raise SemaError(
                "generic procedures cannot (yet) be imported, so a qualified call "
                "cannot take template arguments",
                call.pos,
            )
        self._check_call_args(call, member_sym.param_types, member_sym.param_by_ref, scope)
        call.resolved_mangled_name = member_sym.mangled_name
        call.resolved_param_by_ref = member_sym.param_by_ref
        call.resolved_param_types = member_sym.param_types
        return member_sym.ret_type


def _order_modules(parsed_modules: list[A.Module]) -> list[A.Module]:
    """Topologically sorts `parsed_modules` (imports before importers, the
    main module -- always `parsed_modules[0]` -- last), validating along
    the way that every IMPORTed name was actually provided, that no two
    given modules share a name, that there's no import cycle, and that
    every provided module is actually reachable from main (an unused file
    on the command line is far more likely a typo than an intentional
    no-op)."""
    by_name: dict[str, A.Module] = {}
    for m in parsed_modules:
        if m.name in by_name:
            raise SemaError(f"module '{m.name}' is defined more than once", m.pos)
        by_name[m.name] = m

    main_name = parsed_modules[0].name
    order: list[A.Module] = []
    visited: set[str] = set()
    visiting: list[str] = []

    def visit(name: str, pos: SourcePos) -> None:
        if name in visited:
            return
        if name in visiting:
            cycle = " -> ".join([*visiting[visiting.index(name) :], name])
            raise SemaError(f"circular IMPORT: {cycle}", pos)
        m = by_name.get(name)
        if m is None:
            raise SemaError(
                f"module '{name}' is imported but its source file was not given "
                "on the command line",
                pos,
            )
        visiting.append(name)
        for imp in m.imports:
            visit(imp.name, imp.pos)
        visiting.pop()
        visited.add(name)
        order.append(m)

    visit(main_name, parsed_modules[0].pos)
    unused = set(by_name) - visited
    if unused:
        raise SemaError(
            f"module(s) {sorted(unused)} were given but are not imported "
            f"(directly or transitively) by the main module '{main_name}'",
            by_name[main_name].pos,
        )
    return order


def analyze_program(parsed_modules: list[A.Module]) -> AnalyzedProgram:
    """`parsed_modules[0]` is the program's entry point -- its body becomes
    the compiled program's actual `main`; every other module's body (if
    it has one) runs as that module's own initialization code, in
    dependency order, before anything that imports it runs."""
    ordered = _order_modules(parsed_modules)
    analyzer = Analyzer()
    for m in ordered:
        analyzer.analyze_module(m)
    return AnalyzedProgram(
        modules_in_order=[m.name for m in ordered],
        module_scopes=analyzer.modules,
        module_bodies=analyzer.module_bodies,
        proc_instances=analyzer.codegen_queue,
        main_module=ordered[-1].name,
    )


def analyze(module: A.Module) -> AnalyzedProgram:
    return analyze_program([module])
