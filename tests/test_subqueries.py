"""Subconsultas en posición de expresión: `(SELECT ...)` escalar e
`[NOT] IN (SELECT ...)`."""

from __future__ import annotations

import pytest

from sql_query_parser.errors import SqlSyntaxError
from sql_query_parser.models import (
    BinaryOp,
    BinaryOperator,
    ColumnRef,
    FunctionCall,
    InList,
    InSubquery,
    Placeholder,
    SelectStatement,
    Subquery,
)
from sql_query_parser.pipeline import parse


def _select(sql: str) -> SelectStatement:
    statement = parse(sql)
    assert isinstance(statement, SelectStatement)
    return statement


class TestScalarSubquery:
    def test_scalar_subquery_in_where_comparison(self) -> None:
        stmt = _select("SELECT a FROM t WHERE v > (SELECT AVG(v) FROM m)")
        where = stmt.where
        assert isinstance(where, BinaryOp)
        assert where.operator is BinaryOperator.GT
        assert isinstance(where.right, Subquery)
        inner = where.right.statement
        assert inner.from_table.name == "m"
        assert isinstance(inner.columns[0].expression, FunctionCall)

    def test_scalar_subquery_in_select_list_with_alias(self) -> None:
        stmt = _select("SELECT (SELECT MAX(v) FROM m) AS mx, a FROM t")
        assert stmt.columns[0].alias == "mx"
        assert isinstance(stmt.columns[0].expression, Subquery)

    def test_subquery_keeps_its_own_clauses(self) -> None:
        stmt = _select(
            "SELECT a FROM t WHERE v > (SELECT v FROM m WHERE k = 1 ORDER BY v DESC LIMIT 1)"
        )
        where = stmt.where
        assert isinstance(where, BinaryOp)
        assert isinstance(where.right, Subquery)
        inner = where.right.statement
        # ORDER BY y LIMIT pertenecen a la subconsulta, no a la externa.
        assert inner.limit == 1
        assert len(inner.order_by) == 1
        assert stmt.limit is None
        assert stmt.order_by == ()

    def test_scalar_subquery_participates_in_arithmetic(self) -> None:
        stmt = _select("SELECT a FROM t WHERE v > (SELECT AVG(v) FROM m) * 2")
        where = stmt.where
        assert isinstance(where, BinaryOp)
        product = where.right
        assert isinstance(product, BinaryOp)
        assert product.operator is BinaryOperator.MUL
        assert isinstance(product.left, Subquery)


class TestInSubquery:
    def test_in_subquery(self) -> None:
        stmt = _select("SELECT a FROM t WHERE id IN (SELECT id FROM u)")
        where = stmt.where
        assert isinstance(where, InSubquery)
        assert where.operand == ColumnRef(name="id")
        assert where.negated is False
        assert where.subquery.statement.from_table.name == "u"

    def test_not_in_subquery(self) -> None:
        stmt = _select("SELECT a FROM t WHERE id NOT IN (SELECT id FROM u)")
        assert isinstance(stmt.where, InSubquery)
        assert stmt.where.negated is True

    def test_parenthesized_scalar_subquery_in_list_is_not_in_subquery(self) -> None:
        # `IN ((SELECT ...))` es una lista cuyo único elemento es una
        # subconsulta ESCALAR — semántica distinta de `IN (SELECT ...)`.
        stmt = _select("SELECT a FROM t WHERE id IN ((SELECT id FROM u))")
        where = stmt.where
        assert isinstance(where, InList)
        assert len(where.values) == 1
        assert isinstance(where.values[0], Subquery)

    def test_in_subquery_with_where_inside(self) -> None:
        stmt = _select("SELECT a FROM t WHERE id IN (SELECT id FROM u WHERE active = TRUE)")
        where = stmt.where
        assert isinstance(where, InSubquery)
        assert where.subquery.statement.where is not None


class TestNestedSubqueries:
    def test_subquery_inside_subquery(self) -> None:
        stmt = _select(
            "SELECT a FROM t WHERE id IN (SELECT id FROM u WHERE v > (SELECT AVG(v) FROM w))"
        )
        outer_in = stmt.where
        assert isinstance(outer_in, InSubquery)
        inner_where = outer_in.subquery.statement.where
        assert isinstance(inner_where, BinaryOp)
        assert isinstance(inner_where.right, Subquery)
        assert inner_where.right.statement.from_table.name == "w"


class TestPlaceholdersInSubqueries:
    def test_placeholders_share_the_outer_statement_counter(self) -> None:
        stmt = _select(
            "SELECT a FROM t WHERE a = ? AND id IN (SELECT id FROM u WHERE b = ?) AND c = ?"
        )
        found: list[int] = []

        def walk(node: object) -> None:
            if isinstance(node, Placeholder):
                found.append(node.index)
            elif hasattr(node, "__dataclass_fields__"):
                for name in node.__dataclass_fields__:  # type: ignore[attr-defined]
                    value = getattr(node, name)
                    values = value if isinstance(value, tuple) else (value,)
                    for child in values:
                        walk(child)

        walk(stmt)
        # Numeración única por sentencia externa, en orden de aparición,
        # atravesando la subconsulta sin reiniciarse.
        assert found == [0, 1, 2]


class TestSubquerySyntaxErrors:
    def test_unclosed_subquery_paren(self) -> None:
        with pytest.raises(SqlSyntaxError):
            parse("SELECT a FROM t WHERE id IN (SELECT id FROM u")

    def test_empty_subquery_select_list(self) -> None:
        with pytest.raises(SqlSyntaxError):
            parse("SELECT a FROM t WHERE id IN (SELECT FROM u)")

    def test_union_inside_subquery_is_still_a_syntax_error(self) -> None:
        with pytest.raises(SqlSyntaxError):
            parse("SELECT a FROM t WHERE id IN (SELECT a FROM u UNION SELECT b FROM v)")

    def test_exists_is_still_a_syntax_error(self) -> None:
        # EXISTS es keyword del lexer (por IF NOT EXISTS) y no arranca
        # ninguna expresión primaria: queda fuera del subconjunto.
        with pytest.raises(SqlSyntaxError):
            parse("SELECT a FROM t WHERE EXISTS (SELECT 1 FROM u)")
