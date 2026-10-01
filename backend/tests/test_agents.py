import asyncio
import copy
import json

import httpx
import pytest
from test_baseline import reviewed, synthetic_report

from app.agents.planner import INSTRUCTIONS, Decision, OpenAIAgentPlanner
from app.config import PROJECT_ROOT, Settings
from app.evaluation.compare import compare
from app.evaluation.dataset import load_dataset
from app.models.contracts import UsageLimits


def test_planner_schema_is_strict_and_requests_no_reasoning():
    def handler(request):
        payload = json.loads(request.content)
        schema = payload["text"]["format"]["schema"]
        assert payload["store"] is False and payload["text"]["format"]["strict"]
        assert schema["additionalProperties"] is False
        assert "shell" not in schema["properties"]["action"]["enum"]
        assert set(schema["required"]) == {
            "action",
            "path",
            "query",
            "start_line",
            "end_line",
            "limit",
        }
        assert "untrusted" in payload["instructions"]
        assert "chain-of-thought" in INSTRUCTIONS
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": "test",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "action": "finish",
                                        "path": None,
                                        "query": None,
                                        "start_line": None,
                                        "end_line": None,
                                        "limit": None,
                                    }
                                ),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 30},
            },
        )

    planner = OpenAIAgentPlanner(
        Settings(_env_file=None, model_id="test", provider_api_key="test-secret"),
        transport=httpx.MockTransport(handler),
    )
    response = asyncio.run(planner.generate("{}", limits=UsageLimits()))
    assert Decision.model_validate_json(response.text).action == "finish"


def test_decision_rejects_injected_tools_snapshot_ids_and_large_ranges():
    valid = {
        "action": "find_references",
        "query": "save",
        "path": None,
        "start_line": None,
        "end_line": None,
        "limit": 20,
    }
    assert Decision.model_validate(valid).arguments() == {"name": "save", "limit": 20}
    for update in [{"action": "shell"}, {"snapshot_id": "other"}, {"start_line": 0}]:
        with pytest.raises(ValueError):
            Decision.model_validate({**valid, **update})


def reports():
    dataset = load_dataset(PROJECT_ROOT / "evals/datasets/repository-qa-v1.json")
    fixed = synthetic_report(dataset)
    fixed["run"]["pipeline_version"] = "fixed-answer-v4"
    fixed.update(
        snapshots={"shop": "one"},
        parsing_runs={"shop": "run"},
        mode="hybrid",
        embedding_profile=None,
        usage={"input": 100, "output": 20},
    )
    agent = copy.deepcopy(fixed)
    agent["run"]["pipeline_version"] = "repository-agent-v1"
    agent["usage"] = {"input": 200, "output": 50}
    return dataset, fixed, agent


def test_comparison_does_not_invent_quality_improvement_without_review():
    dataset, fixed, agent = reports()
    result = compare(fixed, agent, dataset)
    assert result["agent_minus_fixed"]["input_tokens"] == 100
    assert result["agent_minus_fixed"]["confirmed_case_pass_rate"] is None
    reviewed_result = compare(
        fixed,
        agent,
        dataset,
        fixed_review=reviewed(fixed, dataset),
        agent_review=reviewed(agent, dataset),
    )
    assert reviewed_result["agent_minus_fixed"]["confirmed_case_pass_rate"] == 0


@pytest.mark.parametrize("field", ["snapshots", "parsing_runs", "mode", "embedding_profile"])
def test_comparison_rejects_unmatched_sources_or_retrieval(field):
    dataset, fixed, agent = reports()
    agent[field] = "different"
    with pytest.raises(ValueError, match="identical"):
        compare(fixed, agent, dataset)
