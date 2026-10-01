import asyncio
import copy
import json
import sys
from uuid import uuid4

import pytest

from app.config import PROJECT_ROOT, Settings
from app.evaluation import baseline
from app.evaluation.dataset import dataset_fingerprint, load_dataset
from app.evaluation.results import CaseResult, EvaluationRun


@pytest.fixture
def dataset():
    return load_dataset(PROJECT_ROOT / "evals/datasets/repository-qa-v1.json")


def synthetic_report(dataset):
    cases = [case for case in dataset.cases if case.split == "development"]
    run = EvaluationRun(
        dataset_id=dataset.id,
        dataset_version=dataset.version,
        dataset_sha256=dataset_fingerprint(dataset),
        pipeline_version="test-only",
        model_id="fake",
        split="development",
        results=tuple(
            CaseResult(
                case_id=case.id,
                status="answered",
                answer="Test claim",
                latency_ms=1,
                retrieved_files=case.expected_files,
            )
            for case in cases
        ),
    )
    return {
        "run": run.model_dump(mode="json"),
        "details": {
            case.id: {
                "citation_count": 1,
                "valid_citation_count": 1,
                "trace": {"answer": {"claims": [{"text": "Test claim"}]}},
            }
            for case in cases
        },
    }


def reviewed(report, dataset):
    review = baseline.review_template(report)
    review["reviewer"] = "test reviewer"
    review["kind"] = "human"
    for case in dataset.cases:
        if case.split == "development":
            review["cases"][case.id] = {
                "supported_claims": [True],
                "required_facts": [True] * len(case.required_facts),
                "uncertainty_correct": True,
                "forbidden_claims_absent": True,
                "failure_stage": "none",
                "notes": "Synthetic test assessment",
            }
    return review


def test_unreviewed_answers_never_pass(dataset):
    report = synthetic_report(dataset)
    summary = baseline.summarize(report, dataset)
    assert summary["macro_file_recall_at_10_chunks"] == 1
    assert summary["citation_validity"] == 1
    assert summary["reviewed_claim_support"] is None
    assert summary["confirmed_case_pass_rate"] == 0
    assert not summary["all_gates_pass"]
    assert baseline.summarize(report, dataset, reviewed(report, dataset))["all_gates_pass"]


@pytest.mark.parametrize("kind", ["ai", "unspecified"])
def test_ai_or_unspecified_review_does_not_count_as_human_signoff(dataset, kind):
    report = synthetic_report(dataset)
    review = reviewed(report, dataset)
    review["kind"] = kind
    summary = baseline.summarize(report, dataset, review)
    assert summary["quality_thresholds_met"]
    assert not summary["human_review_complete"]
    assert not summary["all_gates_pass"]


def test_reviews_measure_support_facts_and_absence_separately(dataset):
    report = synthetic_report(dataset)
    review = reviewed(report, dataset)
    absence = next(
        case
        for case in dataset.cases
        if case.split == "development" and case.category == "insufficient_evidence"
    )
    review["cases"][absence.id]["uncertainty_correct"] = False
    first = next(iter(review["cases"].values()))
    first["supported_claims"] = [False]
    first["required_facts"][0] = False
    summary = baseline.summarize(report, dataset, review)
    assert summary["gates"]["insufficient_evidence"] is False
    assert 0 < summary["reviewed_claim_support"] < 1
    assert 0 < summary["confirmed_required_fact_coverage"] < 1
    assert not summary["all_gates_pass"]


@pytest.mark.parametrize("mutation", ["fingerprint", "missing", "claims", "facts", "failed"])
def test_rejects_incomplete_or_misattributed_reviews(dataset, mutation):
    report = synthetic_report(dataset)
    review = reviewed(report, dataset)
    key = next(iter(review["cases"]))
    if mutation == "fingerprint":
        review["report_sha256"] = "0" * 64
    elif mutation == "missing":
        del review["cases"][key]
    elif mutation == "claims":
        review["cases"][key]["supported_claims"] = []
    elif mutation == "facts":
        review["cases"][key]["required_facts"] = []
    else:
        report["run"]["results"][0].update(status="failed", answer=None, error="test")
        review["report_sha256"] = baseline.fingerprint(report)
    with pytest.raises(ValueError):
        baseline.summarize(report, dataset, review)


def test_failures_and_skips_stay_in_denominator(dataset):
    report = synthetic_report(dataset)
    first = report["run"]["results"][0]
    first.update(status="failed", answer=None, error="test", retrieved_files=[])
    report["run"]["results"][1].update(status="skipped", answer=None)
    summary = baseline.summarize(report, dataset)
    assert summary["cases"] == 14
    assert summary["failed"] == summary["skipped"] == 1
    assert summary["macro_file_recall_at_10_chunks"] == pytest.approx(13 / 14)


def test_pricing_missing_rates_are_unknown_not_free():
    usage = {"input": 1000, "output": 500, "embedding": 2000}
    assert baseline.Pricing().estimate(usage) is None
    assert baseline.Pricing(input=2, output=4, embedding=0.1).estimate(usage) == pytest.approx(
        0.0042
    )
    assert baseline.Pricing().estimate(dict.fromkeys(usage, 0)) == 0
    for value in [-1, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            baseline.Pricing(input=value)


def test_runner_keeps_failed_cases_and_never_sends_labels(dataset, monkeypatch):
    snapshots = {source.id: uuid4() for source in dataset.sources}
    runs = {key: uuid4() for key in snapshots}
    monkeypatch.setattr(baseline, "validate_snapshots", lambda *args: runs)
    questions = []

    async def fake_answer(engine, snapshot, question, settings, model, **kwargs):
        questions.append(question)
        if len(questions) == 1:
            raise ValueError("Sensitive provider failure")
        return {
            "status": "failed",
            "answer": None,
            "citations": [],
            "usage": {"input_tokens": 10, "output_tokens": 5, "embedding_input_tokens": 0},
        }

    monkeypatch.setattr(baseline, "answer", fake_answer)
    report = asyncio.run(
        baseline.evaluate(
            dataset, None, Settings(_env_file=None), snapshots, model=object(), mode="lexical"
        )
    )
    assert questions == [case.question for case in dataset.cases if case.split == "development"]
    assert report["summary"]["failed"] == 14
    assert report["usage"]["input"] == 130
    assert report["estimated_cost_usd"] is None
    assert "Sensitive provider failure" not in str(report)


def test_retrieval_only_records_explicit_skips(dataset, monkeypatch):
    snapshots = {source.id: uuid4() for source in dataset.sources}
    monkeypatch.setattr(
        baseline, "validate_snapshots", lambda *args: {key: uuid4() for key in snapshots}
    )

    async def fake_retrieve(*args, **kwargs):
        return {"retrieved_files": [], "input_tokens": 0}

    monkeypatch.setattr(baseline, "retrieve", fake_retrieve)
    report = asyncio.run(
        baseline.evaluate(dataset, None, Settings(_env_file=None), snapshots, mode="lexical")
    )
    assert report["summary"]["skipped"] == 14
    assert report["summary"]["citation_validity"] is None
    assert report["run"]["model_id"] == "generation-disabled"
    assert not report["summary"]["answer_quality_evaluated"]
    altered = copy.deepcopy(report)
    altered["run"]["results"].pop()
    with pytest.raises(ValueError, match="exactly once"):
        baseline.summarize(altered, dataset)


def test_offline_cli_preserves_report_and_enforces_gates(dataset, tmp_path, monkeypatch):
    report = synthetic_report(dataset)
    source = tmp_path / "report.json"
    source.write_text(json.dumps(report))
    output = tmp_path / "summary.json"
    template = tmp_path / "review.json"
    args = [
        "eval",
        "--report",
        str(source),
        "--output",
        str(output),
        "--review-template",
        str(template),
        "--require-gates",
    ]
    monkeypatch.setattr(sys, "argv", args)
    assert baseline.main() == 1
    assert json.loads(template.read_text())["report_sha256"] == baseline.fingerprint(report)
    assert json.loads(source.read_text()) == report
    monkeypatch.setattr(sys, "argv", ["eval", "--report", str(source), "--output", str(source)])
    with pytest.raises(SystemExit) as exc:
        baseline.main()
    assert exc.value.code == 2
    assert json.loads(source.read_text()) == report


def test_failed_case_counts_against_pass_rate_without_erasing_reviews(dataset):
    report = synthetic_report(dataset)
    review = reviewed(report, dataset)
    first = report["run"]["results"][0]
    first.update(status="failed", answer=None, error="test")
    review["cases"][first["case_id"]] = None
    review["report_sha256"] = baseline.fingerprint(report)
    summary = baseline.summarize(report, dataset, review)
    assert summary["confirmed_case_pass_rate"] == pytest.approx(13 / 14)
    assert summary["gates"]["case_pass_rate"] is True


def test_index_failure_retains_every_case(dataset, monkeypatch):
    from test_retrieval_storage import TestEmbeddings

    snapshots = {source.id: uuid4() for source in dataset.sources}
    monkeypatch.setattr(
        baseline, "validate_snapshots", lambda *args: {key: uuid4() for key in snapshots}
    )

    async def failed_index(*args):
        raise ValueError("secret provider data")

    monkeypatch.setattr(baseline, "build_index", failed_index)
    report = asyncio.run(
        baseline.evaluate(
            dataset,
            None,
            Settings(_env_file=None),
            snapshots,
            provider=TestEmbeddings(),
            model=object(),
        )
    )
    assert report["summary"]["failed"] == 14
    assert report["estimated_cost_usd"] is None
    assert all(row["input_tokens"] is None for row in report["indexing"].values())
    assert "secret provider data" not in str(report)
