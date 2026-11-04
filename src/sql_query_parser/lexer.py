"""Lexer manual para el subconjunto de SQL documentado en el README.

Convierte el texto fuente en una lista de `Token`, resolviendo en un único
paso: comentarios (`--` de línea y `/* ... */` de bloque, sin anidar),
literales numéricos/cadena/booleanos/`NULL`, identificadores entrecomillados
con escape de comilla doblada (`"a""b"` -> `a"b`), y el conjunto de palabras
reservadas. No es un lexer en streaming — el subconjunto soportado nunca
produce fuentes lo bastante grandes como para justificar esa complejidad.
"""

from .errors import LexicalError
from .models import Position, Token, TokenType

KEYWORDS: frozenset[str] = frozenset(
    {
        "SELECT",
        "FROM",
        "WHERE",
        "AND",
        "OR",
        "NOT",
        "AS",
        "DISTINCT",
        "ALL",
        "JOIN",
        "CROSS",
        "INNER",
        "LEFT",
        "RIGHT",
        "FULL",
        "OUTER",
        "ON",
        "GROUP",
        "BY",
        "HAVING",
        "ORDER",
        "ASC",
        "DESC",
        "LIMIT",
        "OFFSET",
        "INSERT",
        "INTO",
        "VALUES",
        "UPDATE",
        "SET",
        "DELETE",
        "CREATE",
        "DROP",
        "ANALYZE",
        "TABLE",
        "INDEX",
        "UNIQUE",
        "PRIMARY",
        "KEY",
        "DEFAULT",
        "IF",
        "EXISTS",
        "LIKE",
        "IN",
        "IS",
        "BETWEEN",
        "INTEGER",
        "INT",
        "SMALLINT",
        "BIGINT",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "DECIMAL",
        "NUMERIC",
        "VARCHAR",
        "CHAR",
        "TEXT",
        "BOOLEAN",
        "BOOL",
        "DATE",
        "TIME",
        "TIMESTAMP",
    }
)

# Subconjunto de KEYWORDS válido como nombre de tipo en una CREATE TABLE.
DATA_TYPE_KEYWORDS: frozenset[str] = frozenset(
    {
        "INTEGER",
        "INT",
        "SMALLINT",
        "BIGINT",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "DECIMAL",
        "NUMERIC",
        "VARCHAR",
        "CHAR",
        "TEXT",
        "BOOLEAN",
        "BOOL",
        "DATE",
        "TIME",
        "TIMESTAMP",
    }
)

# TRUE/FALSE/NULL se tratan como literales de primera clase (no como KEYWORD
# genérico) porque el DoD del módulo los lista al mismo nivel que los
# literales numéricos y de cadena, no como parte de la gramática de
# sentencias — el parser no necesita reconocer texto de keyword para ellos.
_TRUE_FALSE: dict[str, bool] = {"TRUE": True, "FALSE": False}

_SINGLE_CHAR_TOKENS: dict[str, TokenType] = {
    "+": TokenType.PLUS,
    "-": TokenType.MINUS,
    "*": TokenType.STAR,
    "/": TokenType.SLASH,
    "%": TokenType.PERCENT,
    ".": TokenType.DOT,
    ",": TokenType.COMMA,
    "(": TokenType.LPAREN,
    ")": TokenType.RPAREN,
    ";": TokenType.SEMICOLON,
    "?": TokenType.PLACEHOLDER,
}


class Lexer:
    """Tokeniza una cadena SQL completa de una sola vez."""

    def __init__(self, source: str) -> None:
        self._source = source
        self._length = len(source)
        self._pos = 0
        self._line = 1
        self._column = 1

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while True:
            token = self._next_token()
            tokens.append(token)
            if token.type is TokenType.EOF:
                break
        return tokens

    # -- avance con seguimiento de línea/columna ----------------------------

    def _peek(self, offset: int = 0) -> str:
        index = self._pos + offset
        return self._source[index] if index < self._length else ""

    def _advance(self) -> str:
        char = self._source[self._pos]
        self._pos += 1
        if char == "\n":
            self._line += 1
            self._column = 1
        else:
            self._column += 1
        return char

    def _current_position(self) -> Position:
        return Position(self._line, self._column)

    # -- bucle principal ------------------------------------------------------

    def _next_token(self) -> Token:
        self._skip_whitespace_and_comments()
        start = self._current_position()
        if self._pos >= self._length:
            return Token(TokenType.EOF, "", None, start)

        char = self._peek()

        if char.isdigit():
            return self._read_number(start)
        if char == "'":
            return self._read_string(start)
        if char == '"':
            return self._read_quoted_identifier(start)
        if char.isalpha() or char == "_":
            return self._read_identifier_or_keyword(start)

        return self._read_operator(start)

    def _skip_whitespace_and_comments(self) -> None:
        while self._pos < self._length:
            char = self._peek()
            if char in " \t\r\n":
                self._advance()
                continue
            if char == "-" and self._peek(1) == "-":
                while self._pos < self._length and self._peek() != "\n":
                    self._advance()
                continue
            if char == "/" and self._peek(1) == "*":
                self._skip_block_comment()
                continue
            break

    def _skip_block_comment(self) -> None:
        start = self._current_position()
        self._advance()  # '/'
        self._advance()  # '*'
        while True:
            if self._pos >= self._length:
                raise LexicalError("unterminated block comment", start)
            if self._peek() == "*" and self._peek(1) == "/":
                self._advance()
                self._advance()
                return
            self._advance()

    # -- literales ------------------------------------------------------------

    def _read_number(self, start: Position) -> Token:
        begin = self._pos
        is_float = False
        while self._peek().isdigit():
            self._advance()
        if self._peek() == "." and self._peek(1).isdigit():
            is_float = True
            self._advance()
            while self._peek().isdigit():
                self._advance()
        if self._peek() in ("e", "E"):
            lookahead = 1
            if self._peek(lookahead) in ("+", "-"):
                lookahead += 1
            if self._peek(lookahead).isdigit():
                is_float = True
                self._advance()
                if self._peek() in ("+", "-"):
                    self._advance()
                while self._peek().isdigit():
                    self._advance()
        lexeme = self._source[begin : self._pos]
        if is_float:
            return Token(TokenType.FLOAT, lexeme, float(lexeme), start)
        return Token(TokenType.INTEGER, lexeme, int(lexeme), start)

    def _read_string(self, start: Position) -> Token:
        self._advance()  # comilla simple de apertura
        chars: list[str] = []
        while True:
            if self._pos >= self._length:
                raise LexicalError("unterminated string literal", start)
            char = self._advance()
            if char == "'":
                if self._peek() == "'":
                    self._advance()
                    chars.append("'")
                    continue
                break
            chars.append(char)
        value = "".join(chars)
        return Token(TokenType.STRING, f"'{value}'", value, start)

    def _read_quoted_identifier(self, start: Position) -> Token:
        self._advance()  # comilla doble de apertura
        chars: list[str] = []
        while True:
            if self._pos >= self._length:
                raise LexicalError("unterminated quoted identifier", start)
            char = self._advance()
            if char == '"':
                if self._peek() == '"':
                    self._advance()
                    chars.append('"')
                    continue
                break
            chars.append(char)
        value = "".join(chars)
        return Token(TokenType.QUOTED_IDENTIFIER, value, None, start)

    def _read_identifier_or_keyword(self, start: Position) -> Token:
        begin = self._pos
        while self._peek().isalnum() or self._peek() == "_":
            self._advance()
        lexeme = self._source[begin : self._pos]
        upper = lexeme.upper()
        if upper in _TRUE_FALSE:
            return Token(TokenType.BOOLEAN, upper, _TRUE_FALSE[upper], start)
        if upper == "NULL":
            return Token(TokenType.NULL, upper, None, start)
        if upper in KEYWORDS:
            return Token(TokenType.KEYWORD, upper, None, start)
        return Token(TokenType.IDENTIFIER, lexeme, None, start)

    # -- operadores -------------------------------------------------------------

    def _read_operator(self, start: Position) -> Token:
        char = self._advance()
        if char == "=":
            return Token(TokenType.EQ, "=", None, start)
        if char == "<":
            if self._peek() == "=":
                self._advance()
                return Token(TokenType.LE, "<=", None, start)
            if self._peek() == ">":
                self._advance()
                return Token(TokenType.NEQ, "<>", None, start)
            return Token(TokenType.LT, "<", None, start)
        if char == ">":
            if self._peek() == "=":
                self._advance()
                return Token(TokenType.GE, ">=", None, start)
            return Token(TokenType.GT, ">", None, start)
        if char == "!":
            if self._peek() == "=":
                self._advance()
                return Token(TokenType.NEQ, "!=", None, start)
            raise LexicalError(f"unexpected character {char!r}", start)
        if char in _SINGLE_CHAR_TOKENS:
            return Token(_SINGLE_CHAR_TOKENS[char], char, None, start)
        raise LexicalError(f"unexpected character {char!r}", start)
