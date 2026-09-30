import asyncio
import copy
import json

import pytest
from test_retrieval_storage import TestEmbeddings, TransactionEngine
from test_snapshot_storage import pytestmark  # noqa: F401

from app.config import PROJECT_ROOT, Settings
from app.evaluation.baseline import evaluate, validate_citations
from app.evaluation.dataset import load_dataset
from app.evaluation.retrieval import prepare_sources
from app.providers.interfaces import Generation


class EvidenceModel:
    async def generate(self, prompt, *, limits):
        scope = json.loads(prompt)
        entry = scope["evidence"][0]
        return Generation(
            model_id="test",
            input_tokens=100,
            output_tokens=20,
            text=json.dumps(
                {
                    "claims": [{"text": "Test claim", "evidence_ids": [entry["id"]]}],
                    "uncertainty": None,
                }
            ),
        )


def test_baseline_roundtrip_validates_real_sources_and_cache(connection):
    dataset = load_dataset(PROJECT_ROOT / "evals/datasets/repository-qa-v1.json")
    dataset = dataset.model_copy(
        update={
            "sources": dataset.sources[:1],
            "cases": tuple(
                case for case in dataset.cases if case.source_id == dataset.sources[0].id
            ),
        }
    )
    engine, settings, provider = (
        TransactionEngine(connection),
        Settings(_env_file=None),
        TestEmbeddings(),
    )
    snapshots = prepare_sources(dataset, engine, settings)
    report = asyncio.run(
        evaluate(dataset, engine, settings, snapshots, model=EvidenceModel(), provider=provider)
    )
    assert report["summary"]["failed"] == 0
    assert report["summary"]["citation_validity"] == 1
    assert not report["summary"]["all_gates_pass"]
    assert report["usage"]["embedding"] > 0
    second = asyncio.run(
        evaluate(dataset, engine, settings, snapshots, model=EvidenceModel(), provider=provider)
    )
    assert second["usage"]["embedding"] == 0
    assert second["indexing"]["tiny-shop"]["reused"]
    trace = copy.deepcopy(next(iter(report["details"].values()))["trace"])
    trace["citations"][0]["source"]["end_line"] = 10000
    with pytest.raises(ValueError):
        validate_citations(engine, snapshots["tiny-shop"], None, trace)


def test_source_mismatch_fails_before_provider_call(connection):
    from sqlalchemy import update

    from app.db.schema import repository_files

    dataset = load_dataset(PROJECT_ROOT / "evals/datasets/repository-qa-v1.json")
    dataset = dataset.model_copy(
        update={"sources": dataset.sources[:1], "cases": dataset.cases[:1]}
    )
    engine, settings = TransactionEngine(connection), Settings(_env_file=None)
    snapshots = prepare_sources(dataset, engine, settings)
    connection.execute(update(repository_files).values(raw_hash="0" * 64))
    provider = TestEmbeddings()
    with pytest.raises(ValueError, match="hash mismatch"):
        asyncio.run(
            evaluate(dataset, engine, settings, snapshots, model=EvidenceModel(), provider=provider)
        )
    assert provider.calls == 0
