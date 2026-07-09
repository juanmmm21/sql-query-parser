"""Interfaces pluggables del módulo.

`ErrorFormatter` es la única abstracción que vale la pena exponer como
`Protocol` en un parser: cómo se renderiza un `SqlSyntaxError` en texto es
una decisión del consumidor, no del parser. La CLI de este mismo paquete usa
`CaretErrorFormatter` (estilo compilador, con la línea fuente y un `^` bajo
la columna del error); `nanosql` podría inyectar un formateador que emita
JSON estructurado para su REPL sin tocar una sola línea del lexer/parser.
"""

from typing import Protocol

from .errors import SqlSyntaxError


class ErrorFormatter(Protocol):
    """Convierte un `SqlSyntaxError` en un mensaje de texto para el usuario."""

    def format(self, error: SqlSyntaxError, source: str) -> str:
        """`source` es el texto SQL original completo, necesario para poder
        extraer la línea concreta donde ocurrió el error."""
        ...


class CaretErrorFormatter:
    """Formateador por defecto: línea del error + un `^` bajo la columna
    exacta, al estilo de los mensajes de un compilador."""

    def format(self, error: SqlSyntaxError, source: str) -> str:
        lines = source.splitlines() or [""]
        line_index = error.position.line - 1
        source_line = lines[line_index] if 0 <= line_index < len(lines) else ""
        caret_line = " " * (error.position.column - 1) + "^"
        return (
            f"Syntax error at line {error.position.line}, "
            f"column {error.position.column}: {error.message}\n"
            f"{source_line}\n"
            f"{caret_line}"
        )
