"""Placeholders posicionales `?` (parámetros de consulta estilo qmark)."""

from __future__ import annotations

import pytest

from sql_query_parser import SqlSyntaxError, parse, parse_many, tokenize
from sql_query_parser.models import (
    BinaryOp,
    DeleteStatement,
    InsertStatement,
    Literal,
    Placeholder,
    SelectStatement,
    TokenType,
    UpdateStatement,
)


class TestLexer:
    def test_question_mark_tokenizes_as_placeholder(self) -> None:
        tokens = tokenize("SELECT a FROM t WHERE b = ?")
        placeholder_tokens = [t for t in tokens if t.type is TokenType.PLACEHOLDER]
        assert len(placeholder_tokens) == 1
        assert placeholder_tokens[0].lexeme == "?"
        assert placeholder_tokens[0].position.column == 27

    def test_question_mark_inside_string_is_not_a_placeholder(self) -> None:
        tokens = tokenize("SELECT a FROM t WHERE b = '?'")
        assert not any(t.type is TokenType.PLACEHOLDER for t in tokens)


class TestParser:
    def test_placeholders_indexed_in_order_of_appearance(self) -> None:
        stmt = parse("SELECT a FROM t WHERE a = ? AND b < ?")
        assert isinstance(stmt, SelectStatement)
        where = stmt.where
        assert isinstance(where, BinaryOp)
        left, right = where.left, where.right
        assert isinstance(left, BinaryOp) and left.right == Placeholder(index=0)
        assert isinstance(right, BinaryOp) and right.right == Placeholder(index=1)

    def test_insert_values_with_placeholders(self) -> None:
        stmt = parse("INSERT INTO t (a, b) VALUES (?, ?), (?, 4)")
        assert isinstance(stmt, InsertStatement)
        assert stmt.rows[0] == (Placeholder(index=0), Placeholder(index=1))
        assert stmt.rows[1][0] == Placeholder(index=2)
        assert isinstance(stmt.rows[1][1], Literal) and stmt.rows[1][1].value == 4

    def test_update_and_delete_with_placeholders(self) -> None:
        update = parse("UPDATE t SET a = ? WHERE id = ?")
        assert isinstance(update, UpdateStatement)
        assert update.assignments[0].value == Placeholder(index=0)
        delete = parse("DELETE FROM t WHERE id = ?")
        assert isinstance(delete, DeleteStatement)

    def test_placeholder_inside_function_and_in_list(self) -> None:
        stmt = parse("SELECT COUNT(?) FROM t WHERE a IN (?, ?) AND b BETWEEN ? AND ?")
        assert isinstance(stmt, SelectStatement)
        # 5 placeholders en total, indexados 0..4 en orden de aparición.
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
        assert found == [0, 1, 2, 3, 4]

    def test_indices_reset_per_statement_in_parse_many(self) -> None:
        statements = parse_many("SELECT a FROM t WHERE a = ?; SELECT b FROM t WHERE b = ?;")
        for stmt in statements:
            assert isinstance(stmt, SelectStatement)
            assert isinstance(stmt.where, BinaryOp)
            assert stmt.where.right == Placeholder(index=0)

    def test_placeholder_not_valid_as_table_name(self) -> None:
        with pytest.raises(SqlSyntaxError):
            parse("SELECT a FROM ?")
