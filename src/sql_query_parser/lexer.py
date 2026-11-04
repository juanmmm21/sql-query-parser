"""Lexer manual para el subconjunto de SQL documentado en el README.

Convierte el texto fuente en una lista de `Token`, resolviendo en un único
paso: comentarios (`--` de línea y `/* ... */` de bloque, sin anidar),
literales numéricos/cadena/booleanos/`NULL`, identificadores entrecomillados
con escape de comilla doblada (`"a""b"` -> `a"b`), y el conjunto de palabras
reservadas. No es un lexer en streaming — el subconjunto soportado nunca
produce fuentes lo bastante grandes como para justificar esa complejidad.
"""

import re

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


# Camino rápido del lexer (ver `Lexer._next_token`): los tokens ASCII más
# comunes se reconocen con una expresión regular en vez de carácter a
# carácter. Todo lo demás (comentarios, caracteres no ASCII, operadores de
# dos caracteres, errores) sigue por el camino original, que define el
# comportamiento: tests/test_lexer_equivalence.py compara ambos.
_WHITESPACE = re.compile(r"[ \t\r\n]+")
_ASCII_NUMBER = re.compile(r"[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?")
_ASCII_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# La comilla de cierre no puede ir seguida de otra: `''` es un escape.
_STRING = re.compile(r"'(?:[^']|'')*'(?!')")
# Tras saltar espacios y comentarios, `-` y `/` ya no pueden abrir uno.
_FAST_PUNCTUATION: dict[str, TokenType] = {
    **{char: kind for char, kind in _SINGLE_CHAR_TOKENS.items()},
    "=": TokenType.EQ,
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

    def _consume_to(self, end: int) -> None:
        """Avanza hasta `end` actualizando línea y columna de una vez."""
        newlines = self._source.count("\n", self._pos, end)
        if newlines:
            self._line += newlines
            self._column = end - self._source.rfind("\n", self._pos, end)
        else:
            self._column += end - self._pos
        self._pos = end

    def _next_token(self) -> Token:
        source = self._source
        blank = _WHITESPACE.match(source, self._pos)
        if blank is not None:
            self._consume_to(blank.end())
        if self._pos < self._length and source[self._pos] in "-/":
            self._skip_whitespace_and_comments()
        start = Position(self._line, self._column)
        if self._pos >= self._length:
            return Token(TokenType.EOF, "", None, start)

        char = source[self._pos]
        kind = _FAST_PUNCTUATION.get(char)
        if kind is not None:
            self._pos += 1
            self._column += 1
            return Token(kind, char, None, start)
        if "0" <= char <= "9":
            number = _ASCII_NUMBER.match(source, self._pos)
            if number is not None and source[number.end() : number.end() + 3].isascii():
                lexeme = number.group()
                self._pos = number.end()
                self._column += len(lexeme)
                if number.group(1) or number.group(2):
                    return Token(TokenType.FLOAT, lexeme, float(lexeme), start)
                return Token(TokenType.INTEGER, lexeme, int(lexeme), start)
        elif char == "'":
            string = _STRING.match(source, self._pos)
            if string is not None:
                value = string.group()[1:-1].replace("''", "'")
                self._consume_to(string.end())
                return Token(TokenType.STRING, f"'{value}'", value, start)
        elif char == "_" or "a" <= char <= "z" or "A" <= char <= "Z":
            word = _ASCII_WORD.match(source, self._pos)
            if word is not None and (word.end() >= self._length or source[word.end()].isascii()):
                lexeme = word.group()
                self._pos = word.end()
                self._column += len(lexeme)
                return _word_token(lexeme, start)
        return self._next_token_slow(start)

    def _next_token_slow(self, start: Position) -> Token:
        """El camino original, carácter a carácter: define el lexer."""
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
        return _word_token(self._source[begin : self._pos], start)

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


def _word_token(lexeme: str, start: Position) -> Token:
    upper = lexeme.upper()
    if upper in _TRUE_FALSE:
        return Token(TokenType.BOOLEAN, upper, _TRUE_FALSE[upper], start)
    if upper == "NULL":
        return Token(TokenType.NULL, upper, None, start)
    if upper in KEYWORDS:
        return Token(TokenType.KEYWORD, upper, None, start)
    return Token(TokenType.IDENTIFIER, lexeme, None, start)
