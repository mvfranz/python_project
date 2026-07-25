"""JIT execution and ahead-of-time object-file emission via llvmlite."""

from __future__ import annotations

import ctypes

import llvmlite.binding as llvm

from .codegen import generate
from .errors import ModplusError
from .parser import parse
from .sema import analyze_program

_initialized = False


def _ensure_llvm_initialized() -> None:
    global _initialized
    if not _initialized:
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()
        _initialized = True


def compile_to_llvm_ir(sources: list[str], module_name: str = "modplus_module") -> str:
    """`sources[0]` is the program's entry module; any others must be
    (directly or transitively) IMPORTed by it -- see
    `sema.py`'s `_order_modules` for the exact rules."""
    modules = [parse(src) for src in sources]
    program = analyze_program(modules)
    llvm_module = generate(program, module_name)
    ir_text = str(llvm_module)
    _ensure_llvm_initialized()
    llvm.parse_assembly(ir_text).verify()
    return ir_text


def compile_to_object(sources: list[str], module_name: str = "modplus_module") -> bytes:
    _ensure_llvm_initialized()
    ir_text = compile_to_llvm_ir(sources, module_name)
    llvm_module = llvm.parse_assembly(ir_text)
    # `reloc="pic"` matters here: most modern Linux distros default to
    # linking position-independent executables, and the linker rejects
    # non-PIC relocations against read-only data with a PIE binary.
    target_machine = llvm.Target.from_default_triple().create_target_machine(
        reloc="pic", codemodel="small"
    )
    llvm_module.triple = target_machine.triple
    llvm_module.data_layout = str(target_machine.target_data)
    llvm_module.verify()
    return target_machine.emit_object(llvm_module)


def run(sources: list[str], module_name: str = "modplus_module") -> int:
    """JIT-compile `sources` (entry module first) and execute the compiled
    program's `main`, returning the process-style exit code it produced."""

    _ensure_llvm_initialized()
    modules = [parse(src) for src in sources]
    program = analyze_program(modules)
    llvm_ir_module = generate(program, module_name)
    llvm_module = llvm.parse_assembly(str(llvm_ir_module))
    llvm_module.verify()
    target_machine = llvm.Target.from_default_triple().create_target_machine()
    engine = llvm.create_mcjit_compiler(llvm_module, target_machine)
    engine.finalize_object()
    engine.run_static_constructors()

    func_ptr = engine.get_function_address("main")
    if not func_ptr:
        raise ModplusError("failed to locate 'main' entry point after JIT compilation")
    cfunc = ctypes.CFUNCTYPE(ctypes.c_int32)(func_ptr)
    return cfunc()
