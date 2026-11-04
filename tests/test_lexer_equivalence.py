"""El camino rápido del lexer produce exactamente lo mismo que el lexer
carácter a carácter anterior (`tests/_reference_lexer.py`, copia literal):
mismos tokens (tipo, lexema, valor, línea, columna) o la misma excepción
(tipo y mensaje), para cualquier entrada."""

from __future__ import annotations

import pytest
from _reference_lexer import Lexer as ReferenceLexer
from hypothesis import given, settings
from hypothesis import strategies as st

from sql_query_parser.lexer import Lexer

Outcome = list[tuple[object, ...]] | tuple[str, str]


def _outcome(lexer_class: type[Lexer] | type[ReferenceLexer], text: str) -> Outcome:
    try:
        tokens = lexer_class(text).tokenize()
    except Exception as exc:  # comparar también el error exacto
        return (type(exc).__name__, str(exc))
    return [
        (t.type, t.lexeme, t.value, type(t.value), t.position.line, t.position.column)
        for t in tokens
    ]


def _same(text: str) -> None:
    assert _outcome(Lexer, text) == _outcome(ReferenceLexer, text), repr(text)


TRICKY = [
    "",
    "   \n\t\r\n  ",
    "SELECT a, b FROM t WHERE x >= 1.5e+3 AND y <> 'it''s' OR z != ?;",
    "INSERT INTO t (a, b) VALUES (1, 'x'), (?, ?), (-2.5, NULL), (TRUE, false)",
    "'a'' b",  # unterminated: the doubled quote must not close the string
    "'a'''",
    "''",
    "'multi\nline\n'x",
    "'unterminated",
    "1.",
    ".5",
    "1.e5",
    "1e",
    "1e+",
    "1e+5x",
    "007",
    "1.٣",  # non-ASCII digits: the original lexer's isdigit() takes them
    "1e5٣",
    "12٣",
    "abc٣",
    "naïve",
    "_x1 é İ K ß",
    "a" + chr(0xA0) + "b",
    "--comment\nSELECT",
    "-- only a comment",
    "a-- c\n-b",
    "a /* x\n y */ b",
    "/* unterminated",
    "a/b-c*d%e.f",
    '"quoted "" id" x',
    '"unterminated',
    "<= >= <> != < > = !",
    "!x",
    "@",
    "x\n\n  y\r\nz",
    "²",
]


@pytest.mark.parametrize("text", TRICKY)
def test_tricky_inputs(text: str) -> None:
    _same(text)


FRAGMENTS = st.sampled_from(
    [
        "SELECT", "select", "FROM", "Null", "TRUE", "x", "_y", "t1", "abc_def", "é", "٣", "²",
        "0", "42", "1.5", "1.", ".", "e", "E", "1e10", "2E-3", "3e+", "'s'", "'it''s'", "''",
        "'", '"q"', '"', "--c\n", "/*c*/", "/*", "-", "/", "*", "+", "%", ",", "(", ")", ";",
        "?", "=", "<", ">", "!", "<=", "<>", "!=", " ", "  ", "\n", "\t", "\r\n",
    ]
)  # fmt: skip


@settings(max_examples=3000, deadline=None)
@given(st.lists(FRAGMENTS, max_size=25).map("".join))
def test_sql_like_fragments(text: str) -> None:
    _same(text)


@settings(max_examples=3000, deadline=None)
@given(
    st.text(
        alphabet=st.sampled_from(
            list("abzAZ_09 \t\r\n'\"-/*+.,()?;=<>!eE%@") + ["²", "٣", "é", "İ", "K", chr(0xA0)]
        ),
        max_size=60,
    )
)
def test_tricky_alphabet(text: str) -> None:
    _same(text)


@settings(max_examples=2000, deadline=None)
@given(st.text(max_size=40))
def test_arbitrary_text(text: str) -> None:
    _same(text)


def test_large_insert() -> None:
    rows = ", ".join(f"({i}, 'name-{i}', {i * 1.25}, NULL, ?)" for i in range(2000))
    _same(f"INSERT INTO t (a, b, c, d, e) VALUES {rows}")
