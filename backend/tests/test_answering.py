import asyncio
import json
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest

from app.answering.evidence import Draft, EvidenceRegistry, citation
from app.config import Settings
from app.models.contracts import UsageLimits
from app.providers.text import OpenAITextModel


class MemoryTools:
    snapshot_id = uuid4()

    def __init__(self):
        self.content = "def auth():\n    return True\n"
        self.row = {
            "id": uuid4(),
            "snapshot_id": self.snapshot_id,
            "content": self.content,
            "content_hash": sha256(self.content.encode()).hexdigest(),
        }

    def file(self, path):
        return self.row

    def read_file(self, path, start, end):
        return {
            "content": "\n".join(self.content.splitlines()[start - 1 : end]),
            "start_line": start,
            "truncated": False,
        }


def test_evidence_validates_and_quotes_immutable_links():
    registry = EvidenceRegistry(MemoryTools(), uuid4())
    entry = registry.add("auth #1.py", 1, 2)
    draft = Draft(
        claims=[{"text": "Auth returns true", "evidence_ids": [entry["id"]]}], uncertainty=None
    )
    registry.validate(draft)
    link = citation(registry.resolve(entry["id"]), "https://github.com/a/b", "a" * 40)
    assert "/blob/" + "a" * 40 + "/auth%20%231.py#L1-L2" in link["url"]


@pytest.mark.parametrize("mutation", ["run", "snapshot", "file", "content", "bounds", "hash"])
def test_evidence_rejects_tampering(mutation):
    tools = MemoryTools()
    registry = EvidenceRegistry(tools, uuid4())
    entry = registry.add("auth.py", 1, 2)
    evidence, content = registry.entries[entry["id"]]
    if mutation == "run":
        registry.run_id = uuid4()
    elif mutation == "snapshot":
        tools.snapshot_id = uuid4()
    elif mutation == "file":
        tools.row["id"] = uuid4()
    elif mutation == "content":
        tools.row["content"] = tools.content.replace("True", "False")
    elif mutation == "hash":
        tools.row["content_hash"] = "0" * 64
    else:
        evidence = evidence.model_copy(
            update={"source": evidence.source.model_copy(update={"end_line": 100})}
        )
        registry.entries[entry["id"]] = (evidence, content)
    with pytest.raises(ValueError):
        registry.resolve(entry["id"])


def test_unknown_evidence_and_empty_answer_rejected():
    registry = EvidenceRegistry(MemoryTools(), uuid4())
    with pytest.raises(ValueError):
        registry.resolve(str(uuid4()))
    with pytest.raises(ValueError):
        registry.validate(Draft(claims=[], uncertainty=None))
    registry.validate(Draft(claims=[], uncertainty="Insufficient evidence"))


def test_partial_line_reads_are_not_registered():
    tools = MemoryTools()
    tools.read_file = lambda *args: {"truncated": True}
    assert EvidenceRegistry(tools, uuid4()).add("auth.py", 1, 2) is None


def model_with_response(handler):
    return OpenAITextModel(
        Settings(_env_file=None, model_id="test-model", provider_api_key="secret-test-key"),
        transport=httpx.MockTransport(handler),
    )


def test_responses_request_schema_usage_and_no_storage():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["store"] is False
        assert payload["text"]["format"]["strict"] is True
        assert payload["max_output_tokens"] == 2000
        assert "untrusted" in payload["instructions"]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": "test-model",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '{"claims":[],"uncertainty":"unknown"}'}
                        ],
                    }
                ],
                "usage": {"input_tokens": 80, "output_tokens": 20},
            },
        )

    response = asyncio.run(model_with_response(handler).generate("{}", limits=UsageLimits()))
    assert response.input_tokens == 80
    assert response.output_tokens == 20


@pytest.mark.parametrize(
    "body",
    [
        {"status": "incomplete"},
        {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}],
        },
        {"status": "completed", "output": []},
    ],
)
def test_provider_rejects_incomplete_refused_and_empty(body):
    with pytest.raises(ValueError, match="incomplete, refused, or invalid"):
        asyncio.run(
            model_with_response(lambda _: httpx.Response(200, json=body)).generate(
                "{}", limits=UsageLimits()
            )
        )


def test_provider_errors_do_not_expose_response_body():
    with pytest.raises(ValueError) as error:
        asyncio.run(
            model_with_response(lambda _: httpx.Response(401, text="secret-test-key")).generate(
                "{}", limits=UsageLimits()
            )
        )
    assert "secret-test-key" not in str(error.value)


def test_context_limit_prevents_request():
    def handler(_):
        pytest.fail("Request sent over budget")

    with pytest.raises(ValueError, match="context budget"):
        asyncio.run(
            model_with_response(handler).generate(
                "x" * 100, limits=UsageLimits(max_context_tokens=10)
            )
        )


@pytest.mark.parametrize("invalid_attempts", [0, 1, 2])
def test_pipeline_validation_and_repair_without_database(monkeypatch, invalid_attempts):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.answering import service
    from app.providers.interfaces import Generation

    tools = MemoryTools()
    tools.snapshot = {"commit_sha": "a" * 40, "repository_id": uuid4(), "coverage": {}}
    monkeypatch.setattr(service, "RepositoryTools", lambda *args: tools)
    connection = SimpleNamespace(
        execute=lambda _: SimpleNamespace(scalar_one=lambda: "https://github.com/a/b")
    )
    engine = SimpleNamespace(connect=lambda: nullcontext(connection))

    async def retrieve(*args, **kwargs):
        return {
            "parsing_run_id": str(uuid4()),
            "input_tokens": 0,
            "retrieved_files": ["auth.py"],
            "context": [{"path": "auth.py", "start_line": 1, "end_line": 2}],
        }

    monkeypatch.setattr(service, "retrieve", retrieve)
    calls = []

    async def generate(prompt, *, limits):
        calls.append(prompt)
        evidence_id = json.loads(prompt.split("\nPrevious output")[0])["evidence"][0]["id"]
        text = (
            "invalid JSON"
            if len(calls) <= invalid_attempts
            else json.dumps(
                {
                    "claims": [{"text": "Auth returns true.", "evidence_ids": [evidence_id]}],
                    "uncertainty": None,
                }
            )
        )
        return Generation(text=text, model_id="fake", input_tokens=10, output_tokens=5)

    result = asyncio.run(
        service.answer(
            engine,
            tools.snapshot_id,
            "auth",
            Settings(_env_file=None),
            SimpleNamespace(generate=generate),
            mode="lexical",
        )
    )
    assert len(calls) == min(invalid_attempts + 1, 2)
    assert result["usage"]["input_tokens"] == 10 * len(calls)
    if invalid_attempts == 2:
        assert result["status"] == "failed"
        assert result["answer"] is None
        assert result["citations"] == []
    else:
        assert result["status"] == "completed"
        assert result["citations"][0]["source"]["snapshot_id"] == str(tools.snapshot_id)
        assert result["citations"][0]["url"].endswith("/auth.py#L1-L2")
