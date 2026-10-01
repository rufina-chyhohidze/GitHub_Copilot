"""Compare fixed and agent reports without mixing source identities or inventing reviews."""

import argparse
import json
from pathlib import Path

from app.config import PROJECT_ROOT
from app.evaluation.baseline import fingerprint, summarize
from app.evaluation.dataset import load_dataset


def compare(fixed, agent, dataset, *, fixed_review=None, agent_review=None):
    for key in ("snapshots", "parsing_runs", "mode", "embedding_profile"):
        if fixed[key] != agent[key]:
            raise ValueError(f"Comparison requires identical {key}")
    for key in ("dataset_sha256", "split", "model_id"):
        if fixed["run"][key] != agent["run"][key]:
            raise ValueError(f"Comparison requires identical {key}")
    if not fixed["run"]["pipeline_version"].startswith("fixed-answer-") or not agent["run"][
        "pipeline_version"
    ].startswith("repository-agent-"):
        raise ValueError("Provide fixed and agent reports in the correct order")
    summaries = {
        "fixed": summarize(fixed, dataset, fixed_review),
        "agent": summarize(agent, dataset, agent_review),
    }
    rows = {
        label: {row["case_id"]: row for row in report["run"]["results"]}
        for label, report in [("fixed", fixed), ("agent", agent)]
    }
    cases = []
    for case_id in rows["fixed"]:
        row = {"case_id": case_id}
        for label, report in [("fixed", fixed), ("agent", agent)]:
            trace = report["details"][case_id]["trace"] or {}
            result = rows[label][case_id]
            row[label] = {
                "status": result["status"],
                "latency_ms": result["latency_ms"],
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "model_calls": trace.get("model_calls", len(trace.get("attempts", []))),
                "tool_calls": trace.get("tool_calls"),
                "claim_count": len((trace.get("answer") or {}).get("claims", [])),
                "citation_count": len(trace.get("citations", [])),
                "stop_reason": trace.get("stop_reason"),
                "inspected_files": trace.get(
                    "inspected_files",
                    list(dict.fromkeys(e["path"] for e in trace.get("evidence", []))),
                ),
            }
        cases.append(row)
    return {
        "report_type": "fixed_agent_comparison",
        "fixed_sha256": fingerprint(fixed),
        "agent_sha256": fingerprint(agent),
        "identity": {
            "model_id": fixed["run"]["model_id"],
            "dataset_sha256": fixed["run"]["dataset_sha256"],
            "split": fixed["run"]["split"],
            "mode": fixed["mode"],
            "embedding_profile": fixed["embedding_profile"],
            "snapshots": fixed["snapshots"],
            "parsing_runs": fixed["parsing_runs"],
            "pipelines": {
                label: report["run"]["pipeline_version"]
                for label, report in [("fixed", fixed), ("agent", agent)]
            },
        },
        "usage": {"fixed": fixed["usage"], "agent": agent["usage"]},
        "summary": summaries,
        "cases": cases,
        "agent_minus_fixed": {
            "mean_latency_ms": summaries["agent"]["mean_case_latency_ms"]
            - summaries["fixed"]["mean_case_latency_ms"],
            "input_tokens": agent["usage"]["input"] - fixed["usage"]["input"],
            "output_tokens": agent["usage"]["output"] - fixed["usage"]["output"],
            "confirmed_case_pass_rate": summaries["agent"]["confirmed_case_pass_rate"]
            - summaries["fixed"]["confirmed_case_pass_rate"]
            if summaries["agent"]["human_review_complete"]
            and summaries["fixed"]["human_review_complete"]
            else None,
        },
        "notes": (
            "Initial recall uses ten candidates in both pipelines; additional agent reads "
            "are reported separately. Latency is observed, not a cold-start guarantee. "
            "Quality improvement is unknown without matched human reviews."
        ),
    }


def main():
    parser = argparse.ArgumentParser(prog="repo-copilot-compare")
    parser.add_argument("--fixed", type=Path, required=True)
    parser.add_argument("--agent", type=Path, required=True)
    parser.add_argument("--fixed-review", type=Path)
    parser.add_argument("--agent-review", type=Path)
    parser.add_argument(
        "--dataset", type=Path, default=PROJECT_ROOT / "evals/datasets/repository-qa-v1.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        inputs = [args.fixed, args.agent, args.fixed_review, args.agent_review, args.dataset]
        if args.output.resolve() in {p.resolve() for p in inputs if p}:
            raise ValueError("Comparison output must not overwrite an input")

        def read(path):
            return json.loads(path.read_text()) if path else None

        result = compare(
            read(args.fixed),
            read(args.agent),
            load_dataset(args.dataset),
            fixed_review=read(args.fixed_review),
            agent_review=read(args.agent_review),
        )
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result["agent_minus_fixed"], indent=2))
        return 0
    except (ValueError, KeyError, OSError):
        parser.exit(2, "Comparison failed; check matching reports, dataset and reviews.\n")


if __name__ == "__main__":
    raise SystemExit(main())
