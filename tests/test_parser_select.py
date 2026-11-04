"""Tests del parser para `SELECT`: WHERE, JOIN, GROUP BY, ORDER BY, LIMIT."""

import pytest

from sql_query_parser.errors import SqlSyntaxError, UnsupportedFeatureError
from sql_query_parser.models import (
    BinaryOp,
    BinaryOperator,
    ColumnRef,
    FunctionCall,
    JoinType,
    Like,
    Literal,
    LiteralType,
    OrderDirection,
    SelectStatement,
    Star,
)
from sql_query_parser.pipeline import parse


def _select(sql: str) -> SelectStatement:
    statement = parse(sql)
    assert isinstance(statement, SelectStatement)
    return statement


class TestBasicSelect:
    def test_select_star(self) -> None:
        stmt = _select("SELECT * FROM t")
        assert len(stmt.columns) == 1
        assert isinstance(stmt.columns[0].expression, Star)
        assert stmt.from_table.name == "t"
        assert stmt.from_table.alias is None

    def test_select_columns_with_and_without_alias(self) -> None:
        stmt = _select("SELECT a, b AS x, c y FROM t")
        assert [item.alias for item in stmt.columns] == [None, "x", "y"]
        assert [item.expression for item in stmt.columns] == [
            ColumnRef(name="a"),
            ColumnRef(name="b"),
            ColumnRef(name="c"),
        ]

    def test_qualified_column_reference(self) -> None:
        stmt = _select("SELECT t.a FROM t")
        assert stmt.columns[0].expression == ColumnRef(name="a", table="t")

    def test_distinct(self) -> None:
        stmt = _select("SELECT DISTINCT a FROM t")
        assert stmt.distinct is True

    def test_table_alias(self) -> None:
        stmt = _select("SELECT * FROM my_table AS m")
        assert stmt.from_table.alias == "m"

    def test_table_alias_without_as(self) -> None:
        stmt = _select("SELECT * FROM my_table m")
        assert stmt.from_table.alias == "m"


class TestWhereClause:
    def test_where_simple_comparison(self) -> None:
        stmt = _select("SELECT * FROM t WHERE a = 1")
        assert stmt.where == BinaryOp(
            ColumnRef(name="a"), BinaryOperator.EQ, Literal(1, LiteralType.INTEGER)
        )

    def test_where_like(self) -> None:
        stmt = _select("SELECT * FROM t WHERE name LIKE 'A%'")
        assert stmt.where == Like(
            ColumnRef(name="name"), Literal("A%", LiteralType.STRING), negated=False
        )

    def test_where_not_like(self) -> None:
        stmt = _select("SELECT * FROM t WHERE name NOT LIKE 'A%'")
        where = stmt.where
        assert isinstance(where, Like)
        assert where.negated is True

    def test_where_absent_is_none(self) -> None:
        stmt = _select("SELECT * FROM t")
        assert stmt.where is None


class TestJoins:
    def test_default_join_is_inner(self) -> None:
        stmt = _select("SELECT * FROM a JOIN b ON a.id = b.id")
        assert len(stmt.joins) == 1
        assert stmt.joins[0].join_type is JoinType.INNER

    def test_explicit_inner_join(self) -> None:
        stmt = _select("SELECT * FROM a INNER JOIN b ON a.id = b.id")
        assert stmt.joins[0].join_type is JoinType.INNER

    @pytest.mark.parametrize(
        ("keyword", "expected"),
        [
            ("LEFT", JoinType.LEFT),
            ("LEFT OUTER", JoinType.LEFT),
            ("RIGHT", JoinType.RIGHT),
            ("RIGHT OUTER", JoinType.RIGHT),
            ("FULL", JoinType.FULL),
            ("FULL OUTER", JoinType.FULL),
        ],
    )
    def test_join_type_keywords(self, keyword: str, expected: JoinType) -> None:
        stmt = _select(f"SELECT * FROM a {keyword} JOIN b ON a.id = b.id")
        assert stmt.joins[0].join_type is expected

    def test_multiple_joins(self) -> None:
        stmt = _select("SELECT * FROM a JOIN b ON a.id = b.id LEFT JOIN c ON b.id = c.id")
        assert len(stmt.joins) == 2
        assert stmt.joins[0].table.name == "b"
        assert stmt.joins[1].table.name == "c"
        assert stmt.joins[1].join_type is JoinType.LEFT


class TestGroupByHavingOrderByLimit:
    def test_group_by_multiple_expressions(self) -> None:
        stmt = _select("SELECT a, b FROM t GROUP BY a, b")
        assert stmt.group_by == (ColumnRef(name="a"), ColumnRef(name="b"))

    def test_having(self) -> None:
        stmt = _select("SELECT a, COUNT(*) FROM t GROUP BY a HAVING COUNT(*) > 1")
        assert isinstance(stmt.having, BinaryOp)
        assert stmt.having.operator is BinaryOperator.GT

    def test_order_by_default_direction_is_asc(self) -> None:
        stmt = _select("SELECT a FROM t ORDER BY a")
        assert stmt.order_by[0].direction is OrderDirection.ASC

    def test_order_by_desc(self) -> None:
        stmt = _select("SELECT a FROM t ORDER BY a DESC")
        assert stmt.order_by[0].direction is OrderDirection.DESC

    def test_order_by_multiple_keys_mixed_direction(self) -> None:
        stmt = _select("SELECT a, b FROM t ORDER BY a DESC, b ASC")
        assert [item.direction for item in stmt.order_by] == [
            OrderDirection.DESC,
            OrderDirection.ASC,
        ]

    def test_limit_only(self) -> None:
        stmt = _select("SELECT * FROM t LIMIT 10")
        assert stmt.limit == 10
        assert stmt.offset is None

    def test_limit_with_offset(self) -> None:
        stmt = _select("SELECT * FROM t LIMIT 10 OFFSET 20")
        assert stmt.limit == 10
        assert stmt.offset == 20

    def test_full_clause_order(self) -> None:
        stmt = _select(
            "SELECT a FROM t WHERE a > 0 GROUP BY a HAVING a < 100 ORDER BY a LIMIT 5 OFFSET 1"
        )
        assert stmt.where is not None
        assert stmt.group_by
        assert stmt.having is not None
        assert stmt.order_by
        assert stmt.limit == 5
        assert stmt.offset == 1


class TestFunctionCallsInSelect:
    def test_count_star(self) -> None:
        stmt = _select("SELECT COUNT(*) FROM t")
        expr = stmt.columns[0].expression
        assert isinstance(expr, FunctionCall)
        assert expr.name == "COUNT"
        assert expr.star_argument is True

    def test_aggregate_with_distinct(self) -> None:
        stmt = _select("SELECT COUNT(DISTINCT a) FROM t")
        expr = stmt.columns[0].expression
        assert isinstance(expr, FunctionCall)
        assert expr.distinct is True
        assert expr.arguments == (ColumnRef(name="a"),)


class TestExplicitlyExcludedConstructs:
    def test_derived_table_in_from_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFeatureError):
            parse("SELECT * FROM (SELECT id FROM other) d")

    def test_derived_table_in_join_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFeatureError):
            parse("SELECT * FROM t JOIN (SELECT id FROM other) d ON t.id = d.id")

    def test_union_is_a_syntax_error(self) -> None:
        with pytest.raises(SqlSyntaxError):
            parse("SELECT a FROM t UNION SELECT b FROM u")

    def test_with_cte_is_a_syntax_error(self) -> None:
        with pytest.raises(SqlSyntaxError):
            parse("WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_case_expression_is_a_syntax_error(self) -> None:
        with pytest.raises(SqlSyntaxError):
            parse("SELECT CASE WHEN a = 1 THEN 'x' ELSE 'y' END FROM t")


class TestAllQuantifier:
    """`ALL` es el cuantificador por defecto: `SELECT ALL` y `COUNT(ALL x)`
    equivalen a no escribirlo. Antes `ALL` era un identificador y
    `SELECT ALL - x FROM t` se parseaba como `"ALL" - x`."""

    def test_select_all_is_the_default_quantifier(self) -> None:
        from sql_query_parser.models import SelectStatement

        explicit = parse("SELECT ALL - x AS y FROM t")
        implicit = parse("SELECT - x AS y FROM t")
        assert isinstance(explicit, SelectStatement)
        assert explicit == implicit
        assert explicit.distinct is False

    def test_aggregate_all_argument(self) -> None:
        assert parse("SELECT COUNT(ALL x) FROM t") == parse("SELECT COUNT(x) FROM t")

    def test_all_is_reserved(self) -> None:
        import pytest

        from sql_query_parser import SqlSyntaxError

        with pytest.raises(SqlSyntaxError):
            parse("SELECT all FROM t")


class TestCommaAndCrossJoins:
    """`FROM a, b` y `CROSS JOIN` son `INNER JOIN ... ON TRUE`."""

    def test_comma_join_is_inner_join_on_true(self) -> None:
        assert parse("SELECT * FROM a, b AS x WHERE a.k = x.k") == parse(
            "SELECT * FROM a INNER JOIN b AS x ON TRUE WHERE a.k = x.k"
        )

    def test_cross_join(self) -> None:
        assert parse("SELECT * FROM a CROSS JOIN b") == parse("SELECT * FROM a JOIN b ON TRUE")

    def test_mixed_chain_keeps_written_order(self) -> None:
        from sql_query_parser.models import JoinType, SelectStatement

        stmt = parse("SELECT * FROM a, b LEFT JOIN c ON b.k = c.k, d")
        assert isinstance(stmt, SelectStatement)
        assert [(j.join_type, j.table.name) for j in stmt.joins] == [
            (JoinType.INNER, "b"),
            (JoinType.LEFT, "c"),
            (JoinType.INNER, "d"),
        ]

    def test_cross_join_does_not_take_on(self) -> None:
        with pytest.raises(SqlSyntaxError):
            parse("SELECT * FROM a CROSS JOIN b ON a.k = b.k")
