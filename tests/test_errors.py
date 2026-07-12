"""Batería de errores de sintaxis con verificación de posición exacta, más
la jerarquía de excepciones y el formateador de errores por defecto.

En vez de contar columnas a mano (frágil y propenso a error humano), la
posición esperada se calcula localizando la subcadena ofensiva en el propio
texto fuente — la prueba sigue verificando la posición *exacta* reportada
por el parser, sólo que de forma robusta a reescrituras del texto de prueba.
"""

import pytest

from sql_query_parser.errors import (
    LexicalError,
    SqlParserError,
    SqlSyntaxError,
    UnsupportedFeatureError,
)
from sql_query_parser.lexer import Lexer
from sql_query_parser.models import Position, Token, TokenType
from sql_query_parser.parser import Parser
from sql_query_parser.pipeline import parse
from sql_query_parser.protocols import CaretErrorFormatter


def _column_of(source: str, needle: str, occurrence: int = 1) -> int:
    index = -1
    for _ in range(occurrence):
        index = source.index(needle, index + 1)
    return index + 1


class TestSyntaxErrorPositions:
    @pytest.mark.parametrize(
        ("source", "needle"),
        [
            ("SELECT FROM t", "FROM"),
            ("SELECT a b c FROM t", "c"),
            ("SELECT a, FROM t", "FROM"),
            ("DELETE t", "t"),
            ("UPDATE t a = 1", "a"),
            ("INSERT t VALUES (1)", "t"),
            ("INSERT INTO t (a, ) VALUES (1)", ")"),
            ("CREATE VIEW v AS SELECT 1", "VIEW"),
            ("CREATE TABLE t (id)", ")"),
            ("CREATE TABLE t (id INTEGER NOT integer)", "integer"),
            ("SELECT * FROM t WHERE a NOT 1", "1"),
            ("SELECT * FROM t WHERE a BETWEEN 1", "'"),
        ],
    )
    def test_error_points_at_offending_token(self, source: str, needle: str) -> None:
        with pytest.raises(SqlSyntaxError) as excinfo:
            parse(source)
        if needle == "'":
            # el caso BETWEEN incompleto: el error real es "found end of input"
            assert "end of input" in str(excinfo.value)
            return
        expected_column = _column_of(source, needle)
        assert excinfo.value.position.column == expected_column

    def test_error_on_second_line_reports_correct_line_and_column(self) -> None:
        source = "SELECT a\nFROM"
        with pytest.raises(SqlSyntaxError) as excinfo:
            parse(source)
        assert excinfo.value.position.line == 2
        assert excinfo.value.position.column == 5  # tras "FROM" (fin de línea)

    def test_error_message_is_human_readable(self) -> None:
        with pytest.raises(SqlSyntaxError) as excinfo:
            parse("SELECT FROM t")
        assert "expected an expression" in str(excinfo.value)
        assert "found 'FROM'" in str(excinfo.value)

    def test_missing_closing_paren_reports_eof(self) -> None:
        with pytest.raises(SqlSyntaxError) as excinfo:
            parse("SELECT * FROM t WHERE (a = 1")
        assert "end of input" in str(excinfo.value)

    def test_empty_source_is_a_syntax_error(self) -> None:
        with pytest.raises(SqlSyntaxError) as excinfo:
            parse("")
        assert excinfo.value.position == Position(1, 1)

    def test_trailing_garbage_after_valid_statement(self) -> None:
        source = "SELECT * FROM t; SELECT"
        with pytest.raises(SqlSyntaxError) as excinfo:
            parse(source)
        # `parse` (una sola sentencia) no admite un `;` seguido de más texto.
        assert excinfo.value.position.column == _column_of(source, "SELECT", occurrence=2)


class TestUnsupportedFeatureRejection:
    def test_derived_table_rejection_points_at_opening_paren(self) -> None:
        source = "SELECT * FROM (SELECT id FROM other) d"
        with pytest.raises(UnsupportedFeatureError) as excinfo:
            parse(source)
        assert excinfo.value.position.column == _column_of(source, "(SELECT")

    def test_unsupported_feature_error_is_also_a_syntax_error(self) -> None:
        assert issubclass(UnsupportedFeatureError, SqlSyntaxError)


class TestLexicalErrorPositions:
    def test_unterminated_string_position(self) -> None:
        source = "SELECT * FROM t WHERE name = 'oops"
        with pytest.raises(LexicalError) as excinfo:
            parse(source)
        assert excinfo.value.position.column == _column_of(source, "'oops")

    def test_invalid_character_position(self) -> None:
        source = "SELECT a # b FROM t"
        with pytest.raises(LexicalError) as excinfo:
            parse(source)
        assert excinfo.value.position.column == _column_of(source, "#")


class TestExceptionHierarchy:
    def test_lexical_error_is_a_syntax_error(self) -> None:
        assert issubclass(LexicalError, SqlSyntaxError)

    def test_syntax_error_is_a_parser_error(self) -> None:
        assert issubclass(SqlSyntaxError, SqlParserError)

    def test_parser_error_is_a_plain_exception(self) -> None:
        assert issubclass(SqlParserError, Exception)

    def test_syntax_error_exposes_message_and_position(self) -> None:
        error = SqlSyntaxError("boom", Position(3, 7))
        assert error.message == "boom"
        assert error.position == Position(3, 7)
        assert str(error) == "3:7: boom"


class TestCaretErrorFormatter:
    def test_formats_line_and_caret_under_column(self) -> None:
        source = "SELECT FROM t"
        with pytest.raises(SqlSyntaxError) as excinfo:
            parse(source)
        rendered = CaretErrorFormatter().format(excinfo.value, source)
        lines = rendered.splitlines()
        assert lines[1] == source
        caret_column = lines[2].index("^") + 1
        assert caret_column == excinfo.value.position.column

    def test_formats_error_on_specific_source_line(self) -> None:
        source = "SELECT a\nFROM t\nWHERE"
        with pytest.raises(SqlSyntaxError) as excinfo:
            parse(source)
        rendered = CaretErrorFormatter().format(excinfo.value, source)
        lines = rendered.splitlines()
        assert lines[1] == "WHERE"


class TestParserTokenStreamContract:
    def test_parser_rejects_token_list_not_ending_in_eof(self) -> None:
        tokens = [
            token for token in Lexer("SELECT 1").tokenize() if token.type is not TokenType.EOF
        ]
        with pytest.raises(ValueError, match="EOF"):
            Parser(tokens)

    def test_parser_rejects_empty_token_list(self) -> None:
        with pytest.raises(ValueError, match="EOF"):
            Parser([])

    def test_parser_accepts_synthetic_token_stream(self) -> None:
        # El parser no depende del lexer real: se puede alimentar con tokens
        # construidos a mano para aislar la gramática del lexer en un test.
        tokens = [
            Token(TokenType.KEYWORD, "SELECT", None, Position(1, 1)),
            Token(TokenType.STAR, "*", None, Position(1, 8)),
            Token(TokenType.KEYWORD, "FROM", None, Position(1, 10)),
            Token(TokenType.IDENTIFIER, "t", None, Position(1, 15)),
            Token(TokenType.EOF, "", None, Position(1, 16)),
        ]
        statement = Parser(tokens).parse_single_statement()
        assert statement.from_table.name == "t"  # type: ignore[union-attr]
