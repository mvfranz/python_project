# modplus

[![CI](https://github.com/mvfranz/python_project/actions/workflows/main.yml/badge.svg)](https://github.com/mvfranz/python_project/actions/workflows/main.yml)

**modplus** is a small, strongly-typed systems programming language in the
Modula-2 lineage, compiled to native code through [LLVM](https://llvm.org/)
(via [llvmlite](https://github.com/numba/llvmlite)). It adds C++-style
compile-time templates on top of Modula-2's syntax and philosophy, while
keeping memory management fully manual (or scope-bound, your choice) and
scoping simple and explicit.

```modula2
MODULE GenericsMax;

GENERIC PROCEDURE Max<T>(a, b: T): T;
BEGIN
  IF a > b THEN
    RETURN a;
  ELSE
    RETURN b;
  END;
END Max;

VAR i: INTEGER; r: REAL;

BEGIN
  i := Max<INTEGER>(7, 3);   (* monomorphized: a dedicated Max$INTEGER *)
  r := Max(2.5, 9.75);       (* type argument deduced as REAL *)
  WriteInt(i); WriteLn;
  WriteReal(r); WriteLn;
END GenericsMax.
```

See [`examples/`](examples/) for more (generics with non-type parameters,
manual `NEW`/`DISPOSE` linked lists, and `OWN` pointers), and
[`docs/language_spec.md`](docs/language_spec.md) for the full language
reference and the design rationale behind each feature.

## Design goals

- **Strongly, statically typed.** Nominal types, no implicit numeric
  coercion (not even `INTEGER`↔`REAL`) -- conversions are explicit
  (`FLOAT`, `TRUNC`, `ORD`, `CHR`).
- **Zero-cost, cache-friendly generics.** `GENERIC PROCEDURE`/`TYPE<...>`
  templates are monomorphized at compile time -- each instantiation is a
  distinct LLVM function/struct with no runtime type tag, vtable, or
  indirection, same cost model as C++ templates. Explicit specialization
  (`PROCEDURE Max<INTEGER>(...)`) lets you override the template for a
  specific instantiation.
- **Value types are inline.** `RECORD` and `ARRAY` are never boxed; they
  live directly in locals, struct fields, or array elements, contiguous in
  memory.
- **Simple, explicit scoping.** Two levels only -- module and procedure.
  Nested procedures don't capture outer locals, so there is never a
  hidden closure to reason about.
- **Manual memory management, with a safety valve.** `POINTER TO T` is
  fully manual (`NEW`/`DISPOSE`, can leak or dangle, same as C).
  `OWN POINTER TO T` is scope-bound: the compiler inserts the matching
  `free()` on every path out of its declaring scope, and manually calling
  `DISPOSE` on one is a compile error.

## Install

Requires Python 3.11+.

```bash
pip install -e .[test]
```

## Usage

```bash
modplusc run examples/hello.m2p          # JIT-compile and execute
modplusc emit-llvm examples/hello.m2p     # print LLVM IR
modplusc emit-object examples/hello.m2p -o hello.o   # native object file
```

```bash
$ make examples   # run every example program
$ make test       # lint (ruff + mypy) and run the test suite
```

## Project layout

```text
src/modplus/     the compiler: lexer, parser, sema (type-check + generics
                 monomorphization), codegen (llvmlite/LLVM IR), CLI, JIT
examples/        example .m2p programs
tests/           pytest suite (unit tests + end-to-end JIT execution)
docs/            language specification and design rationale
```

## Development

Read the [CONTRIBUTING.md](CONTRIBUTING.md) file.
