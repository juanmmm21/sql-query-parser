# sql-query-parser

Part of the **[strata-database-engine](https://github.com/juanmmm21/strata-database-engine)** ecosystem — Query Engine category, component 6 of 11.

Repository: [github.com/juanmmm21/sql-query-parser](https://github.com/juanmmm21/sql-query-parser)

## What it is and what problem it solves

`sql-query-parser` is a hand-written lexer and recursive-descent parser for a documented subset of SQL. It has no external parsing dependencies — no ANTLR, no PLY, no grammar-generator of any kind — and produces a typed, immutable AST (Python dataclasses) instead of an untyped parse tree or a bag of dictionaries.

Every relational database engine needs to turn SQL text into a structure a query planner can reason about. That translation step has to reject malformed input with a message a human can act on (not a stack trace pointing at the tokenizer's internals), and it has to make operator precedence and associativity *provably* correct rather than "correct on the examples I tried." This module solves exactly that translation step, and nothing else: it does not validate that tables or columns exist, does not resolve types, does not plan anything — those are the job of `cost-based-query-optimizer` downstream.

## Role in `strata-database-engine`

```text
                    ┌──────────────────┐
   SQL text  ─────▶ │ sql-query-parser │ ─────▶  typed AST
                    └──────────────────┘
                              │
                              ▼
                 cost-based-query-optimizer
                              │
                              ▼
                 vectorized-execution-engine
                              │
        (storage + transaction layers join here)
                              │
                              ▼
                          nanosql
```

This module sits at the head of the query-engine branch of the pipeline and has **zero dependencies** on any other `strata-database-engine` component — it can be (and was) developed and published in complete isolation. The storage layer (`write-ahead-log-recovery` → `bplus-tree-storage-engine` / `lsm-tree-engine`) and the concurrency layer (`mvcc-transaction-manager` / `lock-manager-deadlock-detector`) develop independently along a parallel branch; both branches only meet inside `nanosql`. This module *produces* the typed AST that `cost-based-query-optimizer` will consume to build a logical and then a physical execution plan. Per the ecosystem's integration rule, there is no direct cross-repository import between siblings: `cost-based-query-optimizer` does not `pip install` this package as of today. The two are wired together only inside `nanosql`, the flagship app that assembles all ten components into one running engine.

## Goal / skills demonstrated

- Hand-written lexical analysis: single-pass tokenization with exact line/column tracking, string and quoted-identifier escaping, numeric literal grammars (integers, decimals, exponents), and line/block comment handling — no regex-soup tokenizer.
- Recursive-descent parsing with explicit operator-precedence climbing (`OR → AND → NOT → comparison → additive → multiplicative → unary → primary`), including the non-obvious rule that SQL comparisons do not chain (`a = b = c` is a syntax error, not a boolean expression).
- A typed AST as a first-class deliverable: every node is a `frozen`, `slots`-based dataclass, so downstream consumers get structural equality, immutability, and `mypy --strict` guarantees for free.
- Deliberate, documented scope control: recognizing a construct is out of scope (a derived table in `FROM`) and rejecting it with a specific, actionable error is treated as a first-class requirement, not an afterthought.
- Property-based testing against an independent reference model: a seeded random query generator that builds the expected AST *and* the SQL text through two separate code paths, then asserts the parser reproduces the generator's tree exactly — for hundreds of random inputs, not a handful of hand-picked ones.

## How it works

### Pipeline

```text
source text ──▶ Lexer.tokenize() ──▶ list[Token] ──▶ Parser.parse_statement() ──▶ Statement (AST)
```

The lexer runs once, eagerly, over the whole input (`Lexer` is not a generator) — the SQL subset this project targets never produces inputs large enough to justify a streaming/lazy tokenizer, and an eager list is simpler to reason about and to unit-test in isolation. The parser is a classic recursive-descent parser over that token list; it never looks back at the source text, only at `Token.position` for error reporting.

### Supported grammar (documented subset)

The subset covers the everyday core of SQL-92/SQL:1999 DDL and DML. In EBNF-ish notation (`[]` optional, `{}` zero-or-more, `|` alternation):

```text
statement       := select_stmt | insert_stmt | update_stmt | delete_stmt
                  | create_table_stmt | create_index_stmt

select_stmt     := SELECT [DISTINCT] select_list
                    FROM table_ref { join }
                    [WHERE expr]
                    [GROUP BY expr {',' expr}]
                    [HAVING expr]
                    [ORDER BY order_item {',' order_item}]
                    [LIMIT integer [OFFSET integer]]

select_list     := '*' | select_item {',' select_item}
select_item     := expr [[AS] alias]
table_ref       := identifier [[AS] alias]
join            := [INNER | LEFT [OUTER] | RIGHT [OUTER] | FULL [OUTER]] JOIN table_ref ON expr
order_item      := expr [ASC | DESC]

insert_stmt     := INSERT INTO identifier ['(' identifier {',' identifier} ')']
                    VALUES value_row {',' value_row}
value_row       := '(' expr {',' expr} ')'

update_stmt     := UPDATE identifier SET assignment {',' assignment} [WHERE expr]
assignment      := identifier '=' expr

delete_stmt     := DELETE FROM identifier [WHERE expr]

create_table_stmt := CREATE TABLE [IF NOT EXISTS] identifier
                      '(' column_def {',' column_def} ')'
column_def      := identifier data_type { column_constraint }
column_constraint := NOT NULL | PRIMARY KEY | UNIQUE | DEFAULT expr
data_type       := type_keyword ['(' integer [',' integer] ')']

create_index_stmt := CREATE [UNIQUE] INDEX [IF NOT EXISTS] identifier
                      ON identifier '(' identifier {',' identifier} ')'

expr            := or_expr
or_expr         := and_expr { OR and_expr }
and_expr        := not_expr { AND not_expr }
not_expr        := NOT not_expr | comparison
comparison      := additive [ [NOT] LIKE additive
                             | [NOT] IN '(' ( select_stmt | expr {',' expr} ) ')'
                             | [NOT] BETWEEN additive AND additive
                             | IS [NOT] NULL
                             | ('=' | '<>' | '!=' | '<' | '<=' | '>' | '>=') additive ]
additive        := multiplicative { ('+' | '-') multiplicative }
multiplicative  := unary { ('*' | '/' | '%') unary }
unary           := ('-' | '+') unary | primary
primary         := literal | column_ref | function_call | placeholder | '*'
                  | '(' select_stmt ')'   -- scalar subquery (Subquery AST node)
                  | '(' expr ')'
placeholder     := '?'   -- positional query parameter, indexed per statement in order of appearance;
                         -- placeholders inside a subquery continue the outer statement's numbering
column_ref      := identifier ['.' identifier]
function_call   := identifier '(' ( '*' | [DISTINCT] [expr {',' expr}] ) ')'
literal         := INTEGER | FLOAT | STRING | TRUE | FALSE | NULL
```

Notes worth calling out explicitly:

- **Comparisons do not chain.** `comparison` appears exactly once per `not_expr`, never in a loop — `a = b = c` is a syntax error, matching standard SQL (there is no boolean-of-comparisons coercion).
- **Identifiers** may be unquoted (`[A-Za-z_][A-Za-z0-9_]*`, case-insensitive for keyword matching, case *preserved* in the AST) or double-quoted with `""` as the escape for an embedded `"` (`"a""b"` → `a"b`), letting a quoted identifier collide textually with a reserved word.
- **Comments**: `-- line comments` and `/* block comments */` (non-nesting) are recognized by the lexer and never reach the parser.
- **`INSERT` without an explicit column list** (`INSERT INTO t VALUES (...)`) is supported; `InsertStatement.columns` is an empty tuple in that case, meaning "all columns, in table-definition order" — resolving that order is `nanosql`'s job, not this parser's.
- **Subqueries are expressions, not table sources.** `(SELECT ...)` as a primary expression produces a `Subquery` node (a scalar subquery), and `x [NOT] IN (SELECT ...)` produces a dedicated `InSubquery` node — deliberately distinct from `InList`, because `x IN ((SELECT ...))` (a one-element list whose element is a *scalar* subquery, at most one row) does not mean the same thing as `x IN (SELECT ...)` (membership in a result of any size). The parser imposes no semantic restrictions on subqueries: single-column projection, single-row scalar results and the no-correlation rule are all checked by the consumer (`nanosql`), which is the first layer with catalog access. Subqueries share the parser instance with their outer statement, so `?` placeholders inside them continue the outer statement's per-statement numbering.

### Explicitly excluded constructs

These are recognized as *known gaps*, not silently mis-parsed. Some fail with a specific `UnsupportedFeatureError`; the rest fail as an ordinary `SqlSyntaxError` because they simply aren't part of the grammar above:

| Construct | Example | Behavior |
|---|---|---|
| Derived tables (subqueries in `FROM`) | `SELECT * FROM (SELECT id FROM u) d` | `UnsupportedFeatureError`, points at the opening `(` |
| `EXISTS (SELECT ...)` | `SELECT * FROM t WHERE EXISTS (SELECT 1 FROM u)` | `SqlSyntaxError` (`EXISTS` is not a primary expression) |
| `UNION` / `INTERSECT` / `EXCEPT` | `SELECT a FROM t UNION SELECT b FROM u` | `SqlSyntaxError` (not part of `select_stmt`) |
| Common table expressions (`WITH`) | `WITH cte AS (...) SELECT * FROM cte` | `SqlSyntaxError` |
| `CASE WHEN ... THEN ... END` | `SELECT CASE WHEN a=1 THEN 'x' END FROM t` | `SqlSyntaxError` |
| Window functions (`OVER (...)`) | `SELECT rank() OVER (...) FROM t` | `SqlSyntaxError` |
| `CAST(expr AS type)` | `SELECT CAST(a AS INTEGER)` | `SqlSyntaxError` |
| `ALTER TABLE`, `DROP`, views, triggers | — | Not part of `statement` at all |
| Transactions (`BEGIN`/`COMMIT`/`ROLLBACK`) | — | Out of scope: this module parses statements, not a session protocol |

The line is deliberate: this parser covers the fragment of SQL a query optimizer and a vectorized executor need to demonstrate real relational query processing (`cost-based-query-optimizer`, `vectorized-execution-engine`), without growing into a full SQL:2016 grammar that would dwarf the rest of the ecosystem.

## Architecture

```text
src/sql_query_parser/
├── models.py        Token/Position + the full typed AST (frozen, slotted dataclasses)
├── errors.py         SqlParserError → SqlSyntaxError → {LexicalError, UnsupportedFeatureError}
├── protocols.py       ErrorFormatter Protocol + CaretErrorFormatter (default impl)
├── lexer.py            Lexer: str -> list[Token]
├── parser.py            Parser: list[Token] -> Statement (expression + statement grammar)
├── pipeline.py           tokenize()/parse()/parse_many() — the public orchestration layer
└── __main__.py            CLI: tokenize/parse/check/repl subcommands
```

`Parser` never imports `Lexer` — it only depends on `list[Token]`, so the grammar can be (and is) unit-tested with hand-built synthetic token streams, independent of whatever the lexer does. `ErrorFormatter` is a `Protocol`, not a concrete class baked into the CLI: `nanosql`'s REPL can supply its own formatter (e.g. one that emits JSON instead of a caret-annotated line) without touching a single line of the lexer or parser.

## Requirements and installation

- Python >= 3.11
- No runtime dependencies

```bash
git clone https://github.com/juanmmm21/sql-query-parser.git
cd sql-query-parser
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

### CLI

```bash
# tokenize a query and print each token with its line:column
sql-query-parser tokenize "SELECT a, b FROM t WHERE a = 1"

# parse one or more ';'-separated statements and print the resulting AST
sql-query-parser parse "SELECT * FROM t; INSERT INTO t VALUES (1)"

# validate syntax only: prints OK or a caret-annotated syntax error, exit code 1 on failure
sql-query-parser check --file query.sql

# interactive REPL: accumulates lines until ';', keeps running after a syntax error
sql-query-parser repl
```

Example of the default error rendering (`CaretErrorFormatter`):

```
$ sql-query-parser check "SELECT FROM t"
Syntax error at line 1, column 8: expected an expression, found 'FROM'
SELECT FROM t
       ^
```

### Programmatic usage

```python
from sql_query_parser import parse, parse_many, tokenize, SqlSyntaxError
from sql_query_parser.models import SelectStatement

statement = parse("SELECT id, name FROM users WHERE active = TRUE ORDER BY id LIMIT 10")
assert isinstance(statement, SelectStatement)
assert statement.from_table.name == "users"
assert statement.limit == 10

statements = parse_many("SELECT 1; SELECT 2;")  # -> list[Statement], length 2

try:
    parse("SELECT FROM t")
except SqlSyntaxError as error:
    print(error.position.line, error.position.column, error.message)

tokens = tokenize("SELECT * FROM t")  # list[Token], for tooling (syntax highlighting, etc.)
```

## Data format / interface exposed to `nanosql`

The public contract of this module is the `Statement` hierarchy in `models.py` — not a serialization format, since the AST is meant to be consumed in-process, not persisted or sent over a wire. Everything downstream (`cost-based-query-optimizer`, and ultimately `nanosql`) is expected to `match`/`isinstance`-dispatch over these node types:

- **Statements**: `SelectStatement`, `InsertStatement`, `UpdateStatement`, `DeleteStatement`, `CreateTableStatement`, `CreateIndexStatement` (all inherit the marker base `Statement`).
- **Expressions**: `Literal`, `ColumnRef`, `Star`, `Placeholder` (positional `?` parameter carrying its 0-based per-statement index), `BinaryOp`, `UnaryOp`, `Between`, `InList`, `Like`, `IsNull`, `FunctionCall`, `Subquery` (a scalar `(SELECT ...)` wrapping a full `SelectStatement`), `InSubquery` (`expr [NOT] IN (SELECT ...)`) — all inherit the marker base `Expression`.
- **Shared fragments**: `SelectItem`, `TableRef`, `Join`, `OrderByItem`, `Assignment`, `DataType`, `ColumnDefinition`.

Every node is `@dataclass(frozen=True, slots=True)`: structurally comparable with `==` (which the property-based tests rely on directly), hashable-by-value where all fields are hashable, and impossible to mutate in place — a consumer that wants a modified plan builds a new node, it never patches this one. This module deliberately does not import or depend on anything from `cost-based-query-optimizer`; the coupling is one-directional and exists only as "the shape of the dataclasses both sides agree on," wired together for real only inside `nanosql`.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
mypy --strict src/
```

155 tests across eight files:

- `test_lexer.py` (43): identifiers, keywords, quoted identifiers with escaping, numeric/string/boolean/null literals, every operator, comments, and line/column tracking across multiple lines.
- `test_parser_select.py` (34): `SELECT` with `DISTINCT`, aliases, all four `JOIN` kinds, `WHERE`/`GROUP BY`/`HAVING`/`ORDER BY`/`LIMIT`/`OFFSET`, aggregate function calls, and the explicitly-excluded-construct battery (derived tables, `UNION`, `WITH`, `CASE`).
- `test_subqueries.py` (14): scalar subqueries as primary expressions (in `WHERE`, in the select list, inside arithmetic), `[NOT] IN (SELECT ...)` vs `IN ((SELECT ...))` node distinction, subqueries nested in subqueries, placeholder numbering across subquery boundaries, and the syntax-error battery for malformed subqueries.
- `test_parser_dml.py` (10) / `test_parser_ddl.py` (10): `INSERT`/`UPDATE`/`DELETE` and `CREATE TABLE`/`CREATE INDEX`, including multi-row inserts, column constraints in any order, and parameterized data types (`VARCHAR(255)`, `DECIMAL(10, 2)`).
- `test_placeholders.py` (8): `?` tokenization (including `?` inside string literals not being a placeholder), per-statement 0-based indexing in order of appearance across every DML statement kind, index reset between the statements of `parse_many`, and rejection of `?` outside expression positions.
- `test_errors.py` (30): a parametrized battery of invalid queries asserting the **exact** line/column the parser reports (computed by locating the offending substring in the source, not hand-counted — robust to test-text edits), the exception hierarchy, and the `CaretErrorFormatter` output shape.
- `test_properties.py` (6 pytest cases, driving ~1,800 generated queries): a seeded (`random.Random(seed)`, 6 fixed seeds total) query generator that builds SQL text and its expected AST through two independent code paths — every compound subexpression is explicitly parenthesized during generation specifically so that *arbitrary* random tree shapes (not just hand-picked ones) are safe to compare, without needing to re-derive precedence rules inside the generator itself. Covers all six statement kinds plus boolean expression trees up to depth 5.

## Benchmarks

Parsing is not the hot path of a database engine the way page I/O or a hash join is — a query is parsed once and then executed many times — so this section exists mainly to confirm there's no accidental quadratic behavior, not to chase microseconds. Measured on this development machine (CPython, single-threaded, `timeit`, no warmup beyond what `timeit` already discounts):

- Six representative statements (a bare `SELECT`, a filtered `SELECT`, a `JOIN` with `ORDER BY`/`LIMIT`, a multi-row `INSERT`, an `UPDATE` with `IN`, and a `CREATE TABLE` with constraints), 20,000 repetitions each: **~23,700 statements/second**, ~42 µs/statement average.
- Breaking that down for the `JOIN` query specifically (the most complex of the six): tokenizing alone runs at ~23,600 calls/sec (~42 µs), while tokenize+parse together runs at ~14,700 calls/sec (~68 µs) — **building the AST costs roughly as much as tokenizing did** (+38% over tokenizing alone). That overhead is dataclass instantiation and tuple construction for the AST nodes, not the character-by-character scanning; it's the expected cost of choosing a typed, immutable tree over untyped dicts/tuples, and it's still two orders of magnitude below what a single page fault to disk costs in `bplus-tree-storage-engine`.

## Troubleshooting

- **"expected an expression, found '...'"** — the parser hit a token that cannot start a `primary` (see grammar above) where it expected one. The most common cause is a missing operand around a comma or a keyword typo.
- **`UnsupportedFeatureError` mentioning derived tables** — the query uses `(SELECT ...)` as a table source in `FROM`/`JOIN`, which this parser recognizes but has deliberately excluded (see the exclusion table above). Subqueries in *expression* positions (scalar, `[NOT] IN`) are supported; only the table-source position is out of scope.
- **A `NOT` produces "expected LIKE, IN or BETWEEN after NOT"** — `NOT` immediately after a comparison's left-hand side is only legal as `NOT LIKE`/`NOT IN`/`NOT BETWEEN`. A leading `NOT` in front of a whole boolean expression (`WHERE NOT (a = 1)`) is handled separately and does not hit this path.
- **Column/table names silently accepted even though they don't exist in any schema** — expected: this module performs no semantic/catalog validation. Existence and type checking happen in `cost-based-query-optimizer`, which is the first consumer with access to table metadata.
- **`mypy --strict` failures after modifying `models.py`** — every AST dataclass is `frozen=True, slots=True`; make sure a new field has a type on every dataclass, and remember dataclass field ordering requires all-defaulted fields after the first defaulted one.

## License

MIT — see [LICENSE](LICENSE).
