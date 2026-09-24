"""Measure relevant-file retrieval on pinned sources before generating any answers."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import PROJECT_ROOT, Settings
from app.db.health import check_database
from app.db.schema import repository_files
from app.db.session import make_engine
from app.evaluation.dataset import dataset_fingerprint, load_dataset, verify_source
from app.ingestion.parsing import parse_snapshot
from app.ingestion.scanner import LANGUAGES, ManifestEntry, ScanResult, StoredFile
from app.ingestion.service import ingest, save_snapshot
from app.providers.embeddings import OpenAIEmbeddings
from app.retrieval.embedding_store import build_index
from app.retrieval.service import retrieve
from app.tools.repository import RepositoryTools


def file_recall(expected, retrieved) -> float:
    return len(set(expected) & set(retrieved)) / len(set(expected))


def prepare_sources(dataset, engine, settings) -> dict:
    prepared = {}
    for source in dataset.sources:
        if source.kind == "github":
            result = ingest(source.url, source.commit_sha, settings, engine)
        else:
            root = PROJECT_ROOT / source.local_path
            verify_source(dataset, source, root)
            files, manifest = [], []
            for path in sorted(source.files):
                raw = (root / path).read_bytes()
                content = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                files.append(
                    StoredFile(
                        path,
                        LANGUAGES.get(Path(path).suffix, "text"),
                        content,
                        hashlib.sha256(content.encode()).hexdigest(),
                        hashlib.sha256(raw).hexdigest(),
                        len(raw),
                        content.count("\n") + int(bool(content) and not content.endswith("\n")),
                    )
                )
                manifest.append(ManifestEntry(path, source.files[path], len(raw), "stored"))
            identity = hashlib.sha256(json.dumps(source.files, sort_keys=True).encode()).hexdigest()
            with engine.begin() as connection:
                result = save_snapshot(
                    connection,
                    f"fixture://{source.id}",
                    identity,
                    f"fixture-{source.version}",
                    ScanResult(tuple(files), tuple(manifest)),
                    time.monotonic() + 60,
                )
        snapshot_id = UUID(result["snapshot_id"])
        parse_snapshot(snapshot_id, settings, engine)
        prepared[source.id] = snapshot_id
    return prepared


async def evaluate(
    dataset, engine, settings, snapshot_ids, split="development", mode="lexical", provider=None
):
    cases = [case for case in dataset.cases if case.split == split]
    needed = {case.source_id for case in cases}
    if needed - snapshot_ids.keys():
        raise ValueError("Supply every source in the selected split: " + ", ".join(sorted(needed)))
    runs = {}
    for source in dataset.sources:
        if source.id not in needed:
            continue
        with engine.connect() as connection:
            tools = RepositoryTools(connection, snapshot_ids[source.id])
            if source.kind == "github" and tools.snapshot["commit_sha"] != source.commit_sha:
                raise ValueError(f"Snapshot commit mismatch for {source.id}")
            files = dict(
                connection.execute(
                    select(repository_files.c.path, repository_files.c.raw_hash).where(
                        repository_files.c.snapshot_id == snapshot_ids[source.id]
                    )
                ).all()
            )
            if source.kind == "fixture" and set(files) != set(source.files):
                raise ValueError(f"Fixture file list mismatch for {source.id}")
            if any(files.get(path) != digest for path, digest in source.files.items()):
                raise ValueError(f"Snapshot source hash mismatch for {source.id}")
            runs[source.id] = tools.run["id"]
        if mode != "lexical":
            await build_index(engine, snapshot_ids[source.id], settings, provider, runs[source.id])
    results = []
    for case in cases:
        response = await retrieve(
            engine,
            snapshot_ids[case.source_id],
            case.question,
            settings,
            mode=mode,
            provider=provider,
            run_id=runs[case.source_id],
            limit=10,
        )
        results.append(
            {
                "case_id": case.id,
                "source_id": case.source_id,
                "expected_files": list(case.expected_files),
                "retrieved_files": response["retrieved_files"],
                "file_recall_at_10_chunks": file_recall(
                    case.expected_files, response["retrieved_files"]
                ),
                "latency_ms": response["latency_ms"],
                "input_tokens": response["input_tokens"],
            }
        )
    return {
        "report_type": "retrieval_baseline",
        "dataset_id": dataset.id,
        "dataset_version": dataset.version,
        "dataset_sha256": dataset_fingerprint(dataset),
        "split": split,
        "mode": mode,
        "pipeline_version": "hybrid-v1",
        "embedding_model": provider.model_id if provider else None,
        "snapshots": {key: str(value) for key, value in snapshot_ids.items()},
        "parsing_runs": {key: str(value) for key, value in runs.items()},
        "cases": results,
        "macro_file_recall_at_10_chunks": sum(row["file_recall_at_10_chunks"] for row in results)
        / len(results),
        "answer_quality_evaluated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="repo-copilot-retrieval-eval")
    parser.add_argument(
        "--prepare", action="store_true", help="Ingest pinned public sources and local fixture"
    )
    parser.add_argument("--snapshot", action="append", default=[], metavar="SOURCE_ID=SNAPSHOT_ID")
    parser.add_argument("--split", choices=["development", "held_out"], default="development")
    parser.add_argument("--mode", choices=["lexical", "semantic", "hybrid"], default="lexical")
    args = parser.parse_args()
    engine = None
    try:
        settings = Settings()
        dataset = load_dataset(PROJECT_ROOT / "evals/datasets/repository-qa-v1.json")
        engine = make_engine(settings)
        check_database(engine)
        source_ids = prepare_sources(dataset, engine, settings) if args.prepare else {}
        for item in args.snapshot:
            name, separator, value = item.partition("=")
            if not separator:
                raise ValueError("Use --snapshot SOURCE_ID=SNAPSHOT_ID")
            source_ids[name] = UUID(value)
        provider = OpenAIEmbeddings(settings) if args.mode != "lexical" else None
        print(
            json.dumps(
                asyncio.run(
                    evaluate(dataset, engine, settings, source_ids, args.split, args.mode, provider)
                ),
                indent=2,
            )
        )
        return 0
    except (ValidationError, SQLAlchemyError, OSError):
        # Settings/DB/provider exceptions can contain credentials; keep this CLI error generic.
        parser.exit(
            2,
            "Retrieval evaluation failed; check database, source snapshots, "
            "and provider settings.\n",
        )
    except ValueError as exc:
        parser.exit(2, f"Retrieval evaluation failed: {exc}\n")
    finally:
        if engine is not None:
            engine.dispose()
