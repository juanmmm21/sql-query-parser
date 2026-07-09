"""Tests del lexer: clasificación léxica, posiciones y errores."""

import pytest

from sql_query_parser.errors import LexicalError
from sql_query_parser.lexer import Lexer
from sql_query_parser.models import TokenType


def _types(source: str) -> list[TokenType]:
    return [token.type for token in Lexer(source).tokenize()]


def _lexemes(source: str) -> list[str]:
    return [token.lexeme for token in Lexer(source).tokenize()]


class TestIdentifiersAndKeywords:
    def test_unquoted_identifier(self) -> None:
        tokens = Lexer("my_table").tokenize()
        assert tokens[0].type is TokenType.IDENTIFIER
        assert tokens[0].lexeme == "my_table"

    def test_identifier_case_is_preserved(self) -> None:
        tokens = Lexer("MyTable").tokenize()
        assert tokens[0].lexeme == "MyTable"

    def test_keyword_recognized_case_insensitively(self) -> None:
        for spelling in ("SELECT", "select", "Select", "sElEcT"):
            tokens = Lexer(spelling).tokenize()
            assert tokens[0].type is TokenType.KEYWORD
            assert tokens[0].lexeme == "SELECT"

    def test_identifier_starting_with_underscore(self) -> None:
        tokens = Lexer("_private1").tokenize()
        assert tokens[0].type is TokenType.IDENTIFIER
        assert tokens[0].lexeme == "_private1"

    def test_quoted_identifier_preserves_case_and_spaces(self) -> None:
        tokens = Lexer('"Weird Column Name"').tokenize()
        assert tokens[0].type is TokenType.QUOTED_IDENTIFIER
        assert tokens[0].lexeme == "Weird Column Name"

    def test_quoted_identifier_escapes_doubled_quote(self) -> None:
        tokens = Lexer('"a""b"').tokenize()
        assert tokens[0].lexeme == 'a"b'

    def test_quoted_identifier_can_contain_reserved_word_text(self) -> None:
        tokens = Lexer('"select"').tokenize()
        assert tokens[0].type is TokenType.QUOTED_IDENTIFIER
        assert tokens[0].lexeme == "select"

    def test_unterminated_quoted_identifier_raises(self) -> None:
        with pytest.raises(LexicalError):
            Lexer('"unterminated').tokenize()


class TestNumericLiterals:
    @pytest.mark.parametrize(
        ("source", "expected"), [("0", 0), ("42", 42), ("007", 7), ("1000000", 1000000)]
    )
    def test_integer_literal(self, source: str, expected: int) -> None:
        token = Lexer(source).tokenize()[0]
        assert token.type is TokenType.INTEGER
        assert token.value == expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [("3.14", 3.14), ("0.5", 0.5), ("1e10", 1e10), ("1.5e-3", 1.5e-3), ("2E+2", 2e2)],
    )
    def test_float_literal(self, source: str, expected: float) -> None:
        token = Lexer(source).tokenize()[0]
        assert token.type is TokenType.FLOAT
        assert token.value == pytest.approx(expected)

    def test_dot_after_integer_without_digit_is_separate_token(self) -> None:
        # "1." sin dígitos detrás no es un FLOAT válido en esta gramática:
        # se tokeniza como INTEGER '1' seguido de un DOT, dejando que el
        # parser decida (en la práctica, error de sintaxis en ese contexto).
        tokens = Lexer("1.").tokenize()
        assert tokens[0].type is TokenType.INTEGER
        assert tokens[1].type is TokenType.DOT

    def test_malformed_exponent_is_not_consumed(self) -> None:
        # "1e" sin dígitos tras la 'e' no es un exponente válido: la 'e'
        # queda para el siguiente identificador, no se combina con el número.
        tokens = Lexer("1e x").tokenize()
        assert tokens[0].type is TokenType.INTEGER
        assert tokens[0].lexeme == "1"
        assert tokens[1].type is TokenType.IDENTIFIER
        assert tokens[1].lexeme == "e"


class TestStringLiterals:
    def test_simple_string(self) -> None:
        token = Lexer("'hello'").tokenize()[0]
        assert token.type is TokenType.STRING
        assert token.value == "hello"

    def test_empty_string(self) -> None:
        token = Lexer("''").tokenize()[0]
        assert token.value == ""

    def test_string_escapes_doubled_single_quote(self) -> None:
        token = Lexer("'it''s'").tokenize()[0]
        assert token.value == "it's"

    def test_unterminated_string_raises(self) -> None:
        with pytest.raises(LexicalError):
            Lexer("'unterminated").tokenize()

    def test_unterminated_string_error_position_is_opening_quote(self) -> None:
        with pytest.raises(LexicalError) as excinfo:
            Lexer("SELECT 'oops").tokenize()
        assert excinfo.value.position.column == 8


class TestBooleanAndNullLiterals:
    @pytest.mark.parametrize("spelling", ["TRUE", "true", "True"])
    def test_true_literal(self, spelling: str) -> None:
        token = Lexer(spelling).tokenize()[0]
        assert token.type is TokenType.BOOLEAN
        assert token.value is True

    @pytest.mark.parametrize("spelling", ["FALSE", "false"])
    def test_false_literal(self, spelling: str) -> None:
        token = Lexer(spelling).tokenize()[0]
        assert token.type is TokenType.BOOLEAN
        assert token.value is False

    def test_null_literal(self) -> None:
        token = Lexer("NULL").tokenize()[0]
        assert token.type is TokenType.NULL
        assert token.value is None


class TestOperatorsAndPunctuation:
    def test_all_comparison_operators(self) -> None:
        source = "= <> != < <= > >="
        assert _types(source) == [
            TokenType.EQ,
            TokenType.NEQ,
            TokenType.NEQ,
            TokenType.LT,
            TokenType.LE,
            TokenType.GT,
            TokenType.GE,
            TokenType.EOF,
        ]

    def test_arithmetic_operators(self) -> None:
        assert _types("+ - * / %") == [
            TokenType.PLUS,
            TokenType.MINUS,
            TokenType.STAR,
            TokenType.SLASH,
            TokenType.PERCENT,
            TokenType.EOF,
        ]

    def test_punctuation(self) -> None:
        assert _types(". , ( ) ;") == [
            TokenType.DOT,
            TokenType.COMMA,
            TokenType.LPAREN,
            TokenType.RPAREN,
            TokenType.SEMICOLON,
            TokenType.EOF,
        ]

    def test_lone_exclamation_mark_raises(self) -> None:
        with pytest.raises(LexicalError):
            Lexer("!").tokenize()

    def test_unknown_character_raises(self) -> None:
        with pytest.raises(LexicalError):
            Lexer("SELECT a # b").tokenize()


class TestCommentsAndWhitespace:
    def test_line_comment_is_skipped(self) -> None:
        assert _lexemes("SELECT 1 -- trailing comment\nFROM t") == [
            "SELECT",
            "1",
            "FROM",
            "t",
            "",
        ]

    def test_block_comment_is_skipped(self) -> None:
        assert _lexemes("SELECT /* mid */ 1") == ["SELECT", "1", ""]

    def test_multiline_block_comment_is_skipped(self) -> None:
        assert _lexemes("SELECT /* line1\nline2 */ 1") == ["SELECT", "1", ""]

    def test_unterminated_block_comment_raises(self) -> None:
        with pytest.raises(LexicalError):
            Lexer("SELECT /* never closed").tokenize()

    def test_empty_source_yields_only_eof(self) -> None:
        tokens = Lexer("").tokenize()
        assert len(tokens) == 1
        assert tokens[0].type is TokenType.EOF


class TestPositionTracking:
    def test_column_tracking_on_single_line(self) -> None:
        tokens = Lexer("SELECT a").tokenize()
        assert tokens[0].position.line == 1
        assert tokens[0].position.column == 1
        assert tokens[1].position.line == 1
        assert tokens[1].position.column == 8

    def test_line_and_column_reset_after_newline(self) -> None:
        tokens = Lexer("SELECT a\nFROM t").tokenize()
        from_token = tokens[2]
        assert from_token.lexeme == "FROM"
        assert from_token.position.line == 2
        assert from_token.position.column == 1

    def test_position_accounts_for_multiple_newlines(self) -> None:
        tokens = Lexer("a\n\n\nb").tokenize()
        assert tokens[0].position == tokens[0].position.__class__(1, 1)
        assert tokens[1].position.line == 4
        assert tokens[1].position.column == 1
