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


class TestTableConstraints:
    """Restricciones a nivel de tabla: la forma que generan los ORMs."""

    def test_table_level_primary_key(self) -> None:
        from sql_query_parser.models import TableConstraint, TableConstraintKind

        stmt = parse(
            "CREATE TABLE users (\n\tid INTEGER NOT NULL, \n\tname VARCHAR(50), "
            "\n\tPRIMARY KEY (id)\n)"
        )
        assert isinstance(stmt, CreateTableStatement)
        assert [c.name for c in stmt.columns] == ["id", "name"]
        assert stmt.constraints == (
            TableConstraint(kind=TableConstraintKind.PRIMARY_KEY, columns=("id",)),
        )

    def test_named_and_multi_column_constraints(self) -> None:
        from sql_query_parser.models import TableConstraint, TableConstraintKind

        stmt = parse(
            "CREATE TABLE t (a INTEGER, b INTEGER, c TEXT, "
            "CONSTRAINT t_pk PRIMARY KEY (a, b), UNIQUE (c), CONSTRAINT uq UNIQUE (b, c))"
        )
        assert isinstance(stmt, CreateTableStatement)
        assert stmt.constraints == (
            TableConstraint(TableConstraintKind.PRIMARY_KEY, ("a", "b"), "t_pk"),
            TableConstraint(TableConstraintKind.UNIQUE, ("c",), None),
            TableConstraint(TableConstraintKind.UNIQUE, ("b", "c"), "uq"),
        )

    def test_constraint_is_contextual_not_reserved(self) -> None:
        # Una columna puede seguir llamándose "constraint".
        stmt = parse("CREATE TABLE t (constraint INTEGER, x TEXT)")
        assert isinstance(stmt, CreateTableStatement)
        assert [c.name for c in stmt.columns] == ["constraint", "x"]
        assert stmt.constraints == ()

    def test_column_after_table_constraint_is_rejected(self) -> None:
        import pytest

        from sql_query_parser import SqlSyntaxError

        with pytest.raises(SqlSyntaxError, match="table constraint"):
            parse("CREATE TABLE t (a INTEGER, PRIMARY KEY (a), b INTEGER)")

    def test_constraints_only_is_rejected(self) -> None:
        import pytest

        from sql_query_parser import SqlSyntaxError

        with pytest.raises(SqlSyntaxError, match="column definition"):
            parse("CREATE TABLE t (PRIMARY KEY (a))")

    def test_default_constraints_field_is_empty(self) -> None:
        stmt = parse("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        assert isinstance(stmt, CreateTableStatement)
        assert stmt.constraints == ()
