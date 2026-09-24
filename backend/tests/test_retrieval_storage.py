"""Real pgvector tests with a deterministic test provider, never a paid API."""

import asyncio
import hashlib
from contextlib import contextmanager
from uuid import UUID

import pytest
from sqlalchemy import func, select
from test_snapshot_storage import pytestmark, save  # noqa: F401

from app.config import Settings
from app.db.schema import embedding_indexes, embeddings
from app.ingestion.parsing import parse_and_store
from app.ingestion.scanner import ManifestEntry, ScanResult, StoredFile
from app.providers.interfaces import EmbeddingBatch
from app.retrieval.embedding_store import build_index, cached_vectors, profile
from app.retrieval.lexical import lexical_search
from app.retrieval.semantic import semantic_search
from app.retrieval.service import retrieve
from app.tools.repository import RepositoryTools


class TestEmbeddings:
    __test__ = False
    provider = "test-only"
    model_id = "deterministic"
    dimensions = 2
    version = "v1"

    def __init__(self):
        self.calls = 0
        self.fail = False

    def count_tokens(self, value):
        return len(value)

    async def embed(self, texts, *, limits):
        self.calls += 1
        if self.fail:
            raise ValueError("Simulated failure")
        return EmbeddingBatch(
            model_id=self.model_id,
            dimensions=2,
            vectors=tuple((1.0, 0.1) if "auth" in text.lower() else (0.1, 1.0) for text in texts),
            input_tokens=sum(map(len, texts)),
        )


class TransactionEngine:
    """Keep test commits inside the fixture's rollback-only outer transaction."""

    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def begin(self):
        with self.connection.begin_nested():
            yield self.connection

    @contextmanager
    def connect(self):
        yield self.connection


def snapshot(connection, sha="a" * 40):
    sources = {
        "auth.py": "def authenticate(token):\n    return token\n",
        "orders.py": "def save_order(order):\n    return order\n",
    }
    files = []
    manifest = []
    for path, content in sources.items():
        digest = hashlib.sha256(content.encode()).hexdigest()
        files.append(StoredFile(path, "python", content, digest, digest, len(content), 2))
        manifest.append(ManifestEntry(path, "a" * 40, len(content), "stored"))
    response = save(connection, sha=sha, scan_result=ScanResult(tuple(files), tuple(manifest)))
    snapshot_id = UUID(response["snapshot_id"])
    parsed = parse_and_store(connection, snapshot_id, Settings(_env_file=None))
    return snapshot_id, UUID(parsed["parsing_run_id"])


def test_tools_enforce_snapshot_and_literal_search(connection):
    snapshot_id, run_id = snapshot(connection)
    other, other_run = snapshot(connection, "b" * 40)
    tools = RepositoryTools(connection, snapshot_id, run_id)
    assert tools.search_code("authenticate")["hits"][0]["path"] == "auth.py"
    assert tools.search_code(".*")["hits"] == []
    assert tools.search_code("AUTHENTICATE")["hits"] == []
    assert tools.search_code("AUTHENTICATE", case_sensitive=False)["hits"]
    assert tools.search_code("return token\n")["hits"][0]["end_line"] == 2
    assert tools.find_symbol("save_order")["symbols"][0]["path"] == "orders.py"
    assert tools.read_file("auth.py", 1, 1)["content"] == "def authenticate(token):"
    assert len(tools.get_repository_tree()["entries"]) == 2
    assert tools.get_file_symbols("auth.py")["symbols"][0]["name"] == "authenticate"
    with pytest.raises(ValueError):
        tools.read_file("../secret")
    with pytest.raises(ValueError):
        _ = RepositoryTools(connection, snapshot_id, other_run).run
    assert all(hit["parsing_run_id"] == run_id for hit in lexical_search(tools, "authenticate"))
    assert other != snapshot_id


def test_pgvector_hybrid_search_and_cache_reuse(connection):
    snapshot_id, run_id = snapshot(connection)
    engine = TransactionEngine(connection)
    provider = TestEmbeddings()
    settings = Settings(_env_file=None)
    first = asyncio.run(build_index(engine, snapshot_id, settings, provider, run_id))
    second = asyncio.run(build_index(engine, snapshot_id, settings, provider, run_id))
    assert first["chunks"] == 2 and second["reused"] is True
    assert provider.calls == 1
    response = asyncio.run(
        retrieve(engine, snapshot_id, "authentication", settings, provider=provider)
    )
    assert response["context"][0]["path"] == "auth.py"
    assert response["snapshot_id"] == str(snapshot_id)
    assert response["answer_generated"] is False
    calls = provider.calls
    asyncio.run(retrieve(engine, snapshot_id, "authentication", settings, provider=provider))
    assert provider.calls == calls


def test_embeddings_from_other_snapshot_do_not_leak(connection):
    first, first_run = snapshot(connection)
    second, second_run = snapshot(connection, "b" * 40)
    engine, provider = TransactionEngine(connection), TestEmbeddings()
    settings = Settings(_env_file=None)
    asyncio.run(build_index(engine, first, settings, provider))
    asyncio.run(build_index(engine, second, settings, provider))
    hits = semantic_search(RepositoryTools(connection, first), [1.0, 0.1], profile(provider)["id"])
    assert all(hit["parsing_run_id"] == first_run for hit in hits)
    assert all(hit["parsing_run_id"] != second_run for hit in hits)
    assert provider.calls == 1  # Identical input can reuse cache; chunk membership stays scoped.


def test_partial_index_is_not_searchable_and_can_resume(connection):
    snapshot_id, _ = snapshot(connection)
    engine, provider = TransactionEngine(connection), TestEmbeddings()
    settings = Settings(_env_file=None, embedding_batch_size=1)
    original = provider.embed

    async def fail_second(texts, *, limits):
        if provider.calls == 1:
            raise ValueError("Simulated interruption")
        return await original(texts, limits=limits)

    provider.embed = fail_second
    with pytest.raises(ValueError):
        asyncio.run(build_index(engine, snapshot_id, settings, provider))
    assert connection.execute(select(func.count()).select_from(embeddings)).scalar_one() == 1
    assert connection.execute(select(func.count()).select_from(embedding_indexes)).scalar_one() == 0
    with pytest.raises(ValueError, match="complete"):
        semantic_search(
            RepositoryTools(connection, snapshot_id), [1.0, 0.1], profile(provider)["id"]
        )
    provider.embed = original
    asyncio.run(build_index(engine, snapshot_id, settings, provider))
    assert provider.calls == 2


def test_token_budget_fails_before_api_call(connection):
    snapshot_id, _ = snapshot(connection)
    provider = TestEmbeddings()
    with pytest.raises(ValueError, match="TOKEN_BUDGET"):
        asyncio.run(
            build_index(
                TransactionEngine(connection),
                snapshot_id,
                Settings(_env_file=None, embedding_token_budget=1),
                provider,
            )
        )
    assert provider.calls == 0


def test_duplicate_inputs_embedded_once(connection):
    provider = TestEmbeddings()
    vectors, _, new = asyncio.run(
        cached_vectors(
            TransactionEngine(connection), provider, ["same", "same"], Settings(_env_file=None)
        )
    )
    assert vectors[0] == vectors[1] and new == 1
