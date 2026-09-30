"""Fixed-pipeline evaluation; mechanical checks never substitute for claim review."""

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.answering.evidence import Draft, EvidenceRegistry, render
from app.answering.service import answer
from app.config import PROJECT_ROOT, Settings
from app.db.health import check_database
from app.db.session import make_engine
from app.evaluation.dataset import dataset_fingerprint, load_dataset
from app.evaluation.results import CaseResult, EvaluationRun, ResultCitation
from app.evaluation.retrieval import file_recall, prepare_sources, validate_snapshots
from app.models.contracts import Contract, Evidence
from app.providers.embeddings import OpenAIEmbeddings
from app.providers.text import OpenAITextModel
from app.retrieval.embedding_store import build_index, profile
from app.retrieval.service import retrieve
from app.tools.repository import RepositoryTools


class Pricing(Contract):
    """User-supplied USD rates per million tokens; missing rates mean unknown cost."""

    input: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    embedding: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    def estimate(self, usage):
        rates = self.model_dump()
        if any(usage[key] and rates[key] is None for key in rates):
            return None
        return sum(usage[key] * (rates[key] or 0) for key in rates) / 1_000_000


class Assessment(Contract):
    supported_claims: tuple[StrictBool, ...]
    required_facts: tuple[StrictBool, ...]
    uncertainty_correct: StrictBool
    forbidden_claims_absent: StrictBool
    failure_stage: Literal["none", "scanning", "parsing", "retrieval", "context", "generation"]
    notes: str = Field(min_length=1)


class Review(Contract):
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1)
    cases: dict[str, Assessment | None]


def fingerprint(report):
    return hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def review_template(report):
    return {
        "report_sha256": fingerprint(report),
        "reviewer": "REPLACE_WITH_REVIEWER_NAME",
        "cases": {row["case_id"]: None for row in report["run"]["results"]},
    }


def validate_citations(engine, snapshot_id, parsing_run, response):
    """Re-read saved source independently of the generator's claimed success."""
    if response["snapshot_id"] != str(snapshot_id):
        raise ValueError("Answer snapshot mismatch")
    with engine.connect() as connection:
        tools = RepositoryTools(connection, snapshot_id, parsing_run)
        if response["commit_sha"] != tools.snapshot["commit_sha"]:
            raise ValueError("Answer commit mismatch")
        registry = EvidenceRegistry(tools, UUID(response["run_id"]))
        excerpts = {entry["id"]: entry for entry in response["evidence"]}
        for row in response["citations"]:
            evidence = Evidence.model_validate(
                {key: value for key, value in row.items() if key != "url"}
            )
            registry.entries[row["id"]] = (evidence, excerpts[row["id"]]["content"])
        draft = Draft.model_validate(response["answer"])
        cited = {eid for claim in draft.claims for eid in claim.evidence_ids}
        if cited != set(registry.entries) or len(cited) != len(response["citations"]):
            raise ValueError("Citation list does not match answer claims")
        registry.validate(draft)
        return len(cited)


def summarize(report, dataset, review=None):
    run = EvaluationRun.model_validate(report["run"])
    run.validate_against(dataset)
    labels = {case.id: case for case in dataset.cases if case.split == run.split}
    rows = {row.case_id: row for row in run.results}
    details = report["details"]
    if set(details) != set(rows):
        raise ValueError("Diagnostics must include every selected case")
    assessments = {}
    if review is not None:
        review = Review.model_validate(review)
        if review.report_sha256 != fingerprint(report) or set(review.cases) != set(rows):
            raise ValueError("Review must match the exact report and every selected case")
        for case_id, assessment in review.cases.items():
            if assessment is None:
                continue
            if rows[case_id].status != "answered":
                raise ValueError("Failed or skipped cases cannot receive answer reviews")
            if len(assessment.supported_claims) != len(
                details[case_id]["trace"]["answer"]["claims"]
            ):
                raise ValueError("Review every generated claim in order")
            if len(assessment.required_facts) != len(labels[case_id].required_facts):
                raise ValueError("Review every required fact in dataset order")
            assessments[case_id] = assessment
    total = len(rows)
    recall = (
        sum(
            file_recall(labels[key].expected_files, row.retrieved_files)
            for key, row in rows.items()
        )
        / total
    )
    supported = [value for item in assessments.values() for value in item.supported_claims]
    fact_count = sum(len(case.required_facts) for case in labels.values())
    facts = sum(sum(item.required_facts) for item in assessments.values())
    passed = sum(
        all(item.supported_claims)
        and all(item.required_facts)
        and item.uncertainty_correct
        and item.forbidden_claims_absent
        for item in assessments.values()
    )
    answered = {key for key, row in rows.items() if row.status == "answered"}
    reviewed_all = answered == set(assessments) and not any(
        row.status == "skipped" for row in rows.values()
    )
    citation_count = sum(item["citation_count"] for item in details.values())
    valid_count = sum(item["valid_citation_count"] for item in details.values())
    citation_rate = valid_count / citation_count if citation_count else None
    support_rate = sum(supported) / len(supported) if supported else None
    absent = {key for key, case in labels.items() if case.category == "insufficient_evidence"}
    uncertainty = (
        all(
            assessments[key].uncertainty_correct and assessments[key].forbidden_claims_absent
            for key in absent
        )
        if absent and absent <= assessments.keys()
        else None
    )
    if any(rows[key].status == "failed" for key in absent):
        uncertainty = False
    gates = {
        "citation_validity": citation_rate == 1 if citation_rate is not None else None,
        "file_recall_at_10_chunks": recall >= 0.9,
        "claim_support": support_rate >= 0.9 if reviewed_all and support_rate is not None else None,
        "case_pass_rate": passed / total >= 0.8 if reviewed_all else None,
        "insufficient_evidence": uncertainty,
    }
    return {
        "cases": total,
        "answered": len(answered),
        "failed": sum(row.status == "failed" for row in rows.values()),
        "skipped": sum(row.status == "skipped" for row in rows.values()),
        "reviewed": len(assessments),
        "macro_file_recall_at_10_chunks": recall,
        "citation_validity": citation_rate,
        "reviewed_claim_support": support_rate,
        "confirmed_required_fact_coverage": facts / fact_count,
        "confirmed_case_pass_rate": passed / total,
        "mean_case_latency_ms": sum(row.latency_ms for row in rows.values()) / total,
        "gates": gates,
        "all_gates_pass": reviewed_all and all(value is True for value in gates.values()),
        "answer_quality_evaluated": bool(assessments),
    }


async def evaluate(
    dataset,
    engine,
    settings,
    snapshot_ids,
    *,
    split="development",
    mode="hybrid",
    model=None,
    provider=None,
    pricing=None,
    preparation_ms=None,
):
    if mode not in {"lexical", "hybrid"} or (mode == "hybrid" and provider is None):
        raise ValueError("Choose lexical mode or supply an embedding provider for hybrid mode")
    pricing = pricing or Pricing()
    runs = validate_snapshots(dataset, engine, snapshot_ids, split)
    indexing, failures = {}, {}
    for source_id, run_id in runs.items():
        started = time.monotonic()
        try:
            index = (
                await build_index(engine, snapshot_ids[source_id], settings, provider, run_id)
                if provider
                else {"input_tokens": 0}
            )
            indexing[source_id] = {**index, "latency_ms": (time.monotonic() - started) * 1000}
        except (ValueError, SQLAlchemyError, OSError, TimeoutError):
            failures[source_id] = (
                "Embedding indexing failed; inspect provider and database configuration"
            )
            indexing[source_id] = {
                "error": failures[source_id],
                "input_tokens": None,
                "latency_ms": (time.monotonic() - started) * 1000,
            }
    results, details = [], {}
    for case in dataset.cases:
        if case.split != split:
            continue
        started = time.monotonic()
        trace, retrieval = None, {}
        status, error, text = "failed", failures.get(case.source_id), None
        citations, citation_count, valid_count = (), 0, 0
        usage = {"input_tokens": 0, "output_tokens": 0, "embedding_input_tokens": 0}
        if error is None:
            try:
                if model is None:
                    retrieval = await retrieve(
                        engine,
                        snapshot_ids[case.source_id],
                        case.question,
                        settings,
                        mode=mode,
                        provider=provider,
                        run_id=runs[case.source_id],
                        limit=10,
                    )
                    usage["embedding_input_tokens"] = retrieval["input_tokens"]
                    status, error = "skipped", "Generation disabled: retrieval-only measurement"
                else:
                    # Labels and rubric are deliberately never included in the model input.
                    trace = await answer(
                        engine,
                        snapshot_ids[case.source_id],
                        case.question,
                        settings,
                        model,
                        provider=provider,
                        mode=mode,
                        run_id=runs[case.source_id],
                    )
                    retrieval, usage = trace.get("retrieval", {}), trace["usage"]
                    citation_count = len(trace["citations"])
                    if trace["status"] == "completed":
                        valid_count = validate_citations(
                            engine, snapshot_ids[case.source_id], runs[case.source_id], trace
                        )
                        text = render(trace)
                        citations = tuple(
                            ResultCitation(
                                path=row["source"]["path"],
                                start_line=row["source"]["start_line"],
                                end_line=row["source"]["end_line"],
                            )
                            for row in trace["citations"]
                        )
                        status = "answered"
                    else:
                        error = "Answer pipeline failed; inspect the case trace"
            except (ValueError, SQLAlchemyError, OSError, TimeoutError, KeyError):
                error = "Case failed during retrieval, generation, or citation validation"
        results.append(
            CaseResult(
                case_id=case.id,
                status=status,
                answer=text,
                citations=citations,
                retrieved_files=tuple(retrieval.get("retrieved_files", [])),
                latency_ms=(time.monotonic() - started) * 1000,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                error=error,
            )
        )
        details[case.id] = {
            "trace": trace,
            "retrieval": retrieval,
            "usage": usage,
            "citation_count": citation_count,
            "valid_citation_count": valid_count,
        }
    run = EvaluationRun(
        dataset_id=dataset.id,
        dataset_version=dataset.version,
        dataset_sha256=dataset_fingerprint(dataset),
        pipeline_version="fixed-answer-v1",
        model_id=(settings.model_id or "unspecified-test-model")
        if model
        else "generation-disabled",
        split=split,
        results=tuple(results),
    )
    usage = {
        "input": sum(row.input_tokens for row in results),
        "output": sum(row.output_tokens for row in results),
        "embedding": sum(row["usage"]["embedding_input_tokens"] for row in details.values())
        + sum(row["input_tokens"] or 0 for row in indexing.values()),
    }
    report = {
        "report_type": "answer_baseline",
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "run": run.model_dump(mode="json"),
        "details": details,
        "indexing": indexing,
        "preparation_ms": preparation_ms,
        "snapshots": {key: str(snapshot_ids[key]) for key in runs},
        "parsing_runs": {key: str(value) for key, value in runs.items()},
        "embedding_profile": profile(provider) if provider else None,
        "limits": {
            key: getattr(settings, key)
            for key in (
                "max_context_tokens",
                "max_output_tokens",
                "max_tool_calls",
                "run_timeout_seconds",
            )
        },
        "usage": usage,
        "pricing_usd_per_million": pricing.model_dump(),
        "estimated_cost_usd": pricing.estimate(usage)
        if not failures and all(row.status != "failed" for row in results)
        else None,
        "usage_note": (
            "Recorded usage only; failed provider requests may incur unreported usage. "
            "Cache hits cost zero additional embedding tokens."
        ),
    }
    report["summary"] = summarize(report, dataset)
    return report


def main():
    parser = argparse.ArgumentParser(prog="repo-copilot-answer-eval")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--snapshot", action="append", default=[], metavar="SOURCE_ID=SNAPSHOT_ID")
    parser.add_argument("--split", choices=["development", "held_out"], default="development")
    parser.add_argument("--mode", choices=["lexical", "hybrid"], default="hybrid")
    parser.add_argument("--retrieval-only", action="store_true", help="Skip generation explicitly")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--pricing", type=Path, help="JSON USD rates per million: input, output, embedding"
    )
    parser.add_argument("--report", type=Path, help="Existing report to summarize offline")
    parser.add_argument("--review", type=Path, help="Completed review for --report")
    parser.add_argument("--review-template", type=Path)
    parser.add_argument(
        "--require-gates", action="store_true", help="Exit 1 unless every quality gate passes"
    )
    args = parser.parse_args()
    engine = None
    try:
        dataset = load_dataset(PROJECT_ROOT / "evals/datasets/repository-qa-v1.json")
        if args.review and not args.report:
            raise ValueError("--review requires --report")
        inputs = [path.resolve() for path in (args.report, args.review, args.pricing) if path]
        outputs = [path.resolve() for path in (args.output, args.review_template) if path]
        if len(outputs) != len(set(outputs)) or set(inputs) & set(outputs):
            raise ValueError("Output paths must be distinct from each other and from input files")
        if args.report:
            report = json.loads(args.report.read_text(encoding="utf-8"))
            review = json.loads(args.review.read_text(encoding="utf-8")) if args.review else None
            output = {
                "report_sha256": fingerprint(report),
                "summary": summarize(report, dataset, review),
                "review": review,
            }
        else:
            settings = Settings()
            model = None if args.retrieval_only else OpenAITextModel(settings)
            provider = OpenAIEmbeddings(settings) if args.mode == "hybrid" else None
            pricing = (
                Pricing.model_validate_json(args.pricing.read_text()) if args.pricing else Pricing()
            )
            engine = make_engine(settings)
            check_database(engine)
            started = time.monotonic()
            snapshots = prepare_sources(dataset, engine, settings) if args.prepare else {}
            preparation_ms = (time.monotonic() - started) * 1000 if args.prepare else None
            for item in args.snapshot:
                name, separator, value = item.partition("=")
                if not separator or name not in {source.id for source in dataset.sources}:
                    raise ValueError("Use --snapshot KNOWN_SOURCE_ID=SNAPSHOT_ID")
                snapshots[name] = UUID(value)
            report = asyncio.run(
                evaluate(
                    dataset,
                    engine,
                    settings,
                    snapshots,
                    split=args.split,
                    mode=args.mode,
                    model=model,
                    provider=provider,
                    pricing=pricing,
                    preparation_ms=preparation_ms,
                )
            )
            output = report
        if args.review_template:
            args.review_template.write_text(
                json.dumps(review_template(report), indent=2) + "\n", encoding="utf-8"
            )
        args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(output["summary"], indent=2))
        return int(
            bool(output["summary"]["failed"])
            or (args.require_gates and not output["summary"]["all_gates_pass"])
        )
    except (ValidationError, SQLAlchemyError, OSError, KeyError):
        parser.exit(
            2, "Evaluation failed; check configuration, snapshots, and input/output files.\n"
        )
    except ValueError as exc:
        parser.exit(2, f"Evaluation failed: {exc}\n")
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
