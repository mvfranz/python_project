"""The modplus nominal type system.

Value types (INTEGER, REAL, BOOLEAN, CHAR, RECORD, ARRAY) are always laid
out inline -- as LLVM `alloca`s, struct/array fields, or by-value function
arguments -- never behind a hidden heap allocation or vtable. That is what
gives records and arrays contiguous, cache-friendly memory layout and makes
generic instantiation a zero-cost (compile-time only) abstraction: a
`Stack<INTEGER>` and a hand-written `IntStack` compile to the identical
struct layout and machine code.
"""

from __future__ import annotations

from dataclasses import dataclass


class Type:
    """Base class for all resolved (post type-checking) types."""

    def __eq__(self, other: object) -> bool:  # pragma: no cover - overridden below
        return self is other

    def __hash__(self) -> int:  # pragma: no cover
        return id(self)


class ScalarType(Type):
    """Singleton scalar types compared by identity."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


INTEGER = ScalarType("INTEGER")
REAL = ScalarType("REAL")
BOOLEAN = ScalarType("BOOLEAN")
CHAR = ScalarType("CHAR")

_BUILTIN_TYPES: dict[str, Type] = {
    "INTEGER": INTEGER,
    "REAL": REAL,
    "BOOLEAN": BOOLEAN,
    "CHAR": CHAR,
}


def builtin_named_type(name: str) -> Type | None:
    return _BUILTIN_TYPES.get(name)


@dataclass(eq=True, frozen=True)
class ArrayType(Type):
    elem: Type
    size: int

    def __repr__(self) -> str:
        return f"ARRAY[{self.size}] OF {self.elem!r}"

    def __hash__(self) -> int:
        return hash((ArrayType, self.elem, self.size))


class RecordField:
    __slots__ = ("name", "type", "index")

    def __init__(self, name: str, type_: Type, index: int) -> None:
        self.name = name
        self.type = type_
        self.index = index


class RecordType(Type):
    """Nominal: every declared/instantiated RECORD is its own distinct type,
    identified by name, even if two records share identical field lists.
    Fields keep declaration order so the generated LLVM struct is laid out
    contiguously in that order (no reordering, no hidden padding beyond
    what LLVM's target data layout naturally inserts for alignment)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.fields: list[RecordField] = []
        self._field_index: dict[str, RecordField] = {}

    def add_field(self, name: str, type_: Type) -> None:
        field = RecordField(name, type_, len(self.fields))
        self.fields.append(field)
        self._field_index[name] = field

    def field(self, name: str) -> RecordField | None:
        return self._field_index.get(name)

    def __repr__(self) -> str:
        return f"RECORD {self.name}"


class PointerType(Type):
    """`base` may be None transiently while resolving a self-referential
    record (`POINTER TO Node` inside `Node`'s own declaration); codegen
    only ever sees fully-resolved pointer types."""

    def __init__(self, base: Type | None, owning: bool) -> None:
        self.base = base
        self.owning = owning

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, PointerType)
            and self.owning == other.owning
            and self.base == other.base
        )

    def __hash__(self) -> int:
        return hash((PointerType, id(self.base), self.owning))

    def __repr__(self) -> str:
        kw = "OWN POINTER TO" if self.owning else "POINTER TO"
        return f"{kw} {self.base!r}"


class NilType(Type):
    """The type of the `NIL` literal: assignable to any POINTER, but not a
    real type of its own -- there is no `VAR x: NIL;`."""

    def __repr__(self) -> str:
        return "NIL"


NIL = NilType()


class StringLitType(Type):
    """The type of a string literal (`"hello"`): assignable to any
    `ARRAY[N] OF CHAR` with room for the text plus a null terminator, but
    not a real type of its own -- there is no standalone `STRING` type
    (see `docs/language_spec.md`'s STRING section for why). Mirrors
    `NilType`'s role for pointers: a literal-only marker that widens
    assignment compatibility without generalizing ARRAY assignment
    itself (two same-element, different-size ARRAYs are still not
    interchangeable)."""

    def __init__(self, length: int) -> None:
        self.length = length

    def __repr__(self) -> str:
        return f"STRING literal (length {self.length})"


def is_value_type(t: Type) -> bool:
    return isinstance(t, (ScalarType, ArrayType, RecordType))


def is_numeric(t: Type) -> bool:
    return t is INTEGER or t is REAL


def type_name(t: Type) -> str:
    return repr(t)
