import pytest

from modplus.lexer import TokKind, tokenize


def test_tokenizes_keywords_and_identifiers():
    toks = tokenize("MODULE Foo;")
    kinds = [(t.kind, t.text) for t in toks]
    assert kinds == [
        (TokKind.KEYWORD, "MODULE"),
        (TokKind.IDENT, "Foo"),
        (TokKind.OP, ";"),
        (TokKind.EOF, ""),
    ]


def test_tokenizes_numbers():
    toks = tokenize("42 3.14 1e3 2.5e-2")
    values = [(t.kind, t.text) for t in toks if t.kind != TokKind.EOF]
    assert values == [
        (TokKind.INT_LIT, "42"),
        (TokKind.REAL_LIT, "3.14"),
        (TokKind.REAL_LIT, "1e3"),
        (TokKind.REAL_LIT, "2.5e-2"),
    ]


def test_tokenizes_char_and_string_literals():
    toks = tokenize("'A' \"hello\"")
    assert toks[0].kind == TokKind.CHAR_LIT
    assert toks[0].text == "A"
    assert toks[1].kind == TokKind.STRING_LIT
    assert toks[1].text == "hello"


def test_skips_nested_block_comments():
    toks = tokenize("(* outer (* inner *) still-comment *) X")
    non_eof = [t for t in toks if t.kind != TokKind.EOF]
    assert len(non_eof) == 1
    assert non_eof[0].text == "X"


def test_multi_char_operators():
    toks = tokenize(":= <= >= # <>")
    ops = [t.text for t in toks if t.kind == TokKind.OP]
    assert ops == [":=", "<=", ">=", "#", "<>"]


def test_unterminated_comment_raises():
    from modplus.errors import LexError

    with pytest.raises(LexError):
        tokenize("(* never closed")
