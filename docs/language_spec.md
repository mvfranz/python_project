# The modplus Language Specification

modplus is a Modula-2-lineage systems language: same block structure,
keywords, and philosophy of "no hidden control flow, no hidden cost," with
two additions grafted on top -- C++-style compile-time generics, and an
opt-in scope-bound pointer type for the cases where fully manual
`NEW`/`DISPOSE` is more ceremony than the problem needs.

This document is both a language reference and a record of *why* each
design choice was made, and where the current implementation (a focused
prototype, not a production compiler) draws the line on scope.

## 1. Philosophy and cost model

Four design goals came from the project brief, and each maps to a concrete
implementation choice:

| Goal | How modplus delivers it |
|---|---|
| Strongly typed | Nominal types, zero implicit conversions -- see [§7](#7-strong-typing-and-conversions) |
| Zero-cost, cache-friendly abstraction | Generics are monomorphized, not type-erased; `RECORD`/`ARRAY` are always inline value types -- see [§6](#6-generics-templates) and [§4.2](#42-record-and-array) |
| Easy scoping | Exactly two lexical levels (module, procedure), no closures -- see [§8](#8-scoping) |
| Manual memory management | `POINTER` is fully manual; `OWN POINTER` is scope-bound but still zero-hidden-runtime-cost -- see [§9](#9-memory-management) |

## 2. Lexical structure

- Keywords are upper-case and reserved (`MODULE`, `BEGIN`, `IF`, `GENERIC`,
  `OWN`, ...). Identifiers are case-sensitive.
- Comments are `(* ... *)` and may nest.
- Literals: `42` (INTEGER), `3.14` / `1e3` (REAL), `'A'` (CHAR, single
  quotes), `"text"` (a string literal -- see [§4.5](#45-string-literals)),
  `TRUE`/`FALSE` (BOOLEAN), `NIL` (untyped pointer literal).
- Operators: `:=  + - * / = # < <= > >= ( ) [ ] . , ; : ^`, and the
  keyword-operators `DIV MOD AND OR NOT`.

## 3. Module structure

A modplus program is one or more `MODULE`s:

```modula2
MODULE Name;
IMPORT Other;  (* optional, zero or more, comma lists allowed *)
  (* CONST / TYPE / VAR / PROCEDURE sections, in any order, any number of times *)
BEGIN
  (* statement sequence: this module's own initialization code *)
END Name.
```

Unlike classic Modula-2, procedures do **not** need forward declarations:
every top-level `PROCEDURE`'s signature is registered before any body is
type-checked, so mutual recursion and "helper defined below `main`" both
just work.

### 3.1 Multi-module compilation and IMPORT

```bash
modplusc run main.m2p helper.m2p   # main.m2p is the entry module
```

Every module the program needs is listed on the command line; the
**first** file is the entry module, and every other file must be
IMPORTed by it, directly or transitively (an extra file that nothing
imports is a compile error -- far more likely a typo than an intentional
no-op, so it's rejected rather than silently ignored). There is no
filesystem search path to configure and no build manifest: the module
graph is exactly what you name on the command line.

**Exports are implicit and qualified-only.** Every top-level
`CONST`/`TYPE`/`VAR`/`PROCEDURE` a module declares is automatically
visible to any module that `IMPORT`s it, reached as `ModuleName.Name` --
there is no separate `DEFINITION MODULE`/`EXPORT` list to maintain, and
no unqualified `FROM Foo IMPORT Bar;` form (deliberately simpler than
classic Modula-2 on both counts; see [§11](#11-known-limitations)):

```modula2
MODULE MathUtils;
VAR CallCount: INTEGER;
PROCEDURE Square(x: INTEGER): INTEGER;
BEGIN
  CallCount := CallCount + 1;
  RETURN x * x;
END Square;
BEGIN
  CallCount := 0;
END MathUtils.
```

```modula2
MODULE Main;
IMPORT MathUtils;
BEGIN
  WriteInt(MathUtils.Square(3));   (* qualified call *)
  WriteInt(MathUtils.CallCount);   (* qualified variable reference *)
END Main.
```

A qualified `TYPE` reference (`MathUtils.Point`) works the same way,
anywhere a type is expected -- a `VAR` declaration, a `PROCEDURE`
parameter or return type, a field type, or a generic template argument
(`Box<MathUtils.Point>`). It names the real, importing-module-independent
type MathUtils declared -- a `VAR p: MathUtils.Point;` is an ordinary
local `Point` value, not a handle back into MathUtils, exactly as if
`Point` had been declared locally:

```modula2
MODULE MathUtils;
TYPE Point = RECORD x, y: INTEGER; END;
...
```

```modula2
MODULE Main;
IMPORT MathUtils;
VAR origin: MathUtils.Point;
BEGIN
  origin.x := 3;
  origin.y := 4;
END Main.
```

`GENERIC TYPE`s cannot themselves be imported (see
[§11](#11-known-limitations)), so `MathUtils.Box<INTEGER>` -- a qualified
reference to a *generic* type, with its own `<...>` instantiation -- is
rejected; only ordinary (non-generic) exported types can be referenced
this way.

A module name occupies the same namespace as everything else -- you
can't `IMPORT Foo;` and also declare a local `VAR Foo: INTEGER;` -- and
qualified access only reaches a module you actually `IMPORT`ed yourself:
being part of the same compilation (because *something else* imports it)
doesn't make its members visible to you.

**Compilation is whole-program, not separately-linked.** All imported
modules are parsed, type-checked, and code-generated together into a
single LLVM module -- there's no per-file object file, no `extern`
linkage, and no separate link step for modplus code (native output via
`emit-object` still goes through a real linker, just with everything
already combined). The trade-off: touching one file means recompiling
the whole program, same as a unity/jumbo build. What you get in exchange
is a much smaller compiler: cross-module calls are just calls to an
already-emitted LLVM function, with no ABI-stability-across-translation-units
concerns to design for.

**Module initialization order.** Every module's `BEGIN...END` body --
including the entry module's -- becomes its own `{Name}$init` function.
The compiled program's real `main` calls each module's `$init` in
dependency order (an imported module's initialization always completes
before anything that imports it runs) and the entry module's runs last,
playing the role "the program" would play if there were no other modules
at all.

Circular `IMPORT`s are rejected outright with the cycle spelled out
(`circular IMPORT: A -> B -> A`) -- there's no notion of "partially
initialized" a cycle could produce, so this is caught before any
analysis of the involved modules' bodies even happens.

## 4. Types

Built-in scalars: `INTEGER` (64-bit signed), `REAL` (64-bit float),
`BOOLEAN`, `CHAR` (8-bit).

### 4.1 Nominal typing

Two `RECORD` types with identical fields but different names are different
types. There is no structural typing and no implicit conversion between
distinct named types -- see [§7](#7-strong-typing-and-conversions).

### 4.2 RECORD and ARRAY

```modula2
TYPE
  Point = RECORD x, y: INTEGER; END;
  Row   = ARRAY[8] OF INTEGER;
```

Both are **value types**: assigning a `RECORD` or `ARRAY` copies it, and
they are never heap-allocated implicitly. A `Point` local is one inline
`alloca`; a `Point` field inside another `RECORD` is inline in that
struct's layout; a `Point` passed by value is copied into the callee's
frame. This is what makes them cache-friendly (contiguous, no
indirection) and what makes generics over them zero-cost -- there's no
boxing to elide because there was never any boxing.

Array bounds are **not** checked at run time (no hidden branch on every
index) -- an out-of-bounds index is undefined behavior, same trade-off as
C arrays.

### 4.3 POINTER and OWN POINTER

See [§9](#9-memory-management).

### 4.4 The NIL literal

`NIL` has no type of its own; it is only valid where a `POINTER` (owning
or not) is expected -- as an assignment's right-hand side, a `RETURN`
value, an argument, or either side of `=`/`#`.

### 4.5 String literals

Like Modula-2 itself, modplus has no dedicated `STRING` type. A string
literal `"text"` is instead a literal-only marker type (`StringLitType`,
carrying its length), exactly analogous to how `NIL` (`NilType`,
[§4.4](#44-the-nil-literal)) widens assignment compatibility without being
a real type of its own. `"text"` is assignment-compatible with any
`ARRAY[N] OF CHAR` where `N` is large enough to hold the text plus a
trailing null terminator (`N >= length + 1`), and by the same rule can be
passed directly wherever an `ARRAY OF CHAR` is expected by value:

```modula2
VAR greeting: ARRAY[20] OF CHAR;
PROCEDURE Greet(who: ARRAY[10] OF CHAR); ...

greeting := "modplus";     (* OK: 7 chars + NUL fits in 20 *)
Greet("Ada");               (* OK: literal passed by value *)
```

A string literal is not addressable, so it cannot be passed as a `VAR`
parameter, and `Foo("x")` and `"x" := ...`-style direct storage requires
going through an `ARRAY OF CHAR` variable first.

Only 8-bit (Latin-1) characters are supported -- a literal containing a
code point above 255 is a compile-time error, matching `CHAR`'s single-byte
representation.

`WriteString(x)` is a builtin that prints either a string literal or an
`ARRAY OF CHAR` variable to standard output, stopping at the first `NUL`
byte; it takes exactly one argument and does not append a newline (pair it
with `WriteLn`).

`=`, `#`/`<>`, `<`, `<=`, `>`, and `>=` all work between two `ARRAY OF
CHAR` values, or a value and a literal, following the same
NUL-terminated-bytes convention as `WriteString` -- comparison doesn't
require the operands' declared `ARRAY` sizes to match, so an `ARRAY[5]
OF CHAR` and an `ARRAY[20] OF CHAR` holding equal text compare equal:

```modula2
VAR a: ARRAY[5] OF CHAR; b: ARRAY[20] OF CHAR;
a := "hi"; b := "hi";
IF a = b THEN ... END;        (* TRUE despite the size mismatch *)
IF a < "hj" THEN ... END;     (* lexicographic, like C's strcmp *)
```

Just like `WriteString`, each operand must be a string literal or an
ordinary variable/field/element -- comparison needs an address (or a
literal's own materialized global) to read bytes from. This is a
genuine `strcmp`-style byte loop at run time, not a free operation the
way `INTEGER` comparison is -- but it costs exactly what a hand-written
comparison would, nothing hidden beyond that. It's also an addition
beyond Modula-2 itself, which has no array comparison operators at all;
see [§11](#11-known-limitations). There is still no string
concatenation -- that would require hidden allocation, against the
zero-hidden-cost design goal ([§1](#1-philosophy-and-cost-model)).

## 5. Declarations

```modula2
CONST Limit = 10;                       (* compile-time constant *)
TYPE  Row = ARRAY[Limit] OF INTEGER;    (* Limit usable in a later TYPE *)
VAR   r: Row;

PROCEDURE Sum(a: Row; VAR total: INTEGER);
BEGIN
  ...
END Sum;
```

`CONST`, `TYPE`, and `VAR` sections at a given level are each processed as
one pass over all their declarations (in file order within each section),
in the order **CONST, then TYPE, then VAR, then PROCEDURE** -- regardless
of how the sections are interleaved in the source. So a `TYPE`'s `ARRAY`
size can reference an earlier `CONST`, but not one declared textually
after it in a different section pass.

`PROCEDURE` parameters are pass-by-value by default; `VAR name: T` makes a
parameter pass-by-reference (the callee writes through to the caller's
variable). A `VAR` argument must be an actual variable, not a constant or
an arbitrary expression.

Records declared at module level may forward-reference each other through
a `POINTER TO OtherRecord` field, regardless of declaration order (the
classic linked-list-node case). This forward-reference support does *not*
extend to `RECORD` types declared inside a procedure, or to generic
`RECORD` templates referencing themselves -- see [§11](#11-known-limitations).

## 6. Generics (templates)

### 6.1 Generic procedures

```modula2
GENERIC PROCEDURE Max<T>(a, b: T): T;
BEGIN
  IF a > b THEN RETURN a; ELSE RETURN b; END;
END Max;

i := Max<INTEGER>(3, 4);   (* explicit instantiation *)
r := Max(2.5, 9.75);       (* T deduced as REAL from the argument types *)
```

Argument deduction only looks at a formal parameter whose declared type
*is* a bare template parameter (`a: T`); it does not look inside a nested
type (`a: ARRAY[N] OF T` or `a: POINTER TO T`) -- instantiate those
explicitly with `<...>`.

### 6.2 Generic types, including non-type parameters

```modula2
TYPE Stack<T, N: CONST INTEGER> = RECORD
  items: ARRAY[N] OF T;
  top: INTEGER;
END;

VAR s: Stack<INTEGER, 8>;
```

`N: CONST INTEGER` is a non-type ("value") template parameter, resolved to
a compile-time constant integer at instantiation -- directly analogous to
`template<typename T, int N> struct array` in C++. A generic procedure may
mix ordinary type parameters and `CONST` parameters the same way; when any
`CONST` parameter is present, argument deduction is unavailable and the
call must use explicit `<...>` instantiation.

### 6.3 Explicit specialization

```modula2
PROCEDURE Max<INTEGER>(a, b: INTEGER): INTEGER;
BEGIN
  IF a >= b THEN RETURN a; END;
  RETURN b;
END Max;
```

A `PROCEDURE Name<ConcreteArgs>(...)` with no `GENERIC` keyword is an
explicit specialization: for that exact set of type arguments, its body
replaces the generic template's. Call sites can't tell the difference --
`instantiate_generic_proc` checks for a specialization first and only
falls back to substituting into the generic template if none exists.

### 6.4 Monomorphization, precisely

Instantiating `Name<Args>` binds each template parameter to its argument
in a fresh scope (a type parameter becomes a type alias; a `CONST`
parameter becomes a constant), deep-copies the template's body, and
re-runs ordinary type-checking and code generation against that scope.
The result is a distinct, uniquely-named LLVM function (`Max$INTEGER`) or
struct (`Stack$INTEGER$8`) per instantiation -- no runtime type tag, no
vtable, no dictionary lookup. Two different instantiations of the same
template share no code and pay no dispatch cost relative to hand-writing
each one separately.

A direct consequence, also true of C++ templates: **an uninstantiated
generic's body is never type-checked or compiled.** If nothing in the
program ever calls `Max<CHAR>`, that instantiation simply never exists.

### 6.5 Self-referential generic types

A generic `RECORD` can contain a `POINTER TO` its own (same type
arguments) instantiation, the same way a plain module-level `RECORD`
can reference itself:

```modula2
TYPE
  Node<T> = RECORD
    value: T;
    next: POINTER TO Node<T>;
  END;

VAR head: POINTER TO Node<INTEGER>;
```

This works because instantiating `Node<T>` caches the (still-empty)
struct under its mangled name (`Node$INTEGER`) *before* resolving its
fields -- so when field resolution reaches `POINTER TO Node<T>` with the
same template arguments, it finds that same not-yet-finished struct
instead of recursing forever, exactly mirroring how a non-generic
`RECORD`'s own forward self-reference is resolved. A `POINTER` field
only ever needs its pointee's *identity*, never its complete layout, so
"still being filled in" is fine.

## 7. Strong typing and conversions

Assignment, `RETURN`, and argument-passing all require the value's type to
equal the target's type exactly -- including `INTEGER` vs. `REAL`, which
do **not** implicitly convert (unlike C). Conversions are explicit
built-in procedures:

| Builtin | Signature |
|---|---|
| `FLOAT(i: INTEGER): REAL` | widen to floating point |
| `TRUNC(r: REAL): INTEGER` | truncate toward zero |
| `ORD(c: CHAR): INTEGER` | character code |
| `CHR(i: INTEGER): CHAR` | code to character |

Operators are similarly strict: `+ - *` require two operands of the same
type (`INTEGER` or `REAL`); `/` requires `REAL` (use `DIV`/`MOD` for
integer division, which truncate toward zero, matching LLVM's `sdiv`);
`AND OR NOT` require `BOOLEAN`; `=`/`#` require matching types and reject
`RECORD`/`ARRAY` operands (no way to generate that comparison as one
machine instruction, so it's rejected rather than silently doing the
wrong thing); ordering operators (`< <= > >=`) additionally require
`INTEGER`, `REAL`, or `CHAR` (not `BOOLEAN`, not pointers). `NIL` may only
be compared with `=`/`#` against a `POINTER`.

## 8. Scoping

Exactly two lexical levels: one module scope, and one scope per procedure
for its parameters and its own `CONST`/`TYPE`/`VAR` sections. `IF`/`WHILE`/
`FOR`/etc. bodies do **not** introduce a new scope (same as classic
Modula-2 -- you can't declare a variable inside an `IF`).

Nested `PROCEDURE` declarations are parsed but rejected by the analyzer in
this prototype specifically to avoid closures: supporting them properly
would mean either capturing outer locals (hidden indirection through a
captured-variable environment -- exactly the kind of hidden cost/hidden
scoping this language is trying to avoid) or silently forbidding outer-local
access while still allowing sibling calls (a visibility model that's easy
to get subtly wrong). Declare a helper as a top-level `PROCEDURE` instead.

## 9. Memory management

Two pointer flavors, chosen explicitly per declaration:

```modula2
VAR p:     POINTER TO Node;      (* fully manual *)
VAR own_p: OWN POINTER TO Node;  (* compiler-managed, scope-bound *)
```

**`POINTER TO T`** is exactly Modula-2's pointer: `NEW(p)` allocates
(`malloc(sizeof(T))`), `DISPOSE(p)` frees. Nothing happens automatically;
forget a `DISPOSE` and it leaks, `DISPOSE` twice and it double-frees --
the same trade-offs as C, by design.

**`OWN POINTER TO T`** ties the allocation's lifetime to its declaring
scope: `NEW` still allocates, but the compiler inserts the matching
`free()` itself at every point control can leave that scope -- each
`RETURN` in the procedure, and falling off the end of it. Manually
calling `DISPOSE` on an `OWN` pointer is a **compile-time error**, because
the compiler is already going to free it; there is exactly one place
ownership ends, and the compiler puts the call there. This costs nothing
beyond the `free()` call itself that a hand-written version would also
need -- no reference counting, no GC, no hidden allocation for bookkeeping.

`OWN POINTER` cannot be used as a parameter type or a return type: both
would require deciding whether ownership transfers across a call
boundary, which this prototype sidesteps entirely by keeping `OWN`
strictly local to the scope that allocated it. If a value needs to
outlive its creating scope, use a plain `POINTER` and manage it explicitly.

## 10. Statements

`IF/ELSIF/ELSE`, `WHILE/DO`, `REPEAT/UNTIL`, `FOR var := start TO stop [BY step] DO`
(bounds are evaluated once, before the loop starts; `var` must already be
a declared `INTEGER` variable), `RETURN [value]`, procedure calls, and
`NEW`/`DISPOSE`. A function (non-`NIL` return type) must `RETURN` a value
on every path the type-checker can prove is reachable -- `IF/ELSIF/ELSE`
where every branch returns counts; falling out of a loop does not (loops
aren't proven to execute their body), so a function can't rely on a
`RETURN` that's only inside a `WHILE`.

## 11. Known limitations

Deliberate scope cuts for this prototype, listed so they read as decisions
rather than surprises:

- **Generic templates cannot be imported.** `GENERIC PROCEDURE`/`TYPE`
  declarations, and explicit specializations of them, are only visible
  within their own module -- a qualified reference to one (`Foo.Bar<T>`)
  fails, since generic names are never declared into a module's exported
  scope at all (see [§3.1](#31-multi-module-compilation-and-import)).
- **No selective/unqualified IMPORT.** Only `IMPORT Foo;` (whole-module,
  qualified-access) -- no `FROM Foo IMPORT Bar;`.
- **No separate export list.** Every top-level declaration is
  automatically exported; there's no `DEFINITION MODULE`-equivalent way
  to keep something module-private while still using it across
  procedures in the same module.
- **No nested procedures** (see [§8](#8-scoping)).
- **No array bounds checking** (see [§4.2](#42-record-and-array)).
- **Template argument deduction is shallow** -- only bare `a: T` formal
  parameters participate; nested occurrences (`ARRAY[N] OF T`,
  `POINTER TO T`) require explicit `<...>` instantiation.
- **No first-class procedures** -- you can call a `PROCEDURE` by name,
  but can't store one in a variable or pass it as a value.
- **No string concatenation** -- only assignment, passing by value, and
  `=`/`#`/`<`/`<=`/`>`/`>=` comparison are supported; see
  [§4.5](#45-string-literals).
- **Strings are 8-bit (Latin-1) only** -- a string literal containing a
  code point above 255 is rejected at compile time.

## 12. Compiler architecture

```
source.m2p (x N) -> lexer.py -> parser.py -> sema.py -> codegen.py -> LLVM IR
                                                                          |
                                                       +---------------+---------------+
                                                       |                               |
                                                 jit.py (MCJIT)                emit-object (native .o)
```

- **`lexer.py` / `parser.py`** -- hand-written tokenizer and recursive-descent
  parser producing a plain-dataclass AST (`ast_nodes.py`).
- **`types.py` / `symbols.py`** -- the nominal type system and the two-level
  scope chain. `symbols.ImportedModuleSymbol` is the marker declared for each
  `IMPORT`ed name, occupying the same namespace as everything else so a
  colliding local declaration is caught by the same duplicate-name check.
- **`sema.py`** -- name resolution, strong type-checking, and the generics
  monomorphization engine described in [§6.4](#64-monomorphization-precisely).
  Annotates the AST in place (`.resolved_type` and friends) so codegen never
  re-derives a decision sema already made. `analyze_program` orchestrates
  multiple modules: `_order_modules` topologically sorts them (validating
  the import graph along the way -- unknown/unused modules, cycles), then a
  single `Analyzer` instance processes each in dependency order, resetting
  its per-module state (scope, generic template caches) between modules
  while accumulating the cross-module registries (`modules`,
  `module_bodies`, the codegen queue). Module-level names get mangled with
  their module as a prefix (`Foo$Bar`) precisely so this can all still
  land in one LLVM module without collisions.
- **`codegen.py`** -- walks the annotated AST once, emitting LLVM IR via
  [llvmlite](https://github.com/numba/llvmlite). Each `Codegen` instance owns
  a private LLVM `Context`, since identified struct types are keyed by name
  *within* a context -- without this, two independent compilations that both
  declare a `RECORD` named `Node` would collide. Every module gets its own
  `{Name}$init` function; the real `main` calls them in dependency order.
- **`jit.py` / `cli.py`** -- MCJIT execution (`modplusc run`), textual IR
  dump (`modplusc emit-llvm`), and native object-file emission via a real
  `TargetMachine` (`modplusc emit-object`, position-independent so the
  result links straight into a PIE executable). All three take one or more
  source files, entry module first.
