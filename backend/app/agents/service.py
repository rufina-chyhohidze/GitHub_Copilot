"""One explicit investigation loop; deterministic budgets and final citation validation."""

import asyncio
import json
import time
from uuid import uuid4

from langsmith import tracing_context
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.agents.planner import Decision, OpenAIAgentPlanner
from app.agents.tools import RepositoryAgentTools
from app.answering.evidence import Draft, EvidenceRegistry, citation
from app.answering.service import validation_feedback
from app.db.schema import repositories
from app.models.contracts import UsageLimits
from app.tools.repository import RepositoryTools, literal_query

PIPELINE_VERSION = "repository-agent-v1"


class BudgetLimit(ValueError):
    pass


async def answer(
    engine,
    snapshot_id,
    question,
    settings,
    model,
    *,
    provider=None,
    mode="hybrid",
    run_id=None,
    planner=None,
):
    literal_query(question)
    if mode not in {"lexical", "hybrid"} or (mode == "hybrid" and provider is None):
        raise ValueError("Agent requires lexical mode or a hybrid embedding provider")
    started = time.monotonic()
    deadline = started + settings.run_timeout_seconds
    identity = uuid4()
    result = {
        "run_id": str(identity),
        "snapshot_id": str(snapshot_id),
        "pipeline_version": PIPELINE_VERSION,
        "status": "failed",
        "answer": None,
        "citations": [],
        "evidence": [],
        "attempts": [],
        "usage": {"input_tokens": 0, "output_tokens": 0, "embedding_input_tokens": 0},
        "tool_events": [],
        "tool_calls": 0,
        "model_calls": 0,
        "stop_reason": None,
    }
    runtime = None

    async def generate(adapter, scope, purpose):
        if result["model_calls"] >= settings.agent_max_model_calls:
            raise BudgetLimit("model_call_budget")
        input_left = settings.agent_input_token_budget - result["usage"]["input_tokens"]
        output_left = settings.agent_output_token_budget - result["usage"]["output_tokens"]
        prompt = json.dumps(scope, ensure_ascii=False)
        # A conservative reserve covers instructions, schema and escaped provider framing.
        if len(json.dumps(prompt, ensure_ascii=False).encode()) + 6000 > min(
            settings.max_context_tokens, input_left
        ):
            raise BudgetLimit("context_or_input_budget")
        if output_left < 1:
            raise BudgetLimit("output_token_budget")
        remaining = deadline - time.monotonic()
        if remaining < 1:
            raise TimeoutError
        limits = UsageLimits(
            max_context_tokens=min(settings.max_context_tokens, input_left),
            max_output_tokens=min(
                settings.max_output_tokens,
                output_left,
                512 if purpose == "planning" else settings.max_output_tokens,
            ),
            timeout_seconds=max(1, int(remaining)),
        )
        result["model_calls"] += 1
        response = await adapter.generate(prompt, limits=limits)
        result["usage"]["input_tokens"] += response.input_tokens
        result["usage"]["output_tokens"] += response.output_tokens
        attempt = {
            "model": response.model_id,
            "purpose": purpose,
            "valid": False,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }
        result["attempts"].append(attempt)
        if (
            response.input_tokens > limits.max_context_tokens
            or response.output_tokens > limits.max_output_tokens
        ):
            raise ValueError("Provider usage exceeded the requested limit")
        return response, attempt

    try:
        async with asyncio.timeout(settings.run_timeout_seconds):
            with (
                engine.connect() as connection,
                tracing_context(enabled=settings.agent_langsmith_enabled),
            ):
                connection.execute(
                    text("SELECT set_config('statement_timeout', :timeout, true)"),
                    {"timeout": str(settings.run_timeout_seconds * 1000)},
                )
                tools = RepositoryTools(connection, snapshot_id, run_id)
                result["parsing_run_id"] = str(tools.run["id"])
                result["commit_sha"] = tools.snapshot["commit_sha"]
                url = connection.execute(
                    select(repositories.c.canonical_url).where(
                        repositories.c.id == tools.snapshot["repository_id"]
                    )
                ).scalar_one()
                registry = EvidenceRegistry(tools, identity)
                runtime = RepositoryAgentTools(
                    engine, tools, registry, settings, provider, mode, deadline
                )
                # Keep the initial ten-candidate retrieval identical in scope to the baseline.
                await runtime.invoke("search", {"query": question, "limit": 10})
                feedback = None
                while (
                    runtime.calls < settings.max_tool_calls
                    and result["model_calls"] < settings.agent_max_model_calls - 2
                ):
                    planner = planner if planner is not None else OpenAIAgentPlanner(settings)
                    scope = {
                        "question": question,
                        "observations": runtime.observations,
                        "tools": {
                            name: {
                                "description": tool.description,
                                "parameters": tool.args_schema.model_json_schema(),
                            }
                            for name, tool in runtime.catalog.items()
                        },
                        "remaining_tool_calls": settings.max_tool_calls - runtime.calls,
                        "feedback": feedback,
                    }
                    try:
                        generated, attempt = await generate(planner, scope, "planning")
                    except BudgetLimit as exc:
                        result["stop_reason"] = str(exc)
                        break
                    try:
                        decision = Decision.model_validate_json(generated.text)
                        attempt["valid"] = True
                    except ValueError:
                        attempt["validation_error"] = {
                            "code": "invalid_action",
                            "message": "Choose one listed tool with valid parameters.",
                        }
                        feedback = attempt["validation_error"]
                        continue
                    if decision.action == "finish":
                        result["stop_reason"] = "agent_finished"
                        break
                    feedback = None
                    try:
                        await runtime.invoke(decision.action, decision.arguments())
                    except ValueError as exc:
                        result["stop_reason"] = str(exc)
                        break
                if result["stop_reason"] is None:
                    result["stop_reason"] = (
                        "tool_budget"
                        if runtime.calls >= settings.max_tool_calls
                        else "planning_call_budget"
                    )
                evidence = runtime.evidence
                result["evidence"] = evidence
                if not evidence:
                    draft = Draft(
                        claims=[],
                        uncertainty=(
                            "No exact citable evidence was obtained within the limits. "
                            "Failed or incomplete searches do not establish absence."
                        ),
                    )
                else:
                    scope = {
                        "question": question,
                        "evidence": evidence,
                        "coverage": tools.snapshot["coverage"],
                        "investigation_limits": result["stop_reason"],
                        "tool_errors": [
                            e["tool"] for e in runtime.events if e["status"] == "error"
                        ],
                        "instruction": (
                            "Answer only from the supplied exact evidence. Describe limitations; "
                            "do not treat unsuccessful searches as proof of absence."
                        ),
                    }
                    draft = None
                    for attempt_number in range(2):
                        try:
                            generated, attempt = await generate(
                                model, scope, "repair" if attempt_number else "answer"
                            )
                        except BudgetLimit as exc:
                            result["stop_reason"] = str(exc)
                            if attempt_number:
                                raise ValueError(
                                    "No remaining budget to repair an invalid answer"
                                ) from None
                            draft = Draft(
                                claims=[],
                                uncertainty=(
                                    "Source evidence was collected, but the generation budget "
                                    "was exhausted before a validated answer could be produced."
                                ),
                            )
                            break
                        try:
                            candidate = Draft.model_validate_json(generated.text)
                            registry.validate(candidate)
                            attempt["valid"] = True
                            draft = candidate
                            break
                        except ValueError as exc:
                            attempt["validation_error"] = validation_feedback(exc)
                            scope["validation_feedback"] = attempt["validation_error"]
                    if draft is None:
                        raise ValueError(
                            "Agent answer failed citation or structured-output validation"
                        )
                if time.monotonic() >= deadline:
                    raise TimeoutError
                registry.validate(draft)
                ids = dict.fromkeys(eid for claim in draft.claims for eid in claim.evidence_ids)
                result["citations"] = [
                    citation(registry.resolve(eid), url, result["commit_sha"]) for eid in ids
                ]
                result["answer"] = draft.model_dump()
                result["status"] = "completed"
    except TimeoutError:
        result["error"] = "Agent exceeded its elapsed-time limit"
        result["stop_reason"] = "timeout"
    except (ValueError, SQLAlchemyError, OSError):
        result["error"] = "Agent failed within its provider, tool, or validation limits"
    finally:
        if runtime is not None:
            result["tool_events"] = runtime.events
            result["tool_calls"] = runtime.calls
            result["returned_tool_bytes"] = runtime.returned_bytes
            result["evidence"] = runtime.evidence
            result["inspected_files"] = list(dict.fromkeys(runtime.inspected_files))
            result["tool_observations"] = runtime.observations
            result["usage"]["embedding_input_tokens"] = runtime.embedding_tokens
            result["retrieval"] = runtime.first_retrieval or {"retrieved_files": []}
            result["retrieval"]["parsing_run_id"] = str(runtime.tools.run["id"])
        result["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
    return result
