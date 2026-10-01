"""Tree-sitter syntax extraction; module targets and runtime calls stay unresolved."""

from functools import lru_cache

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from app.ingestion.parser import Export, Import, ParseResult, Symbol

FUNCTIONS = {
    "function_declaration",
    "generator_function_declaration",
    "function_expression",
    "generator_function",
    "arrow_function",
    "function_signature",
}
DECLARATIONS = {
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "class": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "internal_module": "module",
}
NAMES = {"identifier", "type_identifier", "property_identifier", "private_property_identifier"}


@lru_cache(maxsize=3)
def grammar(dialect: str) -> Language:
    if dialect == "typescript":
        return Language(tree_sitter_typescript.language_typescript())
    if dialect == "tsx":
        return Language(tree_sitter_typescript.language_tsx())
    return Language(tree_sitter_javascript.language())


def text(node: Node | None) -> str | None:
    return node.text.decode("utf-8") if node is not None else None


def has_token(node: Node, token: str) -> bool:
    return any(child.type == token for child in node.children)


def end_line(node: Node) -> int:
    # Tree-sitter ends are exclusive, while persisted source locations are inclusive.
    return node.end_point.row + (1 if node.end_point.column else 0)


class Syntax:
    def __init__(self):
        self.symbols = []
        self.imports = []
        self.exports = []
        self.limitations = set()
        self.scope = []

    @property
    def parent(self):
        return self.scope[-1].name if self.scope else None

    def definition(self, node, name, kind, span=None):
        span = span if span is not None else node
        qualified = f"{self.parent}.{name}" if self.parent else name
        symbol = Symbol(qualified, kind, span.start_point.row + 1, end_line(node), self.parent)
        self.symbols.append(symbol)
        if kind in {"variable", "type"}:
            return
        self.scope.append(symbol)
        body = node.child_by_field_name("body")
        if body is not None:
            self.visit(body)
        self.scope.pop()

    def module(self, node):
        source = node.child_by_field_name("source")
        if source is None:
            return None
        # Preserve literal specifier spelling; do not interpret escapes or resolve paths.
        return text(source)[1:-1]

    def import_statement(self, node):
        module = self.module(node)
        if module is None:
            self.limitations.add("TypeScript import-equals syntax is searchable text only.")
            return
        type_only = has_token(node, "type")

        def add(name=None, alias=None, only_type=False):
            self.imports.append(
                Import(
                    module,
                    name,
                    alias,
                    0,
                    node.start_point.row + 1,
                    end_line(node),
                    self.parent,
                    type_only or only_type,
                )
            )

        clause = next((c for c in node.named_children if c.type == "import_clause"), None)
        if clause is None:
            add()
            return
        for child in clause.named_children:
            if child.type == "identifier":
                add("default", text(child))
            elif child.type == "namespace_import":
                add("*", text(child.named_children[-1]))
            elif child.type == "named_imports":
                for item in child.named_children:
                    if item.type == "import_specifier":
                        add(
                            text(item.child_by_field_name("name")),
                            text(item.child_by_field_name("alias")),
                            has_token(item, "type"),
                        )

    def export_statement(self, node):
        module = self.module(node)
        default = has_token(node, "default")
        type_only = has_token(node, "type")

        def add(name, alias, only_type=False):
            self.exports.append(
                Export(
                    name,
                    alias,
                    module,
                    node.start_point.row + 1,
                    end_line(node),
                    self.parent,
                    type_only or only_type,
                )
            )

        declaration = node.child_by_field_name("declaration")
        value = node.child_by_field_name("value")
        if declaration is not None:
            before = len(self.symbols)
            self.visit(declaration, span=node)
            for symbol in self.symbols[before:]:
                if symbol.parent_name == self.parent:
                    name = (
                        symbol.name.removeprefix(f"{self.parent}.") if self.parent else symbol.name
                    )
                    add(name, "default" if default else name, symbol.kind in {"interface", "type"})
            return
        if default and value is not None:
            name = text(value.child_by_field_name("name"))
            if value.type in FUNCTIONS or value.type == "class":
                self.definition(
                    value, name or "default", "class" if value.type == "class" else "function", node
                )
            elif value.type == "identifier":
                name = text(value)
            else:
                self.visit(value)
            add(name, "default")
            return
        for clause in node.named_children:
            if clause.type == "export_clause":
                for item in clause.named_children:
                    if item.type == "export_specifier":
                        name = text(item.child_by_field_name("name"))
                        add(
                            name,
                            text(item.child_by_field_name("alias")) or name,
                            has_token(item, "type"),
                        )
            elif clause.type == "namespace_export":
                add("*", text(clause.named_children[-1]))
        if has_token(node, "*"):
            add("*", "*")
        if has_token(node, "="):
            self.limitations.add("TypeScript export assignment is searchable text only.")

    def visit(self, node, span=None):
        kind = node.type
        if kind == "import_statement":
            self.import_statement(node)
            return
        if kind == "export_statement":
            self.export_statement(node)
            return
        if kind in DECLARATIONS or kind in FUNCTIONS:
            name = node.child_by_field_name("name")
            if name is None:
                self.limitations.add("Anonymous callbacks and expressions have no symbol identity.")
                return
            self.definition(node, text(name), DECLARATIONS.get(kind, "function"), span)
            return
        if kind in {"method_definition", "method_signature", "abstract_method_signature"}:
            name = node.child_by_field_name("name")
            if name is not None and name.type in NAMES:
                self.definition(node, text(name), "method", span)
            else:
                self.limitations.add("Computed or literal method names are searchable text only.")
            return
        if kind in {
            "variable_declarator",
            "field_definition",
            "public_field_definition",
            "property_signature",
        }:
            name = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name is None or name.type not in NAMES:
                self.limitations.add(
                    "Destructured or computed declarations are searchable text only."
                )
                if value is not None:
                    self.visit(value)
                return
            owner = span if span is not None else node
            if value is not None and value.type in FUNCTIONS | {"class"}:
                symbol_kind = "class" if value.type == "class" else "function"
                if kind != "variable_declarator" and symbol_kind == "function":
                    symbol_kind = "method"
                self.definition(value, text(name), symbol_kind, owner)
            else:
                self.definition(node, text(name), "variable", owner)
                if value is not None:
                    if value.type == "object":
                        self.scope.append(self.symbols[-1])
                        self.visit(value)
                        self.scope.pop()
                    else:
                        self.visit(value)
            return
        if kind == "call_expression":
            callee = text(node.child_by_field_name("function"))
            if callee in {"import", "require"}:
                self.limitations.add(
                    "Dynamic imports and CommonJS require calls are unresolved text."
                )
        if kind == "assignment_expression":
            left = text(node.child_by_field_name("left")) or ""
            if left == "module.exports" or left.startswith(("module.exports.", "exports.")):
                self.limitations.add("CommonJS exports are searchable text only.")
        decorator = None
        first_declaration = True
        for child in node.named_children:
            if child.type == "decorator":
                decorator = decorator if decorator is not None else child
                continue
            # Export and decorator spans belong to declarations, not nested bodies.
            child_span = decorator
            if kind in {"lexical_declaration", "variable_declaration"} and first_declaration:
                child_span = span
                first_declaration = False
            self.visit(child, span=child_span)
            decorator = None


def parse_javascript(content: str, dialect: str) -> ParseResult:
    try:
        tree = Parser(grammar(dialect)).parse(content.encode("utf-8"))
        if tree.root_node.has_error:
            pending = [tree.root_node]
            while pending:
                node = pending.pop()
                if node.is_error or node.is_missing:
                    return ParseResult(
                        "parse_error", error=f"SyntaxError at line {node.start_point.row + 1}"
                    )
                pending.extend(reversed([c for c in node.children if c.has_error or c.is_missing]))
            return ParseResult("parse_error", error="SyntaxError at unknown line")
        syntax = Syntax()
        syntax.visit(tree.root_node)
        return ParseResult(
            "parsed",
            tuple(syntax.symbols),
            tuple(syntax.imports),
            exports=tuple(syntax.exports),
            limitations=tuple(sorted(syntax.limitations)),
        )
    except (ValueError, RecursionError, UnicodeError) as exc:
        return ParseResult("parse_error", error=f"{type(exc).__name__} while parsing source")
