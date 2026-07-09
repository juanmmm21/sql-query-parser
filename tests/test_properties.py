"""Tests de propiedades: generación aleatoria (semilla fija) de SQL
sintácticamente válido, construyendo en paralelo el AST que se espera que
produzca el parser, y comprobando `parse(sql) == expected` para cientos de
queries por semilla.

El generador es, en sí mismo, el modelo de referencia independiente: no
llama al lexer ni al parser en ningún momento, construye la cadena SQL y el
nodo de AST esperado a partir de las mismas elecciones aleatorias, por dos
caminos de código completamente separados de `sql_query_parser.parser`.

Para eliminar cualquier ambigüedad de precedencia en el texto SQL generado
(que rompería la comparación de árboles si el generador y el parser no
coincidieran en asociatividad para una forma de árbol arbitraria), toda
subexpresión compuesta (`BinaryOp`/`UnaryOp`/`Like`/`Between`/`InList`/
`IsNull`) se envuelve en paréntesis explícitos al anidarla — un paréntesis
de más nunca cambia el AST resultante (`primary()` lo desenvuelve), así que
esto es siempre seguro, nunca sólo "normalmente correcto".
"""

import random

import pytest

from sql_query_parser.models import (
    Assignment,
    Between,
    BinaryOp,
    BinaryOperator,
    ColumnDefinition,
    ColumnRef,
    CreateIndexStatement,
    CreateTableStatement,
    DataType,
    DeleteStatement,
    Expression,
    FunctionCall,
    InList,
    InsertStatement,
    IsNull,
    Like,
    Literal,
    LiteralType,
    OrderByItem,
    OrderDirection,
    SelectItem,
    SelectStatement,
    Star,
    Statement,
    TableRef,
    UnaryOp,
    UnaryOperator,
    UpdateStatement,
)
from sql_query_parser.pipeline import parse

_TABLE_POOL = ["users", "orders", "products", "accounts"]
_COLUMN_POOL = ["id", "name", "age", "price", "email", "active", "score", "created_at"]
_STRING_POOL = ["alice", "bob", "widget", "north", "pending"]

_ARITHMETIC_OPERATORS: list[tuple[str, BinaryOperator]] = [
    ("+", BinaryOperator.ADD),
    ("-", BinaryOperator.SUB),
    ("*", BinaryOperator.MUL),
    ("/", BinaryOperator.DIV),
    ("%", BinaryOperator.MOD),
]

_COMPARISON_OPERATORS: list[tuple[str, BinaryOperator]] = [
    ("=", BinaryOperator.EQ),
    ("<>", BinaryOperator.NEQ),
    ("<", BinaryOperator.LT),
    ("<=", BinaryOperator.LE),
    (">", BinaryOperator.GT),
    (">=", BinaryOperator.GE),
]

_COMPOUND_EXPRESSION_TYPES = (BinaryOp, UnaryOp, Like, Between, InList, IsNull)


def _paren(sql: str, node: Expression) -> str:
    """Envuelve `sql` en paréntesis si `node` es una expresión compuesta.
    Ver docstring del módulo: siempre seguro, ambigüedad cero."""
    if isinstance(node, _COMPOUND_EXPRESSION_TYPES):
        return f"({sql})"
    return sql


def _gen_literal(rng: random.Random) -> tuple[str, Expression]:
    # Rango no negativo a propósito: el lexer no tokeniza el signo como
    # parte del literal numérico (es el operador unario del parser el que
    # lo añade), así que un literal "negativo" aquí rompería la
    # correspondencia 1:1 entre SQL generado y AST esperado.
    kind = rng.choice(["int", "float", "string", "bool", "null"])
    if kind == "int":
        value = rng.randint(0, 1000)
        return str(value), Literal(value, LiteralType.INTEGER)
    if kind == "float":
        value = round(rng.uniform(0, 1000), 3)
        return repr(value), Literal(value, LiteralType.FLOAT)
    if kind == "string":
        value = rng.choice(_STRING_POOL)
        return f"'{value}'", Literal(value, LiteralType.STRING)
    if kind == "bool":
        flag = rng.choice([True, False])
        return ("TRUE" if flag else "FALSE"), Literal(flag, LiteralType.BOOLEAN)
    return "NULL", Literal(None, LiteralType.NULL)


def _gen_column(rng: random.Random) -> tuple[str, Expression]:
    name = rng.choice(_COLUMN_POOL)
    return name, ColumnRef(name=name)


def _gen_leaf(rng: random.Random) -> tuple[str, Expression]:
    roll = rng.random()
    if roll < 0.45:
        return _gen_literal(rng)
    if roll < 0.9:
        return _gen_column(rng)
    return "COUNT(*)", FunctionCall(name="COUNT", star_argument=True)


def _gen_arithmetic(rng: random.Random, depth: int) -> tuple[str, Expression]:
    if depth <= 0 or rng.random() < 0.35:
        if rng.random() < 0.15:
            leaf_sql, leaf_node = _gen_leaf(rng)
            return f"-{leaf_sql}", UnaryOp(UnaryOperator.NEG, leaf_node)
        return _gen_leaf(rng)
    left_sql, left_node = _gen_arithmetic(rng, depth - 1)
    right_sql, right_node = _gen_arithmetic(rng, depth - 1)
    op_text, op = rng.choice(_ARITHMETIC_OPERATORS)
    sql = f"{_paren(left_sql, left_node)} {op_text} {_paren(right_sql, right_node)}"
    return sql, BinaryOp(left_node, op, right_node)


def _gen_comparison(rng: random.Random, depth: int) -> tuple[str, Expression]:
    left_sql, left_node = _gen_arithmetic(rng, depth)
    right_sql, right_node = _gen_arithmetic(rng, depth)
    op_text, op = rng.choice(_COMPARISON_OPERATORS)
    sql = f"{_paren(left_sql, left_node)} {op_text} {_paren(right_sql, right_node)}"
    return sql, BinaryOp(left_node, op, right_node)


def _gen_boolean(rng: random.Random, depth: int) -> tuple[str, Expression]:
    if depth <= 0 or rng.random() < 0.35:
        return _gen_comparison(rng, max(depth - 1, 0))
    choice = rng.random()
    if choice < 0.4:
        left_sql, left_node = _gen_boolean(rng, depth - 1)
        right_sql, right_node = _gen_boolean(rng, depth - 1)
        sql = f"{_paren(left_sql, left_node)} AND {_paren(right_sql, right_node)}"
        return sql, BinaryOp(left_node, BinaryOperator.AND, right_node)
    if choice < 0.8:
        left_sql, left_node = _gen_boolean(rng, depth - 1)
        right_sql, right_node = _gen_boolean(rng, depth - 1)
        sql = f"{_paren(left_sql, left_node)} OR {_paren(right_sql, right_node)}"
        return sql, BinaryOp(left_node, BinaryOperator.OR, right_node)
    operand_sql, operand_node = _gen_boolean(rng, depth - 1)
    sql = f"NOT {_paren(operand_sql, operand_node)}"
    return sql, UnaryOp(UnaryOperator.NOT, operand_node)


def _gen_select(rng: random.Random) -> tuple[str, Statement]:
    table = rng.choice(_TABLE_POOL)
    use_star = rng.random() < 0.15

    order_candidates: list[str]
    if use_star:
        columns_sql: list[str] = ["*"]
        columns_ast: list[SelectItem] = [SelectItem(expression=Star())]
        order_candidates = []
    else:
        names = rng.sample(_COLUMN_POOL, rng.randint(1, 3))
        columns_sql = list(names)
        columns_ast = [SelectItem(expression=ColumnRef(name=name)) for name in names]
        order_candidates = names

    where_sql: str | None = None
    where_ast: Expression | None = None
    if rng.random() < 0.7:
        where_sql, where_ast = _gen_boolean(rng, depth=2)

    order_by_ast: tuple[OrderByItem, ...] = ()
    order_by_sql = ""
    if order_candidates and rng.random() < 0.5:
        column = rng.choice(order_candidates)
        descending = rng.random() < 0.5
        direction = OrderDirection.DESC if descending else OrderDirection.ASC
        order_by_ast = (OrderByItem(expression=ColumnRef(name=column), direction=direction),)
        order_by_sql = f" ORDER BY {column}{' DESC' if descending else ''}"

    limit_ast: int | None = None
    limit_sql = ""
    if rng.random() < 0.5:
        limit_ast = rng.randint(1, 500)
        limit_sql = f" LIMIT {limit_ast}"

    sql = f"SELECT {', '.join(columns_sql)} FROM {table}"
    if where_sql is not None:
        sql += f" WHERE {where_sql}"
    sql += order_by_sql
    sql += limit_sql

    expected = SelectStatement(
        columns=tuple(columns_ast),
        from_table=TableRef(name=table),
        where=where_ast,
        order_by=order_by_ast,
        limit=limit_ast,
    )
    return sql, expected


def _gen_insert(rng: random.Random) -> tuple[str, Statement]:
    table = rng.choice(_TABLE_POOL)
    columns = rng.sample(_COLUMN_POOL, rng.randint(1, 3))
    rows_sql: list[str] = []
    rows_ast: list[tuple[Expression, ...]] = []
    for _ in range(rng.randint(1, 3)):
        row_sql: list[str] = []
        row_ast: list[Expression] = []
        for _ in columns:
            literal_sql, literal_node = _gen_literal(rng)
            row_sql.append(literal_sql)
            row_ast.append(literal_node)
        rows_sql.append(f"({', '.join(row_sql)})")
        rows_ast.append(tuple(row_ast))

    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES {', '.join(rows_sql)}"
    expected = InsertStatement(table=table, columns=tuple(columns), rows=tuple(rows_ast))
    return sql, expected


def _gen_update(rng: random.Random) -> tuple[str, Statement]:
    table = rng.choice(_TABLE_POOL)
    columns = rng.sample(_COLUMN_POOL, rng.randint(1, 3))
    assignments_sql: list[str] = []
    assignments_ast: list[Assignment] = []
    for column in columns:
        literal_sql, literal_node = _gen_literal(rng)
        assignments_sql.append(f"{column} = {literal_sql}")
        assignments_ast.append(Assignment(column=column, value=literal_node))

    where_sql: str | None = None
    where_ast: Expression | None = None
    if rng.random() < 0.7:
        where_sql, where_ast = _gen_boolean(rng, depth=2)

    sql = f"UPDATE {table} SET {', '.join(assignments_sql)}"
    if where_sql is not None:
        sql += f" WHERE {where_sql}"

    expected = UpdateStatement(table=table, assignments=tuple(assignments_ast), where=where_ast)
    return sql, expected


def _gen_delete(rng: random.Random) -> tuple[str, Statement]:
    table = rng.choice(_TABLE_POOL)
    where_sql: str | None = None
    where_ast: Expression | None = None
    if rng.random() < 0.7:
        where_sql, where_ast = _gen_boolean(rng, depth=2)

    sql = f"DELETE FROM {table}"
    if where_sql is not None:
        sql += f" WHERE {where_sql}"

    expected = DeleteStatement(table=table, where=where_ast)
    return sql, expected


_SIMPLE_DATA_TYPES = ["INTEGER", "TEXT", "BOOLEAN", "DATE"]


def _gen_create_table(rng: random.Random) -> tuple[str, Statement]:
    table = rng.choice(_TABLE_POOL)
    names = rng.sample(_COLUMN_POOL, rng.randint(1, 4))
    columns_sql: list[str] = []
    columns_ast: list[ColumnDefinition] = []
    for name in names:
        type_name = rng.choice(_SIMPLE_DATA_TYPES)
        not_null = rng.random() < 0.4
        column_sql = f"{name} {type_name}"
        if not_null:
            column_sql += " NOT NULL"
        columns_sql.append(column_sql)
        columns_ast.append(
            ColumnDefinition(name=name, data_type=DataType(name=type_name), not_null=not_null)
        )

    sql = f"CREATE TABLE {table} ({', '.join(columns_sql)})"
    expected = CreateTableStatement(table=table, columns=tuple(columns_ast))
    return sql, expected


def _gen_create_index(rng: random.Random) -> tuple[str, Statement]:
    table = rng.choice(_TABLE_POOL)
    index_name = f"idx_{table}_{rng.randint(1, 9999)}"
    columns = rng.sample(_COLUMN_POOL, rng.randint(1, 3))
    unique = rng.random() < 0.5
    prefix = "CREATE UNIQUE INDEX" if unique else "CREATE INDEX"

    sql = f"{prefix} {index_name} ON {table} ({', '.join(columns)})"
    expected = CreateIndexStatement(
        index_name=index_name, table=table, columns=tuple(columns), unique=unique
    )
    return sql, expected


_STATEMENT_GENERATORS = [
    _gen_select,
    _gen_insert,
    _gen_update,
    _gen_delete,
    _gen_create_table,
    _gen_create_index,
]


class TestRandomValidQueriesMatchIndependentModel:
    @pytest.mark.parametrize("seed", [1, 42, 12345, 987654321])
    def test_random_statements_parse_to_expected_ast(self, seed: int) -> None:
        rng = random.Random(seed)
        for iteration in range(300):
            generator = rng.choice(_STATEMENT_GENERATORS)
            sql, expected = generator(rng)
            actual = parse(sql)
            assert actual == expected, (
                f"seed={seed} iteration={iteration} generator={generator.__name__}\n"
                f"sql={sql!r}\nexpected={expected!r}\nactual={actual!r}"
            )

    @pytest.mark.parametrize("seed", [7, 2024])
    def test_random_boolean_expressions_of_growing_depth_match(self, seed: int) -> None:
        rng = random.Random(seed)
        for depth in range(0, 6):
            for _ in range(50):
                sql, expected = _gen_boolean(rng, depth=depth)
                actual = parse(f"SELECT * FROM t WHERE {sql}")
                assert isinstance(actual, SelectStatement)
                assert actual.where == expected, f"depth={depth} sql={sql!r}"
