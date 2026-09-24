import asyncio

import httpx
import pytest

from app.config import Settings
from app.models.contracts import UsageLimits
from app.providers.embeddings import OpenAIEmbeddings
from app.retrieval.context import fuse_rankings
from app.retrieval.embedding_store import profile


class TestTokenizer:
    def encode(self, value, **kwargs):
        return list(value)


def provider(handler):
    return OpenAIEmbeddings(
        Settings(
            _env_file=None,
            embedding_model_id="text-embedding-3-small",
            embedding_dimensions=2,
            provider_api_key="test-secret",
        ),
        transport=httpx.MockTransport(handler),
        tokenizer=TestTokenizer(),
    )


def test_embedding_response_is_reordered_and_validated():
    def handler(request):
        assert request.url == "https://api.openai.com/v1/embeddings"
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-3-small",
                "usage": {"total_tokens": 2},
                "data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}],
            },
        )

    result = asyncio.run(provider(handler).embed(["a", "b"], limits=UsageLimits()))
    assert result.vectors == ((1.0, 0.0), (0.0, 1.0))


def test_auth_errors_do_not_leak_response_body_or_key():
    with pytest.raises(ValueError) as error:
        asyncio.run(
            provider(lambda _: httpx.Response(401, text="test-secret")).embed(
                ["a"], limits=UsageLimits()
            )
        )
    assert "401" in str(error.value) and "test-secret" not in str(error.value)


def test_transient_failure_is_retried(monkeypatch):
    calls = []

    async def no_delay(_):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_delay)

    def handler(_):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-3-small",
                "usage": {"total_tokens": 1},
                "data": [{"index": 0, "embedding": [1, 0]}],
            },
        )

    asyncio.run(provider(handler).embed(["a"], limits=UsageLimits()))
    assert len(calls) == 3


@pytest.mark.parametrize("vector", [[0, 0], [1], [float("inf"), 1]])
def test_bad_vectors_are_rejected(vector):
    import json

    body = json.dumps(
        {
            "model": "text-embedding-3-small",
            "usage": {"total_tokens": 1},
            "data": [{"index": 0, "embedding": vector}],
        }
    )
    with pytest.raises(ValueError, match="invalid response"):
        asyncio.run(
            provider(lambda _: httpx.Response(200, content=body)).embed(["a"], limits=UsageLimits())
        )


def test_token_limit_is_checked_before_network():
    with pytest.raises(ValueError, match="token limits"):
        asyncio.run(
            provider(lambda _: pytest.fail("Network request made")).embed(
                ["x" * 8192], limits=UsageLimits()
            )
        )


def test_cache_profile_changes_with_model_version_or_dimensions():
    original = provider(lambda _: None)
    key = profile(original)["id"]
    original.version = "v2"
    assert profile(original)["id"] != key
    original.version = "v1"
    original.dimensions = 3
    assert profile(original)["id"] != key


def test_rank_fusion_boosts_shared_hits_and_deduplicates_spans():
    a = {"id": "a", "file_id": "f", "path": "a.py", "start_char": 0, "end_char": 10}
    b = {"id": "b", "file_id": "g", "path": "b.py", "start_char": 0, "end_char": 10}
    overlapping = {**a, "id": "c", "start_char": 5}
    result = fuse_rankings([a, b], [b, overlapping])
    assert result[0]["id"] == "b"
    assert len(result) == 2


def test_expanded_context_merges_overlapping_source_ranges():
    from app.retrieval.context import assemble_context

    class Tools:
        run = {"files": [{"path": "x.py", "symbols": [], "imports": []}]}

        def file(self, _):
            return {"content": "a\nb\nc\nd\ne\nf\n", "language": "python"}

    hits = [
        {
            "id": str(i),
            "path": "x.py",
            "start_line": i,
            "end_line": i,
            "parent_name": None,
            "symbol_name": None,
            "score": 1.0,
            "content": "x",
            "start_char": 0,
            "end_char": 1,
        }
        for i in (2, 4)
    ]
    context = assemble_context(Tools(), hits)
    assert len(context) == 1
    assert context[0]["content"] == "a\nb\nc\nd\ne\nf"


def test_tool_output_budget_rejects_oversized_metadata():
    from app.tools.repository import bounded_result

    @bounded_result
    def large():
        return {"symbols": ["x" * 40000]}

    with pytest.raises(ValueError, match="32 KiB"):
        large()


def test_file_recall_counts_distinct_expected_files():
    from app.evaluation.retrieval import file_recall

    assert file_recall(["a", "b"], ["a", "a", "other"]) == 0.5
