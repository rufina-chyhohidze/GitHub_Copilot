import json
import shutil
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.config import PROJECT_ROOT
from app.evaluation import cli
from app.evaluation.dataset import Dataset, dataset_fingerprint, load_dataset, verify_source
from app.evaluation.results import CaseResult, EvaluationRun

DATASET_PATH = PROJECT_ROOT / "evals/datasets/repository-qa-v1.json"


@pytest.fixture
def dataset():
    return load_dataset(DATASET_PATH)


def test_committed_fixture_and_all_its_evidence_are_valid(dataset):
    source = dataset.sources[0]
    assert verify_source(dataset, source, PROJECT_ROOT / source.local_path) == 14
    assert {case.split for case in dataset.cases} == {"development", "held_out"}
    assert len(dataset.cases) == 20


@pytest.mark.parametrize("mutation", ["unknown_source", "duplicate_id", "unsafe_path"])
def test_rejects_broken_dataset_links(dataset, mutation):
    data = dataset.model_dump(mode="json")
    if mutation == "unknown_source":
        data["cases"][0]["source_id"] = "missing"
    elif mutation == "duplicate_id":
        data["cases"][1]["id"] = data["cases"][0]["id"]
    else:
        data["cases"][0]["supporting_spans"][0]["path"] = "../secret"
    with pytest.raises(ValidationError):
        Dataset.model_validate(data)


@pytest.mark.parametrize("mutation", ["modified", "extra", "symlink"])
def test_fixture_drift_is_detected(dataset, tmp_path, mutation):
    source = dataset.sources[0]
    root = tmp_path / "fixture"
    shutil.copytree(PROJECT_ROOT / source.local_path, root)
    if mutation == "modified":
        (root / "shop/auth.py").write_text("# changed\n")
    elif mutation == "extra":
        (root / "oauth.py").write_text("# new feature\n")
    else:
        (root / "shop/auth.py").unlink()
        (root / "shop/auth.py").symlink_to(PROJECT_ROOT / source.local_path / "shop/auth.py")
    with pytest.raises(ValueError):
        verify_source(dataset, source, root)


@pytest.mark.parametrize("change", [{"end_line": 10000}, {"anchor": "nonexistent marker"}])
def test_incorrect_evidence_is_detected(dataset, change):
    data = dataset.model_dump(mode="json")
    data["cases"][0]["supporting_spans"][0].update(change)
    altered = Dataset.model_validate(data)
    source = altered.sources[0]
    with pytest.raises(ValueError):
        verify_source(altered, source, PROJECT_ROOT / source.local_path)


def test_public_checkout_requires_exact_commit(dataset, monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="wrong\n"),
    )
    with pytest.raises(ValueError, match="clean checkout"):
        verify_source(dataset, dataset.sources[1], tmp_path)


def test_validation_does_not_import_or_execute_fixture(dataset):
    source = dataset.sources[0]
    before = set(sys.modules)
    verify_source(dataset, source, PROJECT_ROOT / source.local_path)
    assert not any(name.startswith("shop") for name in set(sys.modules) - before)


def test_offline_cli_reports_unverified_public_sources(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["repo-copilot-eval"])
    assert cli.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert set(output["unverified_sources"]) == {"sampleproject", "itsdangerous"}
    assert output["answer_quality_evaluated"] is False


def test_require_all_fails_without_public_checkouts(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["repo-copilot-eval", "--require-all"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "Missing public checkouts" in capsys.readouterr().err


def test_results_roundtrip_and_require_full_split(dataset):
    results = tuple(
        CaseResult(case_id=case.id, status="skipped", latency_ms=0)
        for case in dataset.cases
        if case.split == "development"
    )
    run = EvaluationRun(
        dataset_id=dataset.id,
        dataset_version=dataset.version,
        dataset_sha256=dataset_fingerprint(dataset),
        pipeline_version="not-run",
        model_id="none",
        split="development",
        results=results,
    )
    EvaluationRun.model_validate_json(run.model_dump_json()).validate_against(dataset)
    assert all(result.review == "unreviewed" for result in run.results)
    with pytest.raises(ValueError, match="exactly once"):
        run.model_copy(update={"results": results[:-1]}).validate_against(dataset)
    with pytest.raises(ValueError, match="exact dataset"):
        run.model_copy(update={"dataset_sha256": "0" * 64}).validate_against(dataset)


def test_failed_answer_cannot_be_marked_passed():
    with pytest.raises(ValidationError):
        CaseResult(
            case_id="shop-01",
            status="failed",
            error="timeout",
            latency_ms=1,
            review="pass",
            reviewer_notes="incorrectly passed",
        )
