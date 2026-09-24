"""Extract syntax and source locations without importing or executing repository code."""

import ast
import sys
from dataclasses import dataclass

PARSER_VERSION = f"python-ast-v1-py{sys.version_info.major}.{sys.version_info.minor}"


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    start_line: int
    end_line: int
    parent_name: str | None


@dataclass(frozen=True)
class Import:
    module: str
    name: str | None
    alias: str | None
    level: int
    start_line: int
    end_line: int
    scope: str | None


@dataclass(frozen=True)
class ParseResult:
    status: str
    symbols: tuple[Symbol, ...] = ()
    imports: tuple[Import, ...] = ()
    error: str | None = None


class Symbols(ast.NodeVisitor):
    def __init__(self):
        self.symbols: list[Symbol] = []
        self.imports: list[Import] = []
        self.scope: list[Symbol] = []

    @property
    def parent(self) -> str | None:
        return self.scope[-1].name if self.scope else None

    def qualified(self, name: str) -> str:
        return f"{self.parent}.{name}" if self.parent else name

    def definition(self, node, kind: str) -> None:
        symbol = Symbol(
            self.qualified(node.name),
            kind,
            min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)]),
            node.end_lineno,
            self.parent,
        )
        self.symbols.append(symbol)
        self.scope.append(symbol)
        # Only the body declares names in this lexical scope; decorators/defaults are expressions.
        for child in node.body:
            self.visit(child)
        self.scope.pop()

    def visit_ClassDef(self, node):
        self.definition(node, "class")

    def visit_FunctionDef(self, node):
        kind = "method" if self.scope and self.scope[-1].kind == "class" else "function"
        self.definition(node, kind)

    visit_AsyncFunctionDef = visit_FunctionDef

    def declaration(self, target, node):
        if isinstance(target, ast.Name):
            self.symbols.append(
                Symbol(
                    self.qualified(target.id), "variable", node.lineno, node.end_lineno, self.parent
                )
            )
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self.declaration(item, node)

    def visit_Assign(self, node):
        for target in node.targets:
            self.declaration(target, node)

    def visit_AnnAssign(self, node):
        self.declaration(node.target, node)

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append(
                Import(alias.name, None, alias.asname, 0, node.lineno, node.end_lineno, self.parent)
            )

    def visit_ImportFrom(self, node):
        for alias in node.names:
            self.imports.append(
                Import(
                    node.module or "",
                    alias.name,
                    alias.asname,
                    node.level,
                    node.lineno,
                    node.end_lineno,
                    self.parent,
                )
            )


def parse_source(content: str, language: str) -> ParseResult:
    if language != "python":
        return ParseResult("text_fallback")
    try:
        tree = ast.parse(content.removeprefix("\ufeff"))
        visitor = Symbols()
        visitor.visit(tree)
    except (SyntaxError, ValueError, RecursionError) as exc:
        line = getattr(exc, "lineno", None)
        return ParseResult("parse_error", error=f"{type(exc).__name__} at line {line or 'unknown'}")
    return ParseResult("parsed", tuple(visitor.symbols), tuple(visitor.imports))
