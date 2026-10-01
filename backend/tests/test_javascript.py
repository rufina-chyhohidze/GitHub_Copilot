from importlib.metadata import version
from pathlib import Path

import pytest

from app.evaluation.dataset import load_dataset, verify_source
from app.ingestion.chunker import chunks_for_source
from app.ingestion.parser import PARSER_VERSION, parse_source
from app.ingestion.scanner import LANGUAGES

FIXTURE = Path(__file__).parent / "fixtures/web_shop"


def parsed_file(name):
    content = (FIXTURE / name).read_text()
    return content, parse_source(content, LANGUAGES[Path(name).suffix], path=name)


def test_typescript_symbols_members_and_export_spans():
    _, parsed = parsed_file("store.ts")
    symbols = {s.name: s for s in parsed.symbols}
    assert parsed.status == "parsed"
    assert symbols["Order"].kind == "interface"
    assert symbols["Order.id"].parent_name == "Order"
    assert symbols["OrderId"].kind == "type"
    assert symbols["State"].kind == "enum"
    assert (symbols["Store"].start_line, symbols["Store"].end_line) == (9, 19)
    assert (symbols["Store.save"].start_line, symbols["Store.save"].end_line) == (12, 16)
    assert symbols["Store.find"].kind == "method"
    assert [(e.name, e.alias, e.type_only) for e in parsed.exports] == [
        ("Order", "Order", True),
        ("OrderId", "OrderId", True),
        ("State", "State", False),
        ("Store", "default", False),
    ]


def test_js_arrow_assignment_and_syntactic_import_export_aliases():
    _, parsed = parsed_file("routes.js")
    symbols = {s.name: s for s in parsed.symbols}
    assert (symbols["create"].start_line, symbols["create"].end_line) == (6, 10)
    assert symbols["create.saved"].parent_name == "create"
    assert [(i.module, i.name, i.alias) for i in parsed.imports] == [
        ("./store", "default", "Store"),
        ("./events", "publish", "notify"),
    ]
    assert [(e.name, e.alias, e.module) for e in parsed.exports] == [
        ("create", "create", None),
        ("create", "submit", None),
        ("*", "events", "./events"),
    ]
    assert all(i.resolution == "unresolved" for i in (*parsed.imports, *parsed.exports))


def test_jsx_tsx_and_default_exports():
    for path, name in [("Badge.jsx", "Badge"), ("OrderCard.tsx", "OrderCard")]:
        _, parsed = parsed_file(path)
        assert parsed.status == "parsed"
        assert parsed.symbols[0].name == name
        assert parsed.exports[-1].alias == "default"
    _, card = parsed_file("OrderCard.tsx")
    assert card.imports[0].type_only
    _, events = parsed_file("events.js")
    assert events.symbols[-1].name == "default"
    assert events.exports[-1].name is None
    assert (events.symbols[-1].start_line, events.symbols[-1].end_line) == (6, 8)


def test_ts_and_tsx_use_distinct_grammars():
    source = "const value = <number>input;"
    assert parse_source(source, "typescript", path="value.ts").status == "parsed"
    assert parse_source(source, "typescript", path="value.tsx").status == "parse_error"
    assert parse_source("const Card = () => <div/>;", "tsx").status == "parsed"


def test_type_only_imports_namespace_side_effects_and_reexports():
    parsed = parse_source(
        """import Main, {type Item as Renamed, save} from "lib";
import type * as Types from './types';
import './setup';
export type { Item as PublicItem } from 'lib';
export * from './api';
""",
        "typescript",
    )
    assert parsed.status == "parsed"
    assert [(i.name, i.alias, i.type_only) for i in parsed.imports] == [
        ("default", "Main", False),
        ("Item", "Renamed", True),
        ("save", None, False),
        ("*", "Types", True),
        (None, None, False),
    ]
    assert parsed.exports[0].type_only
    assert parsed.exports[1].name == "*"


def test_nested_scopes_repeated_names_and_decorators():
    source = """@sealed
export class First {
  @track
  save() {
    const inner = () => 1;
    return inner();
  }
}
class Second { save() {} }
namespace API { export function save() {} }
"""
    parsed = parse_source(source, "typescript")
    symbols = {s.name: s for s in parsed.symbols}
    assert (symbols["First"].start_line, symbols["First.save"].start_line) == (1, 3)
    assert symbols["First.save.inner"].parent_name == "First.save"
    assert symbols["Second.save"].parent_name == "Second"
    assert symbols["API.save"].parent_name == "API"
    assert parsed.exports[-1].scope == "API"


def test_multiple_exported_functions_do_not_share_multiline_spans():
    parsed = parse_source(
        """export const first = () => {
  return 1;
}, second = () => {
  return 2;
};
""",
        "javascript",
    )
    first, second = parsed.symbols
    assert (first.start_line, first.end_line) == (1, 3)
    assert (second.start_line, second.end_line) == (3, 5)


def test_object_methods_keep_their_owner():
    parsed = parse_source("const service = { save() { const value = 1; } };", "javascript")
    assert [s.name for s in parsed.symbols] == ["service", "service.save", "service.save.value"]


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURE.iterdir() if p.suffix != ".md"))
@pytest.mark.parametrize("budget", [4, 64, 1024])
def test_all_fixture_chunks_are_exact_bounded_citable_slices(name, budget):
    content, parsed = parsed_file(name)
    chunks = chunks_for_source(content, parsed, budget)
    assert "".join(c.content for c in chunks) == content
    for chunk in chunks:
        assert chunk.content == content[chunk.start_char : chunk.end_char]
        assert chunk.start_line == content[: chunk.start_char].count("\n") + 1
        assert chunk.end_line == content[: chunk.end_char - 1].count("\n") + 1
        assert chunk.token_upper_bound <= budget


def test_parent_context_excludes_method_bodies():
    content, parsed = parsed_file("store.ts")
    chunks = chunks_for_source(content, parsed, 1024)
    context = "".join(c.content for c in chunks if c.symbol_name == "Store")
    assert "new Map" in context
    assert "positive total required" not in context
    assert any(c.symbol_name == "Store.save" for c in chunks)


def test_invalid_syntax_and_unsupported_forms_preserve_text_and_report_limits():
    _, broken = parsed_file("broken.ts")
    assert broken.status == "parse_error"
    assert broken.error.startswith("SyntaxError at line ")
    assert not broken.symbols and not broken.imports and not broken.exports
    _, legacy = parsed_file("legacy.cjs")
    assert legacy.status == "parsed"
    assert not legacy.imports and not legacy.exports
    assert any("CommonJS" in note for note in legacy.limitations)
    other = parse_source("const { a } = value; import old = require('old');", "typescript")
    assert any("Destructured" in note for note in other.limitations)
    assert any("import-equals" in note for note in other.limitations)


def test_unicode_and_bom_locations_are_source_lines():
    content = '\ufeff// 🌍\nexport const café = () => "é";\n'
    parsed = parse_source(content, "javascript")
    assert parsed.status == "parsed"
    assert (parsed.symbols[0].name, parsed.symbols[0].start_line) == ("café", 2)
    assert "".join(c.content for c in chunks_for_source(content, parsed, 8)) == content


def test_parser_never_executes_javascript(tmp_path):
    marker = tmp_path / "executed"
    source = f"require('fs').writeFileSync('{marker}', 'unsafe');"
    assert parse_source(source, "javascript").status == "parsed"
    assert not marker.exists()
    assert f"-js{version('tree-sitter-javascript')}" in PARSER_VERSION
    assert f"-tsx{version('tree-sitter-typescript')}" in PARSER_VERSION
    assert len(PARSER_VERSION) <= 64


def test_web_dataset_matches_fixture_and_declared_evidence():
    dataset = load_dataset(FIXTURE.parents[3] / "evals/datasets/web-qa-v1.json")
    assert verify_source(dataset, dataset.sources[0], FIXTURE) == len(dataset.cases)
