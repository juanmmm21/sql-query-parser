"""Parser recursivo-descendente sobre el flujo de `Token` del lexer.

La gramática completa soportada está documentada en el README. Aquí sólo se
explican decisiones no obvias:

- Las comparaciones (`=`, `<>`, `<`, `LIKE`, `IN`, `BETWEEN`, `IS [NOT] NULL`)
  son deliberadamente NO asociativas: `a = b = c` es un error de sintaxis,
  igual que en SQL estándar (no existe encadenamiento de comparaciones).
- Las subconsultas (`(SELECT ...)` en cualquier posición de expresión) se
  reconocen y se rechazan explícitamente con `UnsupportedFeatureError` en
  vez de fallar más abajo en la gramática con un error de sintaxis genérico
  y confuso.
"""

from typing import NoReturn

from .errors import SqlSyntaxError, UnsupportedFeatureError
from .models import (
    BinaryOp,
    BinaryOperator,
    Between,
    ColumnRef,
    Expression,
    FunctionCall,
    InList,
    IsNull,
    Like,
    Literal,
    LiteralType,
    Star,
    Token,
    TokenType,
    UnaryOp,
    UnaryOperator,
)

_COMPARISON_OPERATORS: dict[TokenType, BinaryOperator] = {
    TokenType.EQ: BinaryOperator.EQ,
    TokenType.NEQ: BinaryOperator.NEQ,
    TokenType.LT: BinaryOperator.LT,
    TokenType.LE: BinaryOperator.LE,
    TokenType.GT: BinaryOperator.GT,
    TokenType.GE: BinaryOperator.GE,
}


class Parser:
    """Consume una lista de `Token` (terminada en `EOF`) y produce nodos del
    AST tipado. No conoce el lexer — se puede alimentar directamente con
    tokens sintéticos, lo que permite testear la gramática sin pasar por la
    tokenización real."""

    def __init__(self, tokens: list[Token]) -> None:
        if not tokens or tokens[-1].type is not TokenType.EOF:
            raise ValueError("token stream must end with an EOF token")
        self._tokens = tokens
        self._pos = 0

    # -- utilidades de flujo de tokens --------------------------------------

    def _current(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        token = self._tokens[self._pos]
        if token.type is not TokenType.EOF:
            self._pos += 1
        return token

    def _check_keyword(self, *keywords: str) -> bool:
        token = self._current()
        return token.type is TokenType.KEYWORD and token.lexeme in keywords

    def _check_type(self, token_type: TokenType) -> bool:
        return self._current().type is token_type

    def _match_keyword(self, *keywords: str) -> Token | None:
        if self._check_keyword(*keywords):
            return self._advance()
        return None

    def _match_type(self, token_type: TokenType) -> Token | None:
        if self._check_type(token_type):
            return self._advance()
        return None

    def _expect_keyword(self, keyword: str) -> Token:
        if not self._check_keyword(keyword):
            self._error(f"expected {keyword!r}")
        return self._advance()

    def _expect_type(self, token_type: TokenType, description: str) -> Token:
        if not self._check_type(token_type):
            self._error(f"expected {description}")
        return self._advance()

    def _expect_identifier(self) -> str:
        token = self._current()
        if token.type is TokenType.IDENTIFIER or token.type is TokenType.QUOTED_IDENTIFIER:
            self._advance()
            return token.lexeme
        self._error("expected identifier")

    def _error(self, message: str) -> NoReturn:
        token = self._current()
        found = "end of input" if token.type is TokenType.EOF else repr(token.lexeme)
        raise SqlSyntaxError(f"{message}, found {found}", token.position)

    def _reject_subquery(self) -> NoReturn:
        token = self._current()
        raise UnsupportedFeatureError(
            "subqueries are not supported by this parser subset "
            "(see README: excluded constructs)",
            token.position,
        )

    # -- expresiones: precedencia de menor a mayor --------------------------
    # or -> and -> not -> comparison -> additive -> multiplicative -> unary
    # -> primary

    def parse_expression(self) -> Expression:
        return self._parse_or()

    def _parse_or(self) -> Expression:
        left = self._parse_and()
        while self._match_keyword("OR") is not None:
            left = BinaryOp(left, BinaryOperator.OR, self._parse_and())
        return left

    def _parse_and(self) -> Expression:
        left = self._parse_not()
        while self._match_keyword("AND") is not None:
            left = BinaryOp(left, BinaryOperator.AND, self._parse_not())
        return left

    def _parse_not(self) -> Expression:
        if self._match_keyword("NOT") is not None:
            return UnaryOp(UnaryOperator.NOT, self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> Expression:
        left = self._parse_additive()

        negated = self._match_keyword("NOT") is not None

        if self._match_keyword("LIKE") is not None:
            return Like(left, self._parse_additive(), negated)

        if self._match_keyword("IN") is not None:
            return InList(left, self._parse_in_list(), negated)

        if self._match_keyword("BETWEEN") is not None:
            low = self._parse_additive()
            self._expect_keyword("AND")
            high = self._parse_additive()
            return Between(left, low, high, negated)

        if negated:
            self._error("expected LIKE, IN or BETWEEN after NOT")

        if self._match_keyword("IS") is not None:
            is_negated = self._match_keyword("NOT") is not None
            self._expect_type(TokenType.NULL, "NULL")
            return IsNull(left, is_negated)

        operator_type = self._current().type
        if operator_type in _COMPARISON_OPERATORS:
            self._advance()
            return BinaryOp(left, _COMPARISON_OPERATORS[operator_type], self._parse_additive())

        return left

    def _parse_in_list(self) -> tuple[Expression, ...]:
        self._expect_type(TokenType.LPAREN, "'('")
        if self._check_keyword("SELECT"):
            self._reject_subquery()
        values = [self.parse_expression()]
        while self._match_type(TokenType.COMMA) is not None:
            values.append(self.parse_expression())
        self._expect_type(TokenType.RPAREN, "')'")
        return tuple(values)

    def _parse_additive(self) -> Expression:
        left = self._parse_multiplicative()
        while True:
            if self._match_type(TokenType.PLUS) is not None:
                left = BinaryOp(left, BinaryOperator.ADD, self._parse_multiplicative())
            elif self._match_type(TokenType.MINUS) is not None:
                left = BinaryOp(left, BinaryOperator.SUB, self._parse_multiplicative())
            else:
                return left

    def _parse_multiplicative(self) -> Expression:
        left = self._parse_unary()
        while True:
            if self._match_type(TokenType.STAR) is not None:
                left = BinaryOp(left, BinaryOperator.MUL, self._parse_unary())
            elif self._match_type(TokenType.SLASH) is not None:
                left = BinaryOp(left, BinaryOperator.DIV, self._parse_unary())
            elif self._match_type(TokenType.PERCENT) is not None:
                left = BinaryOp(left, BinaryOperator.MOD, self._parse_unary())
            else:
                return left

    def _parse_unary(self) -> Expression:
        if self._match_type(TokenType.MINUS) is not None:
            return UnaryOp(UnaryOperator.NEG, self._parse_unary())
        if self._match_type(TokenType.PLUS) is not None:
            return UnaryOp(UnaryOperator.POS, self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> Expression:
        token = self._current()

        if token.type is TokenType.INTEGER:
            self._advance()
            return Literal(token.value, LiteralType.INTEGER)
        if token.type is TokenType.FLOAT:
            self._advance()
            return Literal(token.value, LiteralType.FLOAT)
        if token.type is TokenType.STRING:
            self._advance()
            return Literal(token.value, LiteralType.STRING)
        if token.type is TokenType.BOOLEAN:
            self._advance()
            return Literal(token.value, LiteralType.BOOLEAN)
        if token.type is TokenType.NULL:
            self._advance()
            return Literal(None, LiteralType.NULL)
        if token.type is TokenType.STAR:
            self._advance()
            return Star()
        if token.type is TokenType.LPAREN:
            self._advance()
            if self._check_keyword("SELECT"):
                self._reject_subquery()
            expression = self.parse_expression()
            self._expect_type(TokenType.RPAREN, "')'")
            return expression
        if token.type is TokenType.IDENTIFIER or token.type is TokenType.QUOTED_IDENTIFIER:
            return self._parse_identifier_expression()

        self._error("expected an expression")

    def _parse_identifier_expression(self) -> Expression:
        first_token = self._advance()
        first_name = first_token.lexeme

        if first_token.type is TokenType.IDENTIFIER and self._check_type(TokenType.LPAREN):
            return self._parse_function_call(first_name)

        if self._match_type(TokenType.DOT) is not None:
            return ColumnRef(name=self._expect_identifier(), table=first_name)

        return ColumnRef(name=first_name)

    def _parse_function_call(self, name: str) -> FunctionCall:
        self._advance()  # '('

        if self._match_type(TokenType.STAR) is not None:
            self._expect_type(TokenType.RPAREN, "')'")
            return FunctionCall(name=name, star_argument=True)

        distinct = self._match_keyword("DISTINCT") is not None

        if self._match_type(TokenType.RPAREN) is not None:
            return FunctionCall(name=name, distinct=distinct)

        arguments = [self.parse_expression()]
        while self._match_type(TokenType.COMMA) is not None:
            arguments.append(self.parse_expression())
        self._expect_type(TokenType.RPAREN, "')'")
        return FunctionCall(name=name, arguments=tuple(arguments), distinct=distinct)
