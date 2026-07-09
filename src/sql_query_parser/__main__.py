"""CLI de demostración: `sql-query-parser {tokenize,parse,check,repl}`."""

import argparse
import sys
from collections.abc import Sequence

from .errors import SqlSyntaxError
from .models import Statement, Token
from .pipeline import parse_many, tokenize
from .protocols import CaretErrorFormatter, ErrorFormatter


def _read_source(args: argparse.Namespace) -> str:
    if args.file is not None:
        with open(args.file, encoding="utf-8") as handle:
            return handle.read()
    if args.sql is not None:
        return str(args.sql)
    return sys.stdin.read()


def _print_tokens(tokens: list[Token]) -> None:
    for token in tokens:
        print(f"{token.position}\t{token.type.name}\t{token.lexeme!r}")


def _print_statements(statements: list[Statement]) -> None:
    for index, statement in enumerate(statements):
        print(f"-- statement {index + 1} --")
        print(statement)


def _cmd_tokenize(args: argparse.Namespace, formatter: ErrorFormatter) -> int:
    source = _read_source(args)
    try:
        tokens = tokenize(source)
    except SqlSyntaxError as error:
        print(formatter.format(error, source), file=sys.stderr)
        return 1
    _print_tokens(tokens)
    return 0


def _cmd_parse(args: argparse.Namespace, formatter: ErrorFormatter) -> int:
    source = _read_source(args)
    try:
        statements = parse_many(source)
    except SqlSyntaxError as error:
        print(formatter.format(error, source), file=sys.stderr)
        return 1
    _print_statements(statements)
    return 0


def _cmd_check(args: argparse.Namespace, formatter: ErrorFormatter) -> int:
    source = _read_source(args)
    try:
        parse_many(source)
    except SqlSyntaxError as error:
        print(formatter.format(error, source), file=sys.stderr)
        return 1
    print("OK")
    return 0


def _cmd_repl(_args: argparse.Namespace, formatter: ErrorFormatter) -> int:
    """Bucle interactivo: acumula líneas hasta ver un `;`, parsea ese
    fragmento y sigue leyendo sin salir del proceso ante un error de
    sintaxis (a diferencia de `parse`/`check`, pensados para un solo intento
    en scripts)."""
    print("sql-query-parser REPL — end a statement with ';', empty line to quit.")
    buffer: list[str] = []
    while True:
        try:
            prompt = "sql> " if not buffer else " ...> "
            line = input(prompt)
        except EOFError:
            print()
            return 0
        if not line.strip() and not buffer:
            return 0
        buffer.append(line)
        if ";" not in line:
            continue
        source = "\n".join(buffer)
        buffer = []
        try:
            for statement in parse_many(source):
                print(statement)
        except SqlSyntaxError as error:
            print(formatter.format(error, source), file=sys.stderr)


def _build_arg_parser() -> argparse.ArgumentParser:
    arg_parser = argparse.ArgumentParser(
        prog="sql-query-parser",
        description="Hand-written lexer/parser for a documented SQL subset.",
    )
    subparsers = arg_parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("tokenize", "print the token stream for a SQL source"),
        ("parse", "parse SQL source and print the resulting AST"),
        ("check", "validate SQL syntax, printing OK or a syntax error"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("sql", nargs="?", help="SQL text (reads stdin if omitted)")
        sub.add_argument("--file", help="read SQL source from this file instead")

    subparsers.add_parser("repl", help="interactive read-eval-print loop")

    return arg_parser


def main(argv: Sequence[str] | None = None) -> int:
    arg_parser = _build_arg_parser()
    args = arg_parser.parse_args(argv)
    formatter: ErrorFormatter = CaretErrorFormatter()

    handlers = {
        "tokenize": _cmd_tokenize,
        "parse": _cmd_parse,
        "check": _cmd_check,
        "repl": _cmd_repl,
    }
    return handlers[args.command](args, formatter)


if __name__ == "__main__":
    raise SystemExit(main())
