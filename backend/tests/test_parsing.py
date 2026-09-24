import hashlib

import pytest

from app.ingestion.chunker import chunks_for_source, pipeline_version
from app.ingestion.parser import parse_source

SOURCE = '''from .models import User as Person
import os.path as paths

@decorate
class Service:
    """Class context."""
    default = "en"

    @staticmethod
    async def create(user):
        def validate():
            return True
        return validate()

    def update(self):
        return None
'''


def test_extracts_decorated_nested_and_async_definitions():
    result = parse_source(SOURCE, "python")
    symbols = {symbol.name: symbol for symbol in result.symbols}
    assert result.status == "parsed"
    assert (symbols["Service"].start_line, symbols["Service"].end_line) == (4, 16)
    method = symbols["Service.create"]
    assert (method.start_line, method.end_line, method.kind, method.parent_name) == (
        9,
        13,
        "method",
        "Service",
    )
    nested = symbols["Service.create.validate"]
    assert nested.kind == "function"
    assert nested.parent_name == "Service.create"
    assert symbols["Service.default"].kind == "variable"


def test_imports_are_syntactic_and_preserve_alias_and_relative_level():
    result = parse_source(SOURCE, "python")
    first, second = result.imports
    assert (first.module, first.name, first.alias, first.level) == ("models", "User", "Person", 1)
    assert (second.module, second.name, second.alias) == ("os.path", None, "paths")
    nested = parse_source("def f():\n    from . import thing\n", "python").imports[0]
    assert nested.scope == "f"
    assert nested.module == ""


def test_parent_context_does_not_duplicate_child_definitions():
    result = parse_source(SOURCE, "python")
    chunks = chunks_for_source(SOURCE, result, 1024)
    context = "".join(chunk.content for chunk in chunks if chunk.symbol_name == "Service")
    assert "@decorate" in context and "default" in context
    assert "async def create" not in context
    assert "def update" not in context
    assert "".join(chunk.content for chunk in chunks) == SOURCE
    assert any(chunk.symbol_name == "Service.create.validate" for chunk in chunks)


@pytest.mark.parametrize(
    "source,language,status",
    [
        ("def broken(:\n", "python", "parse_error"),
        ("# Readme\n\nSome documentation.\n", "markdown", "text_fallback"),
        ("export const value = 1;\n", "typescript", "text_fallback"),
        ("", "python", "parsed"),
        ("\ufeffdef f():\n    pass\n", "python", "parsed"),
    ],
)
def test_fallback_and_empty_files_preserve_source(source, language, status):
    parsed = parse_source(source, language)
    chunks = chunks_for_source(source, parsed, 32)
    assert parsed.status == status
    assert "".join(chunk.content for chunk in chunks) == source
    if status == "parse_error":
        assert parsed.error == "SyntaxError at line 1"
        assert not parsed.symbols


@pytest.mark.parametrize(
    "source",
    [
        "def big():\n" + "    value = 'hello'\n" * 100,
        "value = '" + "🌍é" * 100 + "'\n",
        "x\u2028y\n\nlast",
        "\n\n",
    ],
)
@pytest.mark.parametrize("budget", [4, 32, 128])
def test_oversized_source_has_bounded_exact_citable_slices(source, budget):
    chunks = chunks_for_source(source, parse_source(source, "python"), budget)
    assert "".join(chunk.content for chunk in chunks) == source
    for chunk in chunks:
        assert chunk.content == source[chunk.start_char : chunk.end_char]
        assert chunk.start_line == source[: chunk.start_char].count("\n") + 1
        assert chunk.end_line == source[: chunk.end_char - 1].count("\n") + 1
        assert chunk.content_hash == hashlib.sha256(chunk.content.encode()).hexdigest()
        assert chunk.token_upper_bound == len(chunk.content.encode()) <= budget


def test_oversized_function_segments_keep_symbol_identity():
    source = "def big():\n" + "    pass\n" * 20
    chunks = chunks_for_source(source, parse_source(source, "python"), 32)
    assert len(chunks) > 1
    assert {chunk.symbol_name for chunk in chunks} == {"big"}


def test_parsing_never_imports_or_executes_source(tmp_path):
    marker = tmp_path / "should-not-exist"
    source = f"open({str(marker)!r}, 'w').write('executed')\n"
    assert parse_source(source, "python").status == "parsed"
    assert not marker.exists()


def test_different_budgets_have_distinct_versions():
    assert pipeline_version(32) != pipeline_version(64)


def test_chunk_count_limit_aborts_during_splitting():
    source = "x" * 1000
    with pytest.raises(ValueError, match="MAX_SNAPSHOT_CHUNKS"):
        chunks_for_source(source, parse_source(source, "text"), 4, max_chunks=2)


def test_recursion_failure_falls_back(monkeypatch):
    import ast

    def fail(*args, **kwargs):
        raise RecursionError

    monkeypatch.setattr(ast, "parse", fail)
    assert parse_source("value = 1", "python").status == "parse_error"
