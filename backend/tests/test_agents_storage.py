import asyncio
import json
from uuid import UUID

import pytest
from test_baseline_storage import EvidenceModel
from test_retrieval_storage import TransactionEngine, snapshot
from test_snapshot_storage import pytestmark  # noqa: F401

from app.agents.service import answer
from app.config import PROJECT_ROOT, Settings
from app.evaluation.baseline import evaluate, validate_citations
from app.evaluation.dataset import load_dataset
from app.evaluation.retrieval import prepare_sources
from app.providers.interfaces import Generation
from app.tools.repository import RepositoryTools


class Planner:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.prompts = []

    async def generate(self, prompt, *, limits):
        self.prompts.append(prompt)
        action = next(self.actions, {"action": "finish"})
        value = dict(
            action="finish", path=None, query=None, start_line=None, end_line=None, limit=None
        )
        value.update(action)
        return Generation(
            text=json.dumps(value), model_id="test", input_tokens=100, output_tokens=30
        )


def run(connection, actions, settings=None, model=None):
    sid, rid = snapshot(connection)
    result = asyncio.run(
        answer(
            TransactionEngine(connection),
            sid,
            "authenticate",
            settings or Settings(_env_file=None),
            model or EvidenceModel(),
            mode="lexical",
            run_id=rid,
            planner=Planner(actions),
        )
    )
    return result, sid, rid


def test_agent_investigates_multiple_files_with_valid_citations(connection):
    actions = [
        {"action": "get_imports", "path": "auth.py"},
        {"action": "find_references", "query": "authenticate"},
        {"action": "read_file", "path": "auth.py", "start_line": 1, "end_line": 2},
        {"action": "read_file", "path": "orders.py", "start_line": 1, "end_line": 2},
    ]
    result, sid, rid = run(connection, actions)
    assert result["status"] == "completed", result
    assert result["pipeline_version"] == "repository-agent-v1"
    assert result["inspected_files"] == ["auth.py", "orders.py"]
    assert result["tool_calls"] == 5
    assert result["model_calls"] == 6
    assert result["usage"]["input_tokens"] == 600
    assert validate_citations(TransactionEngine(connection), sid, rid, result) == 1
    references = result["tool_observations"][2]["result"]
    assert (
        references["kind"] == "candidate_text_occurrences"
        and references["confirmed_calls"] is False
    )
    assert result["tool_observations"][1]["result"]["resolved_module_targets"] is False
    assert all("reasoning" not in event for event in result["tool_events"])


def test_invalid_tool_requests_cannot_escape_snapshot_or_execute_code(connection):
    result, _, _ = run(
        connection,
        [
            {"action": "shell", "query": "touch /tmp/never"},
            {"action": "read_file", "path": "../auth.py", "start_line": 1, "end_line": 2},
            {"action": "read_file", "path": "auth.py", "start_line": 1, "end_line": 100},
            {"action": "read_file", "path": "auth.py", "start_line": 1, "end_line": 2},
        ],
    )
    assert result["status"] == "completed"
    assert result["attempts"][0]["validation_error"]["code"] == "invalid_action"
    assert result["tool_events"][1]["status"] == "error"
    invalid_range = result["tool_observations"][2]["result"]
    assert invalid_range["error"] == "invalid_line_range"
    assert 2 <= invalid_range["available_lines"] < 100
    assert result["evidence"][0]["path"] == "auth.py"


def test_budget_exhaustion_returns_uncertainty_without_unverified_claims(connection):
    result, _, _ = run(connection, [], Settings(_env_file=None, max_tool_calls=1))
    assert result["status"] == "completed" and not result["answer"]["claims"]
    assert result["stop_reason"] == "tool_budget"
    assert result["model_calls"] == 0 and result["tool_calls"] == 1
    assert "absence" in result["answer"]["uncertainty"]


@pytest.mark.parametrize(
    "setting,value",
    [
        ("agent_input_token_budget", 1),
        ("agent_output_token_budget", 1),
        ("agent_max_model_calls", 2),
    ],
)
def test_generation_budgets_are_enforced_outside_model(connection, setting, value):
    actions = [{"action": "read_file", "path": "auth.py", "start_line": 1, "end_line": 2}] * 10
    result, _, _ = run(connection, actions, Settings(_env_file=None, **{setting: value}))
    assert result["model_calls"] <= 2
    assert not result["citations"]
    if setting == "agent_output_token_budget":
        assert (
            result["status"] == "failed"
        )  # The fake provider violated its requested one-token cap.
    else:
        assert result["status"] == "completed"


def test_tool_byte_limits_withhold_evidence_instead_of_citing_truncated_source(connection):
    result, _, _ = run(
        connection,
        [{"action": "read_file", "path": "auth.py", "start_line": 1, "end_line": 2}],
        Settings(_env_file=None, agent_total_tool_bytes=256, agent_tool_result_bytes=256),
    )
    assert not result["evidence"] and not result["citations"]
    assert result["returned_tool_bytes"] <= 256


@pytest.mark.parametrize("failures", [1, 2])
def test_agent_repairs_invalid_citations_once(connection, failures):
    class Model:
        calls = 0

        async def generate(self, prompt, *, limits):
            self.calls += 1
            scope = json.loads(prompt)
            if self.calls > 1:
                assert scope["validation_feedback"]["code"] == "unknown_evidence"
            return Generation(
                model_id="test",
                input_tokens=100,
                output_tokens=20,
                text=json.dumps(
                    {
                        "claims": [
                            {
                                "text": "Auth returns token.",
                                "evidence_ids": [
                                    "unknown"
                                    if self.calls <= failures
                                    else scope["evidence"][0]["id"]
                                ],
                            }
                        ],
                        "uncertainty": None,
                    }
                ),
            )

    result, _, _ = run(
        connection,
        [{"action": "read_file", "path": "auth.py", "start_line": 1, "end_line": 2}],
        model=Model(),
    )
    assert result["status"] == ("failed" if failures == 2 else "completed")
    assert result["attempts"][-1]["purpose"] == "repair"
    if failures == 2:
        assert result["answer"] is None and not result["citations"]


def test_slow_planner_times_out_without_publishing_answer(connection):
    class Slow:
        async def generate(self, *args, **kwargs):
            await asyncio.sleep(3)

    sid, rid = snapshot(connection)
    result = asyncio.run(
        answer(
            TransactionEngine(connection),
            sid,
            "auth",
            Settings(_env_file=None, run_timeout_seconds=2),
            EvidenceModel(),
            mode="lexical",
            run_id=rid,
            planner=Slow(),
        )
    )
    assert result["status"] == "failed" and result["stop_reason"] == "timeout"
    assert not result["citations"]


def test_agent_baseline_uses_same_snapshots_and_keeps_labels_out_of_prompts(connection):
    dataset = load_dataset(PROJECT_ROOT / "evals/datasets/repository-qa-v1.json")
    dataset = dataset.model_copy(
        update={"sources": dataset.sources[:1], "cases": dataset.cases[:1]}
    )
    settings = Settings(_env_file=None)
    engine = TransactionEngine(connection)
    snapshots = prepare_sources(dataset, engine, settings)
    planner = Planner(
        [{"action": "read_file", "path": "shop/auth.py", "start_line": 1, "end_line": 10}]
    )
    report = asyncio.run(
        evaluate(
            dataset,
            engine,
            settings,
            snapshots,
            mode="lexical",
            pipeline="agent",
            planner=planner,
            model=EvidenceModel(),
        )
    )
    assert report["run"]["pipeline_version"] == "repository-agent-v1"
    assert report["summary"]["failed"] == 0
    assert report["usage"]["input"] > 0
    assert not report["summary"]["all_gates_pass"]
    for prompt in planner.prompts:
        catalog = json.loads(prompt)["tools"]
        assert set(catalog["read_file"]["parameters"]["properties"]) == {
            "path",
            "start_line",
            "end_line",
        }
        assert catalog["read_file"]["parameters"]["additionalProperties"] is False
        assert "required_facts" not in prompt and "expected_files" not in prompt
    with engine.connect() as conn:
        tools = RepositoryTools(
            conn, next(iter(snapshots.values())), UUID(next(iter(report["parsing_runs"].values())))
        )
        assert tools.get_imports("shop/auth.py")["resolution"] == "syntactic_only"
