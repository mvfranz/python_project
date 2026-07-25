"""Diagnostics for the modplus compiler."""

from __future__ import annotations


class SourcePos:
    __slots__ = ("line", "column")

    def __init__(self, line: int, column: int) -> None:
        self.line = line
        self.column = column

    def __str__(self) -> str:
        return f"{self.line}:{self.column}"


class ModplusError(Exception):
    """Base class for all user-facing compiler errors."""

    stage = "error"

    def __init__(self, message: str, pos: SourcePos | None = None) -> None:
        self.message = message
        self.pos = pos
        location = f" at {pos}" if pos is not None else ""
        super().__init__(f"{self.stage}{location}: {message}")


class LexError(ModplusError):
    stage = "lexical error"


class ParseError(ModplusError):
    stage = "syntax error"


class SemaError(ModplusError):
    stage = "type error"


class CodegenError(ModplusError):
    stage = "codegen error"
