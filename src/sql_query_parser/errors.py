"""Jerarquía de excepciones tipadas del parser.

Ningún error de sintaxis se traga en silencio ni se reporta como
`Exception` genérica: todo error localizado en el texto fuente lleva
`position` (línea/columna) y un mensaje legible lista para mostrarse al
usuario, sin que quien la captura tenga que rehacer el trabajo de
localización.
"""

from .models import Position


class SqlParserError(Exception):
    """Raíz de la jerarquía de errores de `sql_query_parser`."""


class SqlSyntaxError(SqlParserError):
    """Error de sintaxis localizado en una posición concreta del texto fuente."""

    def __init__(self, message: str, position: Position) -> None:
        self.message = message
        self.position = position
        super().__init__(f"{position}: {message}")


class LexicalError(SqlSyntaxError):
    """Error durante la tokenización: carácter inválido, cadena o
    identificador entrecomillado sin cerrar, comentario de bloque sin
    cerrar, literal numérico mal formado."""


class UnsupportedFeatureError(SqlSyntaxError):
    """Construcción sintácticamente reconocible pero excluida a propósito del
    subconjunto de SQL documentado. Hoy la única que el parser detecta como
    tal es la tabla derivada (`FROM (SELECT ...)`); `WITH`/`UNION`/`CASE`
    fallan como `SqlSyntaxError` ordinario porque no llegan a reconocerse.

    Se distingue de `SqlSyntaxError` genérico porque el mensaje no describe
    un error de gramática sino una limitación de alcance conocida y
    documentada en el README — el llamador puede decidir tratarla distinto
    (p.ej. no sugerir "revisa la sintaxis" sino "no soportado todavía").
    """
