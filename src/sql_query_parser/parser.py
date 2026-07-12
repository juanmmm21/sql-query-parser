"""Parser recursivo-descendente sobre el flujo de `Token` del lexer.

La gramática completa soportada está documentada en el README. Aquí sólo se
explican decisiones no obvias:

- Las comparaciones (`=`, `<>`, `<`, `LIKE`, `IN`, `BETWEEN`, `IS [NOT] NULL`)
  son deliberadamente NO asociativas: `a = b = c` es un error de sintaxis,
  igual que en SQL estándar (no existe encadenamiento de comparaciones).
- Las subconsultas se aceptan en dos posiciones de EXPRESIÓN: `(SELECT ...)`
  como primaria (nodo `Subquery`, subconsulta escalar) y `[NOT] IN (SELECT
  ...)` (nodo `InSubquery`). Comparten instancia de `Parser` con la sentencia
  externa, así que los `Placeholder` de una subconsulta continúan la
  numeración por sentencia de su consulta externa — exactamente lo que un
  driver necesita para ligar la tupla de parámetros completa de una vez.
- Las subconsultas en `FROM` (tablas derivadas) siguen fuera del subconjunto
  y se rechazan explícitamente con `UnsupportedFeatureError` en vez de fallar
  más abajo con un error de sintaxis genérico y confuso.
"""

from typing import NoReturn

from .errors import SqlSyntaxError, UnsupportedFeatureError
from .lexer import DATA_TYPE_KEYWORDS
from .models import (
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
    InSubquery,
    IsNull,
    Join,
    JoinType,
    Like,
    Literal,
    LiteralType,
    OrderByItem,
    OrderDirection,
    Placeholder,
    SelectItem,
    SelectStatement,
    Star,
    Statement,
    Subquery,
    TableRef,
    Token,
    TokenType,
    UnaryOp,
    UnaryOperator,
    UpdateStatement,
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
        self._placeholder_count = 0

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

    def _parse_subquery(self) -> Subquery:
        """Parsea `SELECT ...` ya dentro de un paréntesis abierto (el `(`
        lo consumió el llamador; el `)` de cierre también lo cierra él).
        Reutiliza `_parse_select` sobre el MISMO parser: el contador de
        placeholders sigue siendo el de la sentencia externa."""
        return Subquery(statement=self._parse_select())

    def _reject_derived_table(self) -> NoReturn:
        token = self._current()
        raise UnsupportedFeatureError(
            "derived tables (subqueries in FROM) are not supported by this parser "
            "subset (see README: excluded constructs)",
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
            return self._parse_in_rhs(left, negated)

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

    def _parse_in_rhs(self, operand: Expression, negated: bool) -> Expression:
        """Lado derecho de `[NOT] IN`: o una lista de expresiones o una
        subconsulta. La distinción se hace aquí (no dejando que `(SELECT
        ...)` entre como primer elemento de la lista) porque producen nodos
        distintos con semántica distinta — ver `InSubquery` en models.py."""
        self._expect_type(TokenType.LPAREN, "'('")
        if self._check_keyword("SELECT"):
            subquery = self._parse_subquery()
            self._expect_type(TokenType.RPAREN, "')'")
            return InSubquery(operand=operand, subquery=subquery, negated=negated)
        values = [self.parse_expression()]
        while self._match_type(TokenType.COMMA) is not None:
            values.append(self.parse_expression())
        self._expect_type(TokenType.RPAREN, "')'")
        return InList(operand=operand, values=tuple(values), negated=negated)

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
        if token.type is TokenType.PLACEHOLDER:
            self._advance()
            index = self._placeholder_count
            self._placeholder_count += 1
            return Placeholder(index=index)
        if token.type is TokenType.LPAREN:
            self._advance()
            if self._check_keyword("SELECT"):
                subquery = self._parse_subquery()
                self._expect_type(TokenType.RPAREN, "')'")
                return subquery
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

    # -- entrada de nivel superior -------------------------------------------

    def parse_statement(self) -> Statement:
        # Los índices de placeholder son por sentencia: en un programa de
        # varias sentencias (`parse_many`), cada una liga sus parámetros
        # desde 0, igual que las ejecutaría un driver una a una.
        self._placeholder_count = 0
        if self._check_keyword("SELECT"):
            return self._parse_select()
        if self._check_keyword("INSERT"):
            return self._parse_insert()
        if self._check_keyword("UPDATE"):
            return self._parse_update()
        if self._check_keyword("DELETE"):
            return self._parse_delete()
        if self._check_keyword("CREATE"):
            return self._parse_create()
        self._error("expected a SELECT, INSERT, UPDATE, DELETE or CREATE statement")

    def parse_single_statement(self) -> Statement:
        """Parsea exactamente una sentencia, con `;` final opcional, y exige
        que no quede ningún token más allá (más allá de `EOF`)."""
        statement = self.parse_statement()
        self._match_type(TokenType.SEMICOLON)
        if not self._check_type(TokenType.EOF):
            self._error("expected end of input after statement")
        return statement

    def parse_program(self) -> list[Statement]:
        """Parsea una o más sentencias separadas por `;` (el `;` final es
        opcional en la última)."""
        statements = [self.parse_statement()]
        while self._match_type(TokenType.SEMICOLON) is not None:
            if self._check_type(TokenType.EOF):
                break
            statements.append(self.parse_statement())
        if not self._check_type(TokenType.EOF):
            self._error("expected ';' or end of input")
        return statements

    # -- SELECT ---------------------------------------------------------------

    def _parse_select(self) -> SelectStatement:
        self._expect_keyword("SELECT")
        distinct = self._match_keyword("DISTINCT") is not None
        columns = self._parse_select_list()
        self._expect_keyword("FROM")
        from_table = self._parse_table_ref()
        joins = self._parse_joins()
        where = self._parse_optional_where()
        group_by = self._parse_optional_group_by()
        having = self._parse_optional_having()
        order_by = self._parse_optional_order_by()
        limit, offset = self._parse_optional_limit_offset()
        return SelectStatement(
            columns=columns,
            from_table=from_table,
            distinct=distinct,
            joins=joins,
            where=where,
            group_by=group_by,
            having=having,
            order_by=order_by,
            limit=limit,
            offset=offset,
        )

    def _parse_select_list(self) -> tuple[SelectItem, ...]:
        items = [self._parse_select_item()]
        while self._match_type(TokenType.COMMA) is not None:
            items.append(self._parse_select_item())
        return tuple(items)

    def _parse_select_item(self) -> SelectItem:
        expression = self.parse_expression()
        alias = self._parse_optional_alias()
        return SelectItem(expression=expression, alias=alias)

    def _parse_optional_alias(self) -> str | None:
        """`AS alias` o alias sin `AS`. No hay ambigüedad con la siguiente
        cláusula porque toda palabra reservada se tokeniza como `KEYWORD`,
        `NULL` o `BOOLEAN`, nunca como `IDENTIFIER`."""
        if self._match_keyword("AS") is not None:
            return self._expect_identifier()
        if self._check_type(TokenType.IDENTIFIER) or self._check_type(TokenType.QUOTED_IDENTIFIER):
            return self._expect_identifier()
        return None

    def _parse_table_ref(self) -> TableRef:
        # Una subconsulta en posición de tabla es una construcción SQL real
        # (tabla derivada) que este subconjunto excluye: merece el error de
        # alcance específico, no un "expected identifier" genérico.
        if self._check_type(TokenType.LPAREN):
            self._reject_derived_table()
        name = self._expect_identifier()
        alias = self._parse_optional_alias()
        return TableRef(name=name, alias=alias)

    def _parse_joins(self) -> tuple[Join, ...]:
        joins: list[Join] = []
        while True:
            join_type = self._try_parse_join_type()
            if join_type is None:
                break
            table = self._parse_table_ref()
            self._expect_keyword("ON")
            on = self.parse_expression()
            joins.append(Join(join_type=join_type, table=table, on=on))
        return tuple(joins)

    def _try_parse_join_type(self) -> JoinType | None:
        if self._match_keyword("JOIN") is not None:
            return JoinType.INNER
        if self._match_keyword("INNER") is not None:
            self._expect_keyword("JOIN")
            return JoinType.INNER
        if self._match_keyword("LEFT") is not None:
            self._match_keyword("OUTER")
            self._expect_keyword("JOIN")
            return JoinType.LEFT
        if self._match_keyword("RIGHT") is not None:
            self._match_keyword("OUTER")
            self._expect_keyword("JOIN")
            return JoinType.RIGHT
        if self._match_keyword("FULL") is not None:
            self._match_keyword("OUTER")
            self._expect_keyword("JOIN")
            return JoinType.FULL
        return None

    def _parse_optional_where(self) -> Expression | None:
        if self._match_keyword("WHERE") is not None:
            return self.parse_expression()
        return None

    def _parse_optional_group_by(self) -> tuple[Expression, ...]:
        if self._match_keyword("GROUP") is None:
            return ()
        self._expect_keyword("BY")
        items = [self.parse_expression()]
        while self._match_type(TokenType.COMMA) is not None:
            items.append(self.parse_expression())
        return tuple(items)

    def _parse_optional_having(self) -> Expression | None:
        if self._match_keyword("HAVING") is not None:
            return self.parse_expression()
        return None

    def _parse_optional_order_by(self) -> tuple[OrderByItem, ...]:
        if self._match_keyword("ORDER") is None:
            return ()
        self._expect_keyword("BY")
        items = [self._parse_order_by_item()]
        while self._match_type(TokenType.COMMA) is not None:
            items.append(self._parse_order_by_item())
        return tuple(items)

    def _parse_order_by_item(self) -> OrderByItem:
        expression = self.parse_expression()
        direction = OrderDirection.ASC
        if self._match_keyword("DESC") is not None:
            direction = OrderDirection.DESC
        else:
            self._match_keyword("ASC")
        return OrderByItem(expression=expression, direction=direction)

    def _parse_optional_limit_offset(self) -> tuple[int | None, int | None]:
        if self._match_keyword("LIMIT") is None:
            return None, None
        limit = self._expect_integer_literal()
        offset: int | None = None
        if self._match_keyword("OFFSET") is not None:
            offset = self._expect_integer_literal()
        return limit, offset

    def _expect_integer_literal(self) -> int:
        token = self._expect_type(TokenType.INTEGER, "integer literal")
        assert isinstance(token.value, int)
        return token.value

    # -- INSERT -----------------------------------------------------------------

    def _parse_insert(self) -> InsertStatement:
        self._expect_keyword("INSERT")
        self._expect_keyword("INTO")
        table = self._expect_identifier()
        columns: tuple[str, ...] = ()
        if self._match_type(TokenType.LPAREN) is not None:
            names = [self._expect_identifier()]
            while self._match_type(TokenType.COMMA) is not None:
                names.append(self._expect_identifier())
            self._expect_type(TokenType.RPAREN, "')'")
            columns = tuple(names)
        self._expect_keyword("VALUES")
        rows = [self._parse_value_row()]
        while self._match_type(TokenType.COMMA) is not None:
            rows.append(self._parse_value_row())
        return InsertStatement(table=table, columns=columns, rows=tuple(rows))

    def _parse_value_row(self) -> tuple[Expression, ...]:
        self._expect_type(TokenType.LPAREN, "'('")
        values = [self.parse_expression()]
        while self._match_type(TokenType.COMMA) is not None:
            values.append(self.parse_expression())
        self._expect_type(TokenType.RPAREN, "')'")
        return tuple(values)

    # -- UPDATE -----------------------------------------------------------------

    def _parse_update(self) -> UpdateStatement:
        self._expect_keyword("UPDATE")
        table = self._expect_identifier()
        self._expect_keyword("SET")
        assignments = [self._parse_assignment()]
        while self._match_type(TokenType.COMMA) is not None:
            assignments.append(self._parse_assignment())
        where = self._parse_optional_where()
        return UpdateStatement(table=table, assignments=tuple(assignments), where=where)

    def _parse_assignment(self) -> Assignment:
        column = self._expect_identifier()
        self._expect_type(TokenType.EQ, "'='")
        value = self.parse_expression()
        return Assignment(column=column, value=value)

    # -- DELETE -----------------------------------------------------------------

    def _parse_delete(self) -> DeleteStatement:
        self._expect_keyword("DELETE")
        self._expect_keyword("FROM")
        table = self._expect_identifier()
        where = self._parse_optional_where()
        return DeleteStatement(table=table, where=where)

    # -- CREATE TABLE / CREATE INDEX ---------------------------------------------

    def _parse_create(self) -> Statement:
        self._expect_keyword("CREATE")
        if self._check_keyword("UNIQUE"):
            return self._parse_create_index(unique=True)
        if self._check_keyword("INDEX"):
            return self._parse_create_index(unique=False)
        if self._check_keyword("TABLE"):
            return self._parse_create_table()
        self._error("expected TABLE or INDEX after CREATE")

    def _parse_if_not_exists(self) -> bool:
        if self._match_keyword("IF") is not None:
            self._expect_keyword("NOT")
            self._expect_keyword("EXISTS")
            return True
        return False

    def _parse_create_table(self) -> CreateTableStatement:
        self._expect_keyword("TABLE")
        if_not_exists = self._parse_if_not_exists()
        table = self._expect_identifier()
        self._expect_type(TokenType.LPAREN, "'('")
        columns = [self._parse_column_definition()]
        while self._match_type(TokenType.COMMA) is not None:
            columns.append(self._parse_column_definition())
        self._expect_type(TokenType.RPAREN, "')'")
        return CreateTableStatement(
            table=table, columns=tuple(columns), if_not_exists=if_not_exists
        )

    def _parse_column_definition(self) -> ColumnDefinition:
        name = self._expect_identifier()
        data_type = self._parse_data_type()
        not_null = False
        primary_key = False
        unique = False
        default: Expression | None = None
        while True:
            if self._match_keyword("NOT") is not None:
                self._expect_type(TokenType.NULL, "NULL")
                not_null = True
            elif self._match_keyword("PRIMARY") is not None:
                self._expect_keyword("KEY")
                primary_key = True
            elif self._match_keyword("UNIQUE") is not None:
                unique = True
            elif self._match_keyword("DEFAULT") is not None:
                default = self.parse_expression()
            else:
                break
        return ColumnDefinition(
            name=name,
            data_type=data_type,
            not_null=not_null,
            primary_key=primary_key,
            unique=unique,
            default=default,
        )

    def _parse_data_type(self) -> DataType:
        token = self._current()
        if token.type is not TokenType.KEYWORD or token.lexeme not in DATA_TYPE_KEYWORDS:
            self._error("expected a data type")
        self._advance()
        parameters: tuple[int, ...] = ()
        if self._match_type(TokenType.LPAREN) is not None:
            values = [self._expect_integer_literal()]
            while self._match_type(TokenType.COMMA) is not None:
                values.append(self._expect_integer_literal())
            self._expect_type(TokenType.RPAREN, "')'")
            parameters = tuple(values)
        return DataType(name=token.lexeme, parameters=parameters)

    def _parse_create_index(self, *, unique: bool) -> CreateIndexStatement:
        if unique:
            self._expect_keyword("UNIQUE")
        self._expect_keyword("INDEX")
        if_not_exists = self._parse_if_not_exists()
        index_name = self._expect_identifier()
        self._expect_keyword("ON")
        table = self._expect_identifier()
        self._expect_type(TokenType.LPAREN, "'('")
        names = [self._expect_identifier()]
        while self._match_type(TokenType.COMMA) is not None:
            names.append(self._expect_identifier())
        self._expect_type(TokenType.RPAREN, "')'")
        return CreateIndexStatement(
            index_name=index_name,
            table=table,
            columns=tuple(names),
            unique=unique,
            if_not_exists=if_not_exists,
        )
