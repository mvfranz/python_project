"""Tokenizer for the modplus language."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .errors import LexError, SourcePos

KEYWORDS = {
    "MODULE",
    "BEGIN",
    "END",
    "CONST",
    "TYPE",
    "VAR",
    "PROCEDURE",
    "GENERIC",
    "RECORD",
    "ARRAY",
    "OF",
    "POINTER",
    "TO",
    "OWN",
    "IF",
    "THEN",
    "ELSIF",
    "ELSE",
    "WHILE",
    "DO",
    "REPEAT",
    "UNTIL",
    "FOR",
    "BY",
    "RETURN",
    "NEW",
    "DISPOSE",
    "AND",
    "OR",
    "NOT",
    "DIV",
    "MOD",
    "TRUE",
    "FALSE",
    "NIL",
    "INTEGER",
    "REAL",
    "BOOLEAN",
    "CHAR",
}


class TokKind(Enum):
    IDENT = auto()
    INT_LIT = auto()
    REAL_LIT = auto()
    CHAR_LIT = auto()
    STRING_LIT = auto()
    KEYWORD = auto()
    OP = auto()
    EOF = auto()


@dataclass
class Token:
    kind: TokKind
    text: str
    pos: SourcePos

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"Token({self.kind.name}, {self.text!r}, {self.pos})"


# Multi-character operators must be listed before their single-char prefixes.
_OPERATORS = [
    ":=",
    "<=",
    ">=",
    "<>",
    "..",
    "+",
    "-",
    "*",
    "/",
    "=",
    "#",
    "<",
    ">",
    "(",
    ")",
    "[",
    "]",
    ".",
    ",",
    ";",
    ":",
    "^",
]


class Lexer:
    def __init__(self, source: str) -> None:
        self.src = source
        self.i = 0
        self.line = 1
        self.col = 1
        self.n = len(source)

    def _pos(self) -> SourcePos:
        return SourcePos(self.line, self.col)

    def _peek(self, offset: int = 0) -> str:
        j = self.i + offset
        return self.src[j] if j < self.n else "\0"

    def _advance(self) -> str:
        ch = self.src[self.i]
        self.i += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _skip_trivia(self) -> None:
        while self.i < self.n:
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
            elif ch == "(" and self._peek(1) == "*":
                self._skip_block_comment()
            else:
                break

    def _skip_block_comment(self) -> None:
        start = self._pos()
        self._advance()
        self._advance()
        depth = 1
        while depth > 0:
            if self.i >= self.n:
                raise LexError("unterminated comment", start)
            if self._peek() == "(" and self._peek(1) == "*":
                self._advance()
                self._advance()
                depth += 1
            elif self._peek() == "*" and self._peek(1) == ")":
                self._advance()
                self._advance()
                depth -= 1
            else:
                self._advance()

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while True:
            self._skip_trivia()
            if self.i >= self.n:
                tokens.append(Token(TokKind.EOF, "", self._pos()))
                break
            tok = self._next_token()
            tokens.append(tok)
        return tokens

    def _next_token(self) -> Token:
        pos = self._pos()
        ch = self._peek()

        if ch.isalpha() or ch == "_":
            return self._read_ident(pos)
        if ch.isdigit():
            return self._read_number(pos)
        if ch == '"':
            return self._read_string(pos)
        if ch == "'":
            return self._read_char(pos)
        for op in _OPERATORS:
            if self.src.startswith(op, self.i):
                for _ in op:
                    self._advance()
                return Token(TokKind.OP, op, pos)
        raise LexError(f"unexpected character {ch!r}", pos)

    def _read_ident(self, pos: SourcePos) -> Token:
        start = self.i
        while self._peek().isalnum() or self._peek() == "_":
            self._advance()
        text = self.src[start : self.i]
        if text in KEYWORDS:
            return Token(TokKind.KEYWORD, text, pos)
        return Token(TokKind.IDENT, text, pos)

    def _read_number(self, pos: SourcePos) -> Token:
        start = self.i
        while self._peek().isdigit():
            self._advance()
        is_real = False
        if self._peek() == "." and self._peek(1).isdigit():
            is_real = True
            self._advance()
            while self._peek().isdigit():
                self._advance()
        if self._peek() in ("e", "E"):
            is_real = True
            self._advance()
            if self._peek() in ("+", "-"):
                self._advance()
            while self._peek().isdigit():
                self._advance()
        text = self.src[start : self.i]
        return Token(TokKind.REAL_LIT if is_real else TokKind.INT_LIT, text, pos)

    def _read_string(self, pos: SourcePos) -> Token:
        self._advance()
        start = self.i
        while self._peek() != '"':
            if self.i >= self.n:
                raise LexError("unterminated string literal", pos)
            self._advance()
        text = self.src[start : self.i]
        self._advance()
        return Token(TokKind.STRING_LIT, text, pos)

    def _read_char(self, pos: SourcePos) -> Token:
        self._advance()
        if self.i >= self.n:
            raise LexError("unterminated character literal", pos)
        ch = self._advance()
        if self._peek() != "'":
            raise LexError("character literal must contain exactly one character", pos)
        self._advance()
        return Token(TokKind.CHAR_LIT, ch, pos)


def tokenize(source: str) -> list[Token]:
    return Lexer(source).tokenize()
