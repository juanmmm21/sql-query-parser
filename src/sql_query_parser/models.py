"""Tipos de datos compartidos: tokens del lexer y nodos tipados del AST.

Todos los nodos del AST son dataclasses inmutables (`frozen=True`) — el árbol
que produce el parser no se muta nunca aguas abajo (p.ej. en
`cost-based-query-optimizer`), sólo se recorre o se reconstruye.
"""

from dataclasses import dataclass
from enum import Enum, auto

# ---------------------------------------------------------------------------
# Tokens (salida del lexer)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Position:
    """Posición 1-indexada (línea, columna) dentro del texto fuente original."""

    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.line}:{self.column}"


class TokenType(Enum):
    """Categoría léxica de un token.

    Las palabras reservadas comparten un único miembro `KEYWORD` y se
    distinguen por `Token.lexeme` (normalizado en mayúsculas) en vez de tener
    una variante de enum por palabra clave: el parser ya necesita comparar
    contra conjuntos de literales de texto para expresiones multi-palabra
    (`IS NOT NULL`, `NOT LIKE`, `LEFT OUTER JOIN`...), así que duplicar cada
    keyword como tipo de token no añadiría seguridad de tipos real.
    """

    IDENTIFIER = auto()
    QUOTED_IDENTIFIER = auto()
    KEYWORD = auto()
    INTEGER = auto()
    FLOAT = auto()
    STRING = auto()
    BOOLEAN = auto()
    NULL = auto()
    EQ = auto()
    NEQ = auto()
    LT = auto()
    LE = auto()
    GT = auto()
    GE = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()
    DOT = auto()
    COMMA = auto()
    LPAREN = auto()
    RPAREN = auto()
    SEMICOLON = auto()
    EOF = auto()


TokenValue = int | float | str | bool | None


@dataclass(frozen=True, slots=True)
class Token:
    """Unidad léxica emitida por el `Lexer`.

    `value` sólo lleva contenido significativo para literales
    (`INTEGER`/`FLOAT`/`STRING`); en el resto de casos es `None` y el texto
    reside en `lexeme` (ya normalizado a mayúsculas para `KEYWORD`).
    """

    type: TokenType
    lexeme: str
    value: TokenValue
    position: Position


# ---------------------------------------------------------------------------
# Expresiones
# ---------------------------------------------------------------------------


class Expression:
    """Clase marcadora para cualquier nodo de expresión del AST."""


class LiteralType(Enum):
    INTEGER = auto()
    FLOAT = auto()
    STRING = auto()
    BOOLEAN = auto()
    NULL = auto()


LiteralValue = int | float | str | bool | None


@dataclass(frozen=True, slots=True)
class Literal(Expression):
    value: LiteralValue
    literal_type: LiteralType


@dataclass(frozen=True, slots=True)
class ColumnRef(Expression):
    """Referencia a una columna, opcionalmente cualificada por tabla (`t.col`)."""

    name: str
    table: str | None = None


@dataclass(frozen=True, slots=True)
class Star(Expression):
    """El comodín `*`, válido en `SELECT *` y en `COUNT(*)`."""


class BinaryOperator(Enum):
    OR = auto()
    AND = auto()
    EQ = auto()
    NEQ = auto()
    LT = auto()
    LE = auto()
    GT = auto()
    GE = auto()
    ADD = auto()
    SUB = auto()
    MUL = auto()
    DIV = auto()
    MOD = auto()


@dataclass(frozen=True, slots=True)
class BinaryOp(Expression):
    left: Expression
    operator: BinaryOperator
    right: Expression


class UnaryOperator(Enum):
    NEG = auto()
    POS = auto()
    NOT = auto()


@dataclass(frozen=True, slots=True)
class UnaryOp(Expression):
    operator: UnaryOperator
    operand: Expression


@dataclass(frozen=True, slots=True)
class Between(Expression):
    operand: Expression
    low: Expression
    high: Expression
    negated: bool = False


@dataclass(frozen=True, slots=True)
class InList(Expression):
    operand: Expression
    values: tuple[Expression, ...]
    negated: bool = False


@dataclass(frozen=True, slots=True)
class Like(Expression):
    operand: Expression
    pattern: Expression
    negated: bool = False


@dataclass(frozen=True, slots=True)
class IsNull(Expression):
    operand: Expression
    negated: bool = False


@dataclass(frozen=True, slots=True)
class FunctionCall(Expression):
    """Llamada a función, incluyendo la sintaxis agregada `COUNT(DISTINCT x)`
    y `COUNT(*)`. Este módulo no distingue funciones agregadas de escalares
    — esa clasificación semántica es responsabilidad de
    `cost-based-query-optimizer`, no del parser."""

    name: str
    arguments: tuple[Expression, ...] = ()
    distinct: bool = False
    star_argument: bool = False


# ---------------------------------------------------------------------------
# Cláusulas y fragmentos compartidos entre sentencias
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelectItem:
    expression: Expression
    alias: str | None = None


@dataclass(frozen=True, slots=True)
class TableRef:
    name: str
    alias: str | None = None


class JoinType(Enum):
    INNER = auto()
    LEFT = auto()
    RIGHT = auto()
    FULL = auto()


@dataclass(frozen=True, slots=True)
class Join:
    join_type: JoinType
    table: TableRef
    on: Expression


class OrderDirection(Enum):
    ASC = auto()
    DESC = auto()


@dataclass(frozen=True, slots=True)
class OrderByItem:
    expression: Expression
    direction: OrderDirection = OrderDirection.ASC


@dataclass(frozen=True, slots=True)
class Assignment:
    """Una asignación `columna = expr` dentro de `UPDATE ... SET`."""

    column: str
    value: Expression


@dataclass(frozen=True, slots=True)
class DataType:
    """Tipo de columna de `CREATE TABLE`, p.ej. `VARCHAR(255)` o
    `DECIMAL(10, 2)` — `parameters` guarda los enteros entre paréntesis en el
    orden en que aparecen."""

    name: str
    parameters: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ColumnDefinition:
    name: str
    data_type: DataType
    not_null: bool = False
    primary_key: bool = False
    unique: bool = False
    default: Expression | None = None


# ---------------------------------------------------------------------------
# Sentencias de nivel superior
# ---------------------------------------------------------------------------


class Statement:
    """Clase marcadora para cualquier nodo de sentencia de nivel superior."""


@dataclass(frozen=True, slots=True)
class SelectStatement(Statement):
    columns: tuple[SelectItem, ...]
    from_table: TableRef
    distinct: bool = False
    joins: tuple[Join, ...] = ()
    where: Expression | None = None
    group_by: tuple[Expression, ...] = ()
    having: Expression | None = None
    order_by: tuple[OrderByItem, ...] = ()
    limit: int | None = None
    offset: int | None = None


@dataclass(frozen=True, slots=True)
class InsertStatement(Statement):
    """`columns` vacío significa que la sentencia no listó columnas
    explícitamente (`INSERT INTO t VALUES (...)`, implica todas las
    columnas de la tabla en orden de definición)."""

    table: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Expression, ...], ...]


@dataclass(frozen=True, slots=True)
class UpdateStatement(Statement):
    table: str
    assignments: tuple[Assignment, ...]
    where: Expression | None = None


@dataclass(frozen=True, slots=True)
class DeleteStatement(Statement):
    table: str
    where: Expression | None = None


@dataclass(frozen=True, slots=True)
class CreateTableStatement(Statement):
    table: str
    columns: tuple[ColumnDefinition, ...]
    if_not_exists: bool = False


@dataclass(frozen=True, slots=True)
class CreateIndexStatement(Statement):
    index_name: str
    table: str
    columns: tuple[str, ...]
    unique: bool = False
    if_not_exists: bool = False
