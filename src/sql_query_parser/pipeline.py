"""Orquestación de alto nivel: texto SQL -> tokens -> AST.

Junto con `__init__.py`, es el punto de entrada público que consumirán tanto
la CLI de este mismo paquete como, más adelante, `cost-based-query-optimizer`
dentro de `nanosql`.
"""

from .lexer import Lexer
from .models import Statement, Token
from .parser import Parser


def tokenize(source: str) -> list[Token]:
    """Tokeniza `source` completo. Puede lanzar `LexicalError`."""
    return Lexer(source).tokenize()


def parse(source: str) -> Statement:
    """Parsea `source` como una única sentencia SQL (con `;` final opcional).

    Lanza `SqlSyntaxError` (o su subclase `UnsupportedFeatureError`), con la
    posición exacta del error, si `source` no es sintácticamente válido.
    """
    tokens = tokenize(source)
    return Parser(tokens).parse_single_statement()


def parse_many(source: str) -> list[Statement]:
    """Parsea `source` como una o más sentencias separadas por `;`."""
    tokens = tokenize(source)
    return Parser(tokens).parse_program()
