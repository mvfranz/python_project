# modplus

A small, strongly-typed systems programming language in the Modula-2
lineage, compiled to native code through LLVM, with C++-style compile-time
templates.

Start with the [Language Specification](language_spec.md) for the full
grammar, type system, generics/monomorphization model, and memory-management
design, or look at [`examples/`](https://github.com/mvfranz/python_project/tree/main/examples)
in the repository for runnable programs.

## Quickstart

```bash
pip install -e .[test]
modplusc run examples/hello.m2p
```
