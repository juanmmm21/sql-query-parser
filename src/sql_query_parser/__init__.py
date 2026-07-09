"""Lexer y parser manuales para un subconjunto documentado de SQL.

API pública mínima: `tokenize`/`parse`/`parse_many` de `pipeline`, los nodos
del AST de `models`, y la jerarquía de excepciones de `errors`. El resto de
símbolos (`Lexer`, `Parser`) son detalle de implementación reexportado por
conveniencia, no una superficie estable.
"""

from .errors import LexicalError, SqlParserError, SqlSyntaxError, UnsupportedFeatureError
from .lexer import Lexer
from .parser import Parser
from .pipeline import parse, parse_many, tokenize

__all__ = [
    "Lexer",
    "LexicalError",
    "Parser",
    "SqlParserError",
    "SqlSyntaxError",
    "UnsupportedFeatureError",
    "parse",
    "parse_many",
    "tokenize",
]
