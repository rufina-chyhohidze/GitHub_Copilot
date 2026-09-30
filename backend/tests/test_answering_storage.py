import asyncio
import json

import pytest
from test_retrieval_storage import TestEmbeddings, TransactionEngine, snapshot
from test_snapshot_storage import pytestmark  # noqa: F401

from app.answering.evidence import render
from app.answering.service import answer
from app.config import Settings
from app.providers.interfaces import Generation
from app.retrieval.embedding_store import build_index


class Model:
    def __init__(self, failures=0):
        self.calls = 0
        self.failures = failures

    async def generate(self, prompt, *, limits):
        self.calls += 1
        scope = json.loads(prompt.split("\nPrevious output")[0])
        eid = scope["evidence"][0]["id"]
        return Generation(
            model_id="test",
            input_tokens=100,
            output_tokens=20,
            text=json.dumps(
                {
                    "claims": [
                        {
                            "text": "Authentication returns the token.",
                            "evidence_ids": [eid if self.calls > self.failures else "unknown"],
                        }
                    ],
                    "uncertainty": None,
                }
            ),
        )


@pytest.mark.parametrize("failures", [0, 1, 2])
def test_hybrid_answer_repair_and_controlled_failure(connection, failures):
    snapshot_id, run_id = snapshot(connection)
    engine = TransactionEngine(connection)
    settings = Settings(_env_file=None)
    provider = TestEmbeddings()
    asyncio.run(build_index(engine, snapshot_id, settings, provider, run_id))
    model = Model(failures)
    result = asyncio.run(
        answer(
            engine, snapshot_id, "authenticate", settings, model, provider=provider, run_id=run_id
        )
    )
    assert model.calls == min(failures + 1, 2)
    assert result["usage"]["input_tokens"] == 100 * model.calls
    assert result["status"] == ("failed" if failures == 2 else "completed")
    if failures == 2:
        assert result["answer"] is None
        assert result["citations"] == []
    else:
        assert result["citations"][0]["source"]["snapshot_id"] == str(snapshot_id)
        assert "/blob/" + "a" * 40 in render(result)
        assert result["evidence"]


def test_no_evidence_skips_model(connection):
    snapshot_id, run_id = snapshot(connection)
    model = Model()
    result = asyncio.run(
        answer(
            TransactionEngine(connection),
            snapshot_id,
            "zzzzmissing",
            Settings(_env_file=None),
            model,
            mode="lexical",
            run_id=run_id,
        )
    )
    assert model.calls == 0
    assert result["status"] == "completed"
    assert result["answer"]["uncertainty"]
    assert not result["citations"]
