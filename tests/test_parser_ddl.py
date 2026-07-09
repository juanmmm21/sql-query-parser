"""Tests del parser para `CREATE TABLE` y `CREATE INDEX`."""

from sql_query_parser.models import CreateIndexStatement, CreateTableStatement
from sql_query_parser.pipeline import parse


class TestCreateTable:
    def test_minimal_table(self) -> None:
        stmt = parse("CREATE TABLE t (id INTEGER)")
        assert isinstance(stmt, CreateTableStatement)
        assert stmt.table == "t"
        assert stmt.if_not_exists is False
        assert len(stmt.columns) == 1
        assert stmt.columns[0].data_type.name == "INTEGER"
        assert stmt.columns[0].data_type.parameters == ()

    def test_if_not_exists(self) -> None:
        stmt = parse("CREATE TABLE IF NOT EXISTS t (id INTEGER)")
        assert isinstance(stmt, CreateTableStatement)
        assert stmt.if_not_exists is True

    def test_column_constraints(self) -> None:
        stmt = parse(
            "CREATE TABLE t ("
            "id INTEGER PRIMARY KEY, "
            "email VARCHAR(255) UNIQUE NOT NULL, "
            "age INTEGER DEFAULT 18"
            ")"
        )
        assert isinstance(stmt, CreateTableStatement)
        id_col, email_col, age_col = stmt.columns

        assert id_col.primary_key is True

        assert email_col.data_type.parameters == (255,)
        assert email_col.unique is True
        assert email_col.not_null is True

        assert age_col.default is not None

    def test_decimal_with_two_parameters(self) -> None:
        stmt = parse("CREATE TABLE t (price DECIMAL(10, 2))")
        assert isinstance(stmt, CreateTableStatement)
        assert stmt.columns[0].data_type.parameters == (10, 2)

    def test_constraint_order_is_irrelevant(self) -> None:
        stmt = parse("CREATE TABLE t (a INTEGER NOT NULL UNIQUE)")
        assert isinstance(stmt, CreateTableStatement)
        column = stmt.columns[0]
        assert column.not_null is True
        assert column.unique is True

    def test_multiple_columns(self) -> None:
        stmt = parse("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, active BOOLEAN)")
        assert isinstance(stmt, CreateTableStatement)
        assert [c.name for c in stmt.columns] == ["id", "name", "active"]


class TestCreateIndex:
    def test_simple_index(self) -> None:
        stmt = parse("CREATE INDEX idx_name ON t (name)")
        assert isinstance(stmt, CreateIndexStatement)
        assert stmt.index_name == "idx_name"
        assert stmt.table == "t"
        assert stmt.columns == ("name",)
        assert stmt.unique is False
        assert stmt.if_not_exists is False

    def test_unique_index(self) -> None:
        stmt = parse("CREATE UNIQUE INDEX idx_email ON users (email)")
        assert isinstance(stmt, CreateIndexStatement)
        assert stmt.unique is True

    def test_multi_column_index(self) -> None:
        stmt = parse("CREATE INDEX idx_composite ON t (a, b, c)")
        assert isinstance(stmt, CreateIndexStatement)
        assert stmt.columns == ("a", "b", "c")

    def test_index_if_not_exists(self) -> None:
        stmt = parse("CREATE INDEX IF NOT EXISTS idx_name ON t (name)")
        assert isinstance(stmt, CreateIndexStatement)
        assert stmt.if_not_exists is True
