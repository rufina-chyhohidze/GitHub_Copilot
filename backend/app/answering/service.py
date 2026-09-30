"""Retrieve once, read bounded evidence, generate, validate, and repair at most once."""

import asyncio
import json
import time
from uuid import UUID, uuid4

from sqlalchemy import select

from app.answering.evidence import Draft, EvidenceRegistry, citation
from app.db.schema import repositories
from app.models.contracts import UsageLimits
from app.retrieval.service import retrieve
from app.tools.repository import RepositoryTools, literal_query


async def answer(
    engine, snapshot_id, question, settings, model, *, provider=None, mode="hybrid", run_id=None
):
    literal_query(question)
    started = time.monotonic()
    result = {
        "run_id": str(uuid4()),
        "snapshot_id": str(snapshot_id),
        "pipeline_version": "fixed-answer-v1",
        "status": "failed",
        "answer": None,
        "citations": [],
        "evidence": [],
        "attempts": [],
        "usage": {"input_tokens": 0, "output_tokens": 0, "embedding_input_tokens": 0},
    }
    try:
        async with asyncio.timeout(settings.run_timeout_seconds):
            retrieved = await retrieve(
                engine, snapshot_id, question, settings, mode=mode, provider=provider, run_id=run_id
            )
            result["retrieval"] = retrieved
            result["usage"]["embedding_input_tokens"] = retrieved["input_tokens"]
            with engine.connect() as connection:
                tools = RepositoryTools(connection, snapshot_id, UUID(retrieved["parsing_run_id"]))
                registry = EvidenceRegistry(tools, UUID(result["run_id"]))
                result["commit_sha"] = tools.snapshot["commit_sha"]
                url = connection.execute(
                    select(repositories.c.canonical_url).where(
                        repositories.c.id == tools.snapshot["repository_id"]
                    )
                ).scalar_one()
                evidence = []
                # Reserve room for instructions, schema, question, framing and repair feedback.
                budget = max(0, settings.max_context_tokens - 6000)
                for row in retrieved["context"][: max(0, settings.max_tool_calls - 1)]:
                    entry = registry.add(row["path"], row["start_line"], row["end_line"])
                    if entry is None:
                        continue
                    if len(json.dumps(evidence + [entry], ensure_ascii=False).encode()) > budget:
                        del registry.entries[entry["id"]]
                        continue
                    evidence.append(entry)
                result["evidence"] = evidence
                scope = {
                    "mode": mode,
                    "retrieved_files": retrieved["retrieved_files"],
                    "coverage": tools.snapshot["coverage"],
                    "evidence": evidence,
                    "question": question,
                }
                if not evidence:
                    draft = Draft(
                        claims=[],
                        uncertainty=(
                            "No usable evidence was found within the retrieval and context limits. "
                            "This does not establish that the requested feature is absent."
                        ),
                    )
                else:
                    draft = None
                    for attempt in range(2):
                        prompt = json.dumps(scope, ensure_ascii=False)
                        if attempt:
                            prompt += (
                                "\nPrevious output failed validation. Return a fresh JSON "
                                "answer with claims and uncertainty; cite only supplied IDs."
                            )
                        remaining = settings.run_timeout_seconds - (time.monotonic() - started)
                        if remaining < 1:
                            raise ValueError("Answer run exceeded its time limit")
                        limits = UsageLimits(
                            max_context_tokens=settings.max_context_tokens,
                            max_output_tokens=settings.max_output_tokens,
                            timeout_seconds=max(1, int(remaining)),
                        )
                        generated = await model.generate(prompt, limits=limits)
                        result["usage"]["input_tokens"] += generated.input_tokens
                        result["usage"]["output_tokens"] += generated.output_tokens
                        result["attempts"].append(
                            {
                                "model": generated.model_id,
                                "valid": False,
                                "input_tokens": generated.input_tokens,
                                "output_tokens": generated.output_tokens,
                            }
                        )
                        try:
                            candidate = Draft.model_validate_json(generated.text)
                            registry.validate(candidate)
                            draft = candidate
                            result["attempts"][-1]["valid"] = True
                            break
                        except ValueError:
                            pass
                    if draft is None:
                        raise ValueError(
                            "Answer failed structured output or citation validation "
                            "after one repair attempt"
                        )
                registry.validate(draft)
                ids = dict.fromkeys(eid for claim in draft.claims for eid in claim.evidence_ids)
                result["citations"] = [
                    citation(registry.resolve(eid), url, result["commit_sha"]) for eid in ids
                ]
                result["answer"] = draft.model_dump()
                result["status"] = "completed"
    except TimeoutError:
        result["error"] = "Answer run exceeded its time limit"
    except ValueError as exc:
        result["error"] = str(exc)
    result["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
    return result
