"""El camino rápido de `Parser.parse_expression` (literal, `?` o
identificador seguido de `,` o `)`) produce exactamente el mismo AST — o el
mismo error — que recorrer la cadena de precedencia completa."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from sql_query_parser.models import Expression, Statement
from sql_query_parser.parser import Parser
from sql_query_parser.pipeline import tokenize


class ReferenceParser(Parser):
    def parse_expression(self) -> Expression:
        return self._parse_or()


def _outcome(parser_class: type[Parser], sql: str) -> Statement | tuple[str, str]:
    try:
        return parser_class(tokenize(sql)).parse_single_statement()
    except Exception as exc:
        return (type(exc).__name__, str(exc))


def _same(sql: str) -> None:
    assert _outcome(Parser, sql) == _outcome(ReferenceParser, sql), sql


ATOMS = st.sampled_from(
    ["1", "-1", "2.5", "'x'", "''", "NULL", "TRUE", "false", "?", "a", "t.a", '"q"', "*",
     "f(a)", "COUNT(*)", "(1)", "(?)", "a + 1", "? * 2", "NOT a", "a IS NULL", "a = ?",
     "a BETWEEN 1 AND ?", "a IN (1, ?, b)", "a LIKE 'x%'", "-?", "+a", "1 - -2"]
)  # fmt: skip
LISTS = st.lists(ATOMS, min_size=1, max_size=6).map(", ".join)


@settings(max_examples=1500, deadline=None)
@given(st.lists(LISTS, min_size=1, max_size=4))
def test_insert_values(rows: list[str]) -> None:
    _same("INSERT INTO t VALUES " + ", ".join(f"({row})" for row in rows))


@settings(max_examples=1500, deadline=None)
@given(LISTS, LISTS)
def test_select_lists_function_arguments_and_in_lists(items: str, values: str) -> None:
    _same(f"SELECT {items} FROM t WHERE x IN ({values}) AND g({values}) = ?")


@settings(max_examples=1500, deadline=None)
@given(st.lists(st.sampled_from(["1", "?", "a", ",", "(", ")", "+", "NOT", "IS", "NULL"]),
                max_size=12).map(" ".join))  # fmt: skip
def test_malformed_lists_fail_the_same_way(body: str) -> None:
    _same(f"INSERT INTO t VALUES ({body})")


def test_placeholder_numbering_is_preserved() -> None:
    sql = "INSERT INTO t VALUES (?, ?, ? + 1), (?, f(?), ?)"
    assert _outcome(Parser, sql) == _outcome(ReferenceParser, sql)
