"""Offline validation is available before retrieval or models are implemented."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from app.config import PROJECT_ROOT
from app.evaluation.dataset import dataset_fingerprint, load_dataset, verify_source


def main() -> int:
    parser = argparse.ArgumentParser(prog="repo-copilot-eval")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "evals/datasets/repository-qa-v1.json",
    )
    parser.add_argument(
        "--checkout",
        action="append",
        default=[],
        metavar="SOURCE_ID=PATH",
        help="Verify a public source against an existing clean checkout (no download or execution)",
    )
    parser.add_argument("--require-all", action="store_true")
    args = parser.parse_args()
    try:
        dataset = load_dataset(args.dataset)
        roots = {}
        for item in args.checkout:
            source_id, separator, path = item.partition("=")
            if not separator or not path or source_id in roots:
                raise ValueError("Use each --checkout SOURCE_ID=PATH once")
            roots[source_id] = Path(path).resolve()
        public_ids = {source.id for source in dataset.sources if source.kind == "github"}
        if roots.keys() - public_ids:
            raise ValueError("Checkout ID must identify a GitHub source in the dataset")
        verified = {}
        unverified = []
        for source in dataset.sources:
            if source.kind == "fixture":
                root = PROJECT_ROOT / source.local_path
            elif source.id in roots:
                root = roots[source.id]
            else:
                unverified.append(source.id)
                continue
            verified[source.id] = verify_source(dataset, source, root)
        if args.require_all and unverified:
            raise ValueError("Missing public checkouts: " + ", ".join(unverified))
        print(
            json.dumps(
                {
                    "dataset": dataset.id,
                    "version": dataset.version,
                    "sha256": dataset_fingerprint(dataset),
                    "cases": len(dataset.cases),
                    "verified_cases_by_source": verified,
                    "unverified_sources": unverified,
                    "answer_quality_evaluated": False,
                },
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, ValidationError) as exc:
        parser.exit(2, f"Evaluation dataset validation failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
