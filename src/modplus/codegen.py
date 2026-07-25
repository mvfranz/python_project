"""LLVM IR code generation via llvmlite.

Value types (scalars, RECORD, ARRAY) are represented as first-class LLVM
values/allocas and copied with plain load/store -- never boxed, never
behind a vtable -- so a generic instantiation compiles to exactly the code
a hand-written monomorphic version would: this is what makes templates a
zero-cost (compile-time only) abstraction and keeps records/arrays laid
out contiguously (cache-friendly).

OWN pointers are freed with a handful of explicit `free()` calls inserted
at every point control leaves their declaring scope (each RETURN, and
falling off the end of the procedure/module body) -- no refcounting, no
GC, so the cost model stays "you can see every instruction that runs."
"""

from __future__ import annotations

from typing import Any

import llvmlite.ir as ir

from . import ast_nodes as A
from . import types
from .errors import CodegenError
from .sema import AnalyzedProgram, ProcInstance
from .symbols import ConstSymbol, Scope, VarSymbol

I64 = ir.IntType(64)
I32 = ir.IntType(32)
I8 = ir.IntType(8)
I1 = ir.IntType(1)
F64 = ir.DoubleType()
I8P = I8.as_pointer()

_CMP_PRED = {"=": "==", "#": "!=", "<>": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">="}


class Codegen:
    def __init__(self, module_name: str = "modplus_module") -> None:
        # A private Context, not llvmlite's shared global one: identified
        # struct types are keyed by name *within a Context*, so two
        # independent compilations that happen to both declare a `Node`
        # RECORD would otherwise collide with each other.
        self.context = ir.Context()
        self.module = ir.Module(name=module_name, context=self.context)
        self._struct_cache: dict[int, ir.IdentifiedStructType] = {}
        self.functions: dict[str, ir.Function] = {}
        self.builder: ir.IRBuilder | None = None
        self.func: ir.Function | None = None

        self.malloc_fn = ir.Function(self.module, ir.FunctionType(I8P, [I64]), "malloc")
        self.free_fn = ir.Function(self.module, ir.FunctionType(ir.VoidType(), [I8P]), "free")
        self.printf_fn = ir.Function(
            self.module, ir.FunctionType(I32, [I8P], var_arg=True), "printf"
        )
        self._string_cache: dict[str, ir.GlobalVariable] = {}
        self._string_counter = 0

    # -- entry point -----------------------------------------------------

    def generate(self, program: AnalyzedProgram) -> ir.Module:
        for sym in program.module_scope.symbols.values():
            if isinstance(sym, VarSymbol):
                gv = ir.GlobalVariable(self.module, self.llvm_type(sym.type), sym.name)
                gv.linkage = "internal"
                gv.initializer = ir.Constant(self.llvm_type(sym.type), None)
                sym.llvm_ptr = gv

        for inst in program.proc_instances:
            fnty = self._function_type(inst)
            fn = ir.Function(self.module, fnty, inst.mangled_name)
            self.functions[inst.mangled_name] = fn

        for inst in program.proc_instances:
            self._gen_proc_body(inst)

        self._gen_main(program)
        return self.module

    def _function_type(self, inst: ProcInstance) -> ir.FunctionType:
        param_llvm_types = [
            ir.PointerType(self.llvm_type(pt)) if by_ref else self.llvm_type(pt)
            for pt, by_ref in zip(inst.param_types, inst.param_by_ref, strict=True)
        ]
        ret_llvm = self.llvm_type(inst.ret_type) if inst.ret_type is not None else ir.VoidType()
        return ir.FunctionType(ret_llvm, param_llvm_types)

    # -- types -------------------------------------------------------------

    def llvm_type(self, t: types.Type) -> ir.Type:
        if t is types.INTEGER:
            return I64
        if t is types.REAL:
            return F64
        if t is types.BOOLEAN:
            return I1
        if t is types.CHAR:
            return I8
        if isinstance(t, types.ArrayType):
            return ir.ArrayType(self.llvm_type(t.elem), t.size)
        if isinstance(t, types.RecordType):
            cached = self._struct_cache.get(id(t))
            if cached is not None:
                return cached
            st = self.module.context.get_identified_type(t.name)
            self._struct_cache[id(t)] = st
            st.set_body(*[self.llvm_type(f.type) for f in t.fields])
            return st
        if isinstance(t, types.PointerType):
            assert t.base is not None
            return ir.PointerType(self.llvm_type(t.base))
        raise CodegenError(f"cannot map type {t!r} to LLVM")  # pragma: no cover

    def sizeof(self, llvm_ty: ir.Type) -> ir.Value:
        assert self.builder is not None
        null = ir.Constant(ir.PointerType(llvm_ty), None)
        one = ir.Constant(I32, 1)
        gep = self.builder.gep(null, [one], inbounds=True)
        return self.builder.ptrtoint(gep, I64)

    # -- procedures --------------------------------------------------------

    def _gen_proc_body(self, inst: ProcInstance) -> None:
        fn = self.functions[inst.mangled_name]
        self.func = fn
        block = fn.append_basic_block("entry")
        self.builder = ir.IRBuilder(block)

        scope = inst.scope
        param_names = [n for p in inst.decl.params for n in p.names]
        for arg, name, by_ref in zip(fn.args, param_names, inst.param_by_ref, strict=True):
            sym = scope.symbols[name]
            assert isinstance(sym, VarSymbol)
            if by_ref:
                sym.llvm_ptr = arg
            else:
                slot = self.builder.alloca(arg.type, name=name)
                self.builder.store(arg, slot)
                sym.llvm_ptr = slot

        self._declare_locals(inst.decl, scope)
        self.gen_stmts(inst.decl.body, scope, inst.ret_type)

        if self.builder.block.is_terminated:
            return
        self.emit_own_cleanup(scope)
        if inst.ret_type is None:
            self.builder.ret_void()
        else:
            # Unreachable: sema's `_always_returns` check guarantees every
            # path through a function ends in RETURN.
            self.builder.unreachable()

    def _declare_locals(self, decl: A.ProcDecl, scope: Scope) -> None:
        assert self.builder is not None
        for vd in decl.vars:
            for n in vd.names:
                sym = scope.symbols[n]
                assert isinstance(sym, VarSymbol)
                sym.llvm_ptr = self.builder.alloca(self.llvm_type(sym.type), name=n)

    def _gen_main(self, program: AnalyzedProgram) -> None:
        fnty = ir.FunctionType(I32, [])
        fn = ir.Function(self.module, fnty, "main")
        self.func = fn
        block = fn.append_basic_block("entry")
        self.builder = ir.IRBuilder(block)
        self.gen_stmts(program.module.body, program.module_scope, None)
        if not self.builder.block.is_terminated:
            self.emit_own_cleanup(program.module_scope)
            self.builder.ret(ir.Constant(I32, 0))

    def emit_own_cleanup(self, scope: Scope) -> None:
        assert self.builder is not None
        for sym in reversed(scope.own_vars):
            ptr_val = self.builder.load(sym.llvm_ptr)
            i8p = self.builder.bitcast(ptr_val, I8P)
            self.builder.call(self.free_fn, [i8p])

    # -- statements ------------------------------------------------------

    def gen_stmts(self, stmts: list[A.Stmt], scope: Scope, ret_type: types.Type | None) -> None:
        assert self.builder is not None
        for s in stmts:
            if self.builder.block.is_terminated:
                break
            self.gen_stmt(s, scope, ret_type)

    def gen_stmt(self, s: A.Stmt, scope: Scope, ret_type: types.Type | None) -> None:
        assert self.builder is not None
        if isinstance(s, A.Assign):
            addr, t = self.gen_designator_address(s.target, scope)
            val = self.gen_expr_as(s.value, scope, t)
            self.builder.store(val, addr)
        elif isinstance(s, A.CallStmt):
            self.gen_call(s.call, scope)
        elif isinstance(s, A.If):
            self._gen_if(s, scope, ret_type)
        elif isinstance(s, A.While):
            self._gen_while(s, scope, ret_type)
        elif isinstance(s, A.Repeat):
            self._gen_repeat(s, scope, ret_type)
        elif isinstance(s, A.For):
            self._gen_for(s, scope, ret_type)
        elif isinstance(s, A.Return):
            # Evaluate the return value *before* freeing OWN pointers: the
            # value may read from (or even be) memory an OWN pointer owns.
            if ret_type is None:
                self.emit_own_cleanup(scope)
                self.builder.ret_void()
            else:
                assert s.value is not None
                val = self.gen_expr_as(s.value, scope, ret_type)
                self.emit_own_cleanup(scope)
                self.builder.ret(val)
        elif isinstance(s, A.NewStmt):
            addr, t = self.gen_designator_address(s.target, scope)
            assert isinstance(t, types.PointerType) and t.base is not None
            base_llvm = self.llvm_type(t.base)
            size = self.sizeof(base_llvm)
            raw = self.builder.call(self.malloc_fn, [size])
            typed = self.builder.bitcast(raw, ir.PointerType(base_llvm))
            self.builder.store(typed, addr)
        elif isinstance(s, A.DisposeStmt):
            addr, _t = self.gen_designator_address(s.target, scope)
            ptr_val = self.builder.load(addr)
            i8p = self.builder.bitcast(ptr_val, I8P)
            self.builder.call(self.free_fn, [i8p])
        else:
            raise CodegenError(f"unsupported statement {s!r}")  # pragma: no cover

    def _gen_if(self, s: A.If, scope: Scope, ret_type: types.Type | None) -> None:
        assert self.func is not None and self.builder is not None
        end_bb = self.func.append_basic_block("if.end")
        for i, branch in enumerate(s.branches):
            cond_val = self.gen_expr(branch.cond, scope)
            then_bb = self.func.append_basic_block(f"if.then{i}")
            else_bb = self.func.append_basic_block(f"if.else{i}")
            self.builder.cbranch(cond_val, then_bb, else_bb)
            self.builder.position_at_end(then_bb)
            self.gen_stmts(branch.body, scope, ret_type)
            if not self.builder.block.is_terminated:
                self.builder.branch(end_bb)
            self.builder.position_at_end(else_bb)
        if s.else_body is not None:
            self.gen_stmts(s.else_body, scope, ret_type)
        if not self.builder.block.is_terminated:
            self.builder.branch(end_bb)
        self.builder.position_at_end(end_bb)

    def _gen_while(self, s: A.While, scope: Scope, ret_type: types.Type | None) -> None:
        assert self.func is not None and self.builder is not None
        cond_bb = self.func.append_basic_block("while.cond")
        body_bb = self.func.append_basic_block("while.body")
        end_bb = self.func.append_basic_block("while.end")
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cond_val = self.gen_expr(s.cond, scope)
        self.builder.cbranch(cond_val, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        self.gen_stmts(s.body, scope, ret_type)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)

    def _gen_repeat(self, s: A.Repeat, scope: Scope, ret_type: types.Type | None) -> None:
        assert self.func is not None and self.builder is not None
        body_bb = self.func.append_basic_block("repeat.body")
        end_bb = self.func.append_basic_block("repeat.end")
        self.builder.branch(body_bb)
        self.builder.position_at_end(body_bb)
        self.gen_stmts(s.body, scope, ret_type)
        if not self.builder.block.is_terminated:
            cond_val = self.gen_expr(s.cond, scope)
            self.builder.cbranch(cond_val, end_bb, body_bb)
        self.builder.position_at_end(end_bb)

    def _gen_for(self, s: A.For, scope: Scope, ret_type: types.Type | None) -> None:
        assert self.func is not None and self.builder is not None
        var_sym = scope.lookup(s.var)
        assert isinstance(var_sym, VarSymbol)
        var_ptr = var_sym.llvm_ptr

        start_val = self.gen_expr(s.start, scope)
        self.builder.store(start_val, var_ptr)
        stop_val = self.gen_expr(s.stop, scope)
        step_val = self.gen_expr(s.step, scope) if s.step is not None else ir.Constant(I64, 1)

        cond_bb = self.func.append_basic_block("for.cond")
        body_bb = self.func.append_basic_block("for.body")
        end_bb = self.func.append_basic_block("for.end")
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(var_ptr)
        zero = ir.Constant(I64, 0)
        step_nonneg = self.builder.icmp_signed(">=", step_val, zero)
        le_stop = self.builder.icmp_signed("<=", cur, stop_val)
        ge_stop = self.builder.icmp_signed(">=", cur, stop_val)
        cond = self.builder.select(step_nonneg, le_stop, ge_stop)
        self.builder.cbranch(cond, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        self.gen_stmts(s.body, scope, ret_type)
        if not self.builder.block.is_terminated:
            cur2 = self.builder.load(var_ptr)
            nxt = self.builder.add(cur2, step_val)
            self.builder.store(nxt, var_ptr)
            self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)

    # -- designators (addresses) ------------------------------------------

    def gen_designator_address(self, d: A.Designator, scope: Scope) -> tuple[ir.Value, types.Type]:
        assert self.builder is not None
        sym = scope.lookup(d.name)
        assert isinstance(sym, VarSymbol), f"'{d.name}' is not addressable"
        addr = sym.llvm_ptr
        t = sym.type
        for part in d.parts:
            if isinstance(part, A.FieldAccess):
                assert isinstance(t, types.RecordType)
                f = t.field(part.name)
                assert f is not None
                addr = self.builder.gep(
                    addr, [ir.Constant(I32, 0), ir.Constant(I32, f.index)], inbounds=True
                )
                t = f.type
            elif isinstance(part, A.IndexAccess):
                assert isinstance(t, types.ArrayType)
                idx_val = self.gen_expr(part.index, scope)
                addr = self.builder.gep(addr, [ir.Constant(I32, 0), idx_val], inbounds=True)
                t = t.elem
            elif isinstance(part, A.Deref):
                assert isinstance(t, types.PointerType)
                addr = self.builder.load(addr)
                assert t.base is not None
                t = t.base
            else:  # pragma: no cover
                raise CodegenError(f"unsupported designator part {part!r}")
        return addr, t

    # -- expressions ---------------------------------------------------

    def gen_expr_as(self, expr: A.Expr, scope: Scope, expected_type: types.Type) -> ir.Value:
        if isinstance(expr, A.NilLit):
            return ir.Constant(self.llvm_type(expected_type), None)
        return self.gen_expr(expr, scope)

    def gen_expr(self, expr: A.Expr, scope: Scope) -> ir.Value:
        assert self.builder is not None
        if isinstance(expr, A.Designator):
            base_sym = scope.lookup(expr.name)
            if isinstance(base_sym, ConstSymbol) and not expr.parts:
                return self._gen_const_value(base_sym.type, base_sym.value)
            addr, _t = self.gen_designator_address(expr, scope)
            return self.builder.load(addr)
        if isinstance(expr, A.IntLit):
            return ir.Constant(I64, expr.value)
        if isinstance(expr, A.RealLit):
            return ir.Constant(F64, expr.value)
        if isinstance(expr, A.BoolLit):
            return ir.Constant(I1, int(expr.value))
        if isinstance(expr, A.CharLit):
            return ir.Constant(I8, ord(expr.value))
        if isinstance(expr, A.NilLit):
            raise CodegenError("NIL used outside of a known-pointer-type context")
        if isinstance(expr, A.BinOp):
            return self._gen_binop(expr, scope)
        if isinstance(expr, A.UnaryOp):
            return self._gen_unaryop(expr, scope)
        if isinstance(expr, A.Call):
            return self.gen_call(expr, scope)
        raise CodegenError(f"unsupported expression {expr!r}")  # pragma: no cover

    def _gen_const_value(self, t: types.Type, v: Any) -> ir.Value:
        if t is types.INTEGER:
            return ir.Constant(I64, v)
        if t is types.REAL:
            return ir.Constant(F64, v)
        if t is types.BOOLEAN:
            return ir.Constant(I1, int(v))
        if t is types.CHAR:
            return ir.Constant(I8, ord(v))
        raise CodegenError(f"unsupported constant type {t!r}")  # pragma: no cover

    def _gen_binop(self, expr: A.BinOp, scope: Scope) -> ir.Value:
        op = expr.op
        lt: types.Type
        if isinstance(expr.left, A.NilLit):
            right_t = expr.right.resolved_type
            assert right_t is not None
            lt = right_t
            lv = ir.Constant(self.llvm_type(lt), None)
            rv = self.gen_expr(expr.right, scope)
        elif isinstance(expr.right, A.NilLit):
            left_t = expr.left.resolved_type
            assert left_t is not None
            lt = left_t
            lv = self.gen_expr(expr.left, scope)
            rv = ir.Constant(self.llvm_type(lt), None)
        else:
            left_t = expr.left.resolved_type
            assert left_t is not None
            lt = left_t
            lv = self.gen_expr(expr.left, scope)
            rv = self.gen_expr(expr.right, scope)
        return self._emit_binop(op, lt, lv, rv)

    def _emit_binop(self, op: str, lt: types.Type, lv: ir.Value, rv: ir.Value) -> ir.Value:
        b = self.builder
        assert b is not None
        if isinstance(lt, types.PointerType):
            if op == "=":
                return b.icmp_unsigned("==", lv, rv)
            if op in ("#", "<>"):
                return b.icmp_unsigned("!=", lv, rv)
            raise CodegenError(f"unsupported pointer operator '{op}'")  # pragma: no cover
        if lt is types.REAL:
            if op == "+":
                return b.fadd(lv, rv)
            if op == "-":
                return b.fsub(lv, rv)
            if op == "*":
                return b.fmul(lv, rv)
            if op == "/":
                return b.fdiv(lv, rv)
            return b.fcmp_ordered(_CMP_PRED[op], lv, rv)
        if lt is types.INTEGER:
            if op == "+":
                return b.add(lv, rv)
            if op == "-":
                return b.sub(lv, rv)
            if op == "*":
                return b.mul(lv, rv)
            if op == "DIV":
                return b.sdiv(lv, rv)
            if op == "MOD":
                return b.srem(lv, rv)
            return b.icmp_signed(_CMP_PRED[op], lv, rv)
        if lt is types.CHAR:
            return b.icmp_unsigned(_CMP_PRED[op], lv, rv)
        if lt is types.BOOLEAN:
            if op == "AND":
                return b.and_(lv, rv)
            if op == "OR":
                return b.or_(lv, rv)
            pred = {"=": "==", "#": "!=", "<>": "!="}[op]
            return b.icmp_unsigned(pred, lv, rv)
        raise CodegenError(f"unsupported operand type {lt!r} for '{op}'")  # pragma: no cover

    def _gen_unaryop(self, expr: A.UnaryOp, scope: Scope) -> ir.Value:
        assert self.builder is not None
        ot = expr.operand.resolved_type
        ov = self.gen_expr(expr.operand, scope)
        if expr.op == "-":
            if ot is types.REAL:
                return self.builder.fneg(ov)
            return self.builder.sub(ir.Constant(I64, 0), ov)
        if expr.op == "NOT":
            return self.builder.not_(ov)
        raise CodegenError(f"unsupported unary operator '{expr.op}'")  # pragma: no cover

    def _global_string(self, text: str) -> ir.Value:
        assert self.builder is not None
        cached = self._string_cache.get(text)
        if cached is None:
            data = text.encode("utf-8") + b"\0"
            arr_ty = ir.ArrayType(I8, len(data))
            self._string_counter += 1
            gv = ir.GlobalVariable(self.module, arr_ty, name=f".str.{self._string_counter}")
            gv.global_constant = True
            gv.linkage = "private"
            gv.initializer = ir.Constant(arr_ty, bytearray(data))
            cached = gv
            self._string_cache[text] = gv
        return self.builder.gep(cached, [ir.Constant(I32, 0), ir.Constant(I32, 0)], inbounds=True)

    def _gen_write_builtin(self, name: str, call: A.Call, scope: Scope) -> None:
        assert self.builder is not None
        if name == "WriteLn":
            self.builder.call(self.printf_fn, [self._global_string("\n")])
            return
        arg_val = self.gen_expr(call.args[0], scope)
        if name == "WriteInt":
            self.builder.call(self.printf_fn, [self._global_string("%lld"), arg_val])
        elif name == "WriteReal":
            self.builder.call(self.printf_fn, [self._global_string("%f"), arg_val])
        elif name == "WriteChar":
            promoted = self.builder.zext(arg_val, I32)
            self.builder.call(self.printf_fn, [self._global_string("%c"), promoted])
        elif name == "WriteBool":
            chosen = self.builder.select(
                arg_val, self._global_string("TRUE"), self._global_string("FALSE")
            )
            self.builder.call(self.printf_fn, [self._global_string("%s"), chosen])
        else:  # pragma: no cover
            raise CodegenError(f"unknown builtin procedure '{name}'")

    def gen_call(self, call: A.Call, scope: Scope) -> ir.Value:
        assert self.builder is not None
        if call.is_builtin_void_proc:
            self._gen_write_builtin(call.is_builtin_void_proc, call, scope)
            return ir.Constant(I32, 0)
        if call.is_builtin_conversion:
            conv = call.is_builtin_conversion
            arg_val = self.gen_expr(call.args[0], scope)
            if conv == "FLOAT":
                return self.builder.sitofp(arg_val, F64)
            if conv == "TRUNC":
                return self.builder.fptosi(arg_val, I64)
            if conv == "ORD":
                return self.builder.zext(arg_val, I64)
            if conv == "CHR":
                return self.builder.trunc(arg_val, I8)
            raise CodegenError(f"unknown builtin '{conv}'")  # pragma: no cover

        assert call.resolved_mangled_name is not None
        fn = self.functions[call.resolved_mangled_name]
        param_types = call.resolved_param_types
        param_by_ref = call.resolved_param_by_ref
        args: list[ir.Value] = []
        for arg_expr, pt, by_ref in zip(call.args, param_types, param_by_ref, strict=True):
            if by_ref:
                assert isinstance(arg_expr, A.Designator)
                addr, _t = self.gen_designator_address(arg_expr, scope)
                args.append(addr)
            else:
                args.append(self.gen_expr_as(arg_expr, scope, pt))
        return self.builder.call(fn, args)


def generate(program: AnalyzedProgram, module_name: str = "modplus_module") -> ir.Module:
    return Codegen(module_name).generate(program)
