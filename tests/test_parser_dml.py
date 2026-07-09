"""Tests del parser para `INSERT`, `UPDATE` y `DELETE`."""

from sql_query_parser.models import (
    BinaryOp,
    BinaryOperator,
    ColumnRef,
    DeleteStatement,
    InsertStatement,
    Literal,
    LiteralType,
    UpdateStatement,
)
from sql_query_parser.pipeline import parse


class TestInsert:
    def test_insert_with_explicit_columns(self) -> None:
        stmt = parse("INSERT INTO t (a, b) VALUES (1, 'x')")
        assert isinstance(stmt, InsertStatement)
        assert stmt.table == "t"
        assert stmt.columns == ("a", "b")
        assert stmt.rows == ((Literal(1, LiteralType.INTEGER), Literal("x", LiteralType.STRING)),)

    def test_insert_without_explicit_columns(self) -> None:
        stmt = parse("INSERT INTO t VALUES (1, 2)")
        assert isinstance(stmt, InsertStatement)
        assert stmt.columns == ()

    def test_insert_multiple_rows(self) -> None:
        stmt = parse("INSERT INTO t (a) VALUES (1), (2), (3)")
        assert isinstance(stmt, InsertStatement)
        assert len(stmt.rows) == 3
        assert stmt.rows[2] == (Literal(3, LiteralType.INTEGER),)

    def test_insert_with_expression_value(self) -> None:
        stmt = parse("INSERT INTO t (a) VALUES (1 + 2)")
        assert isinstance(stmt, InsertStatement)
        assert stmt.rows[0][0] == BinaryOp(
            Literal(1, LiteralType.INTEGER), BinaryOperator.ADD, Literal(2, LiteralType.INTEGER)
        )

    def test_insert_null_value(self) -> None:
        stmt = parse("INSERT INTO t (a) VALUES (NULL)")
        assert isinstance(stmt, InsertStatement)
        assert stmt.rows[0][0] == Literal(None, LiteralType.NULL)


class TestUpdate:
    def test_update_single_assignment(self) -> None:
        stmt = parse("UPDATE t SET a = 1")
        assert isinstance(stmt, UpdateStatement)
        assert stmt.table == "t"
        assert len(stmt.assignments) == 1
        assert stmt.assignments[0].column == "a"
        assert stmt.where is None

    def test_update_multiple_assignments_with_where(self) -> None:
        stmt = parse("UPDATE t SET a = 1, b = 2 WHERE id = 5")
        assert isinstance(stmt, UpdateStatement)
        assert [a.column for a in stmt.assignments] == ["a", "b"]
        assert stmt.where == BinaryOp(
            ColumnRef(name="id"), BinaryOperator.EQ, Literal(5, LiteralType.INTEGER)
        )

    def test_update_assignment_referencing_own_column(self) -> None:
        stmt = parse("UPDATE t SET balance = balance - 100")
        assert isinstance(stmt, UpdateStatement)
        assignment = stmt.assignments[0]
        assert assignment.value == BinaryOp(
            ColumnRef(name="balance"), BinaryOperator.SUB, Literal(100, LiteralType.INTEGER)
        )


class TestDelete:
    def test_delete_with_where(self) -> None:
        stmt = parse("DELETE FROM t WHERE id = 5")
        assert isinstance(stmt, DeleteStatement)
        assert stmt.table == "t"
        assert stmt.where is not None

    def test_delete_without_where(self) -> None:
        stmt = parse("DELETE FROM t")
        assert isinstance(stmt, DeleteStatement)
        assert stmt.where is None
