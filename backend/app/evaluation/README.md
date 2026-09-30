# Evaluation

`dataset.py` defines and loads versioned questions, source identities, and evidence spans. Its validator reads source files and checks hashes, line ranges, and anchor text without importing or running them.

`results.py` defines answer-run output, including usage, errors, citations, and explicit review status. `cli.py` checks dataset integrity. `retrieval.py` prepares pinned snapshots and measures retrieval. `baseline.py` runs the fixed answering pipeline, revalidates citations against stored files, and combines mechanical measurements with explicit human review.

## Run a baseline

From `backend/`, with the database running and migrations applied:

```sh
uv sync --locked
uv run repo-copilot-answer-eval --prepare --split development --mode hybrid \
  --output ../.data/development-answers-v1.json \
  --review-template ../.data/development-review-v1.json
```

Create the output directory first if it does not exist. Configure the model, API key, and embedding settings as described in the [answering guide](../answering/README.md). This command makes paid provider calls. All source hashes and commits are checked before indexing or answering. Instead of `--prepare`, supply one `--snapshot SOURCE_ID=SNAPSHOT_ID` per source to use existing parsed snapshots.

To measure lexical retrieval without a provider, add `--mode lexical --retrieval-only`. Every selected case is retained with generation explicitly marked `skipped`. This exercises the report workflow but cannot pass the answer-quality gates. Generation is never silently replaced with a fake model. The default split is development; use `--split held_out` only for milestone evaluation after tuning. Expected files, facts, and review labels never enter generation prompts.

The report contains the dataset fingerprint, model, embedding profile, pipeline version, pinned snapshots and parsing runs, configured answer limits, per-case traces, latency, and usage. `preparation_ms` measures combined ingestion/parsing when `--prepare` is used; otherwise it is null. `indexing` records per-source embedding duration, usage, and cache reuse. Per-case latency includes retrieval, generation, and the evaluator's citation recheck. These are observed timings, not cold-start performance guarantees.

Optional `--pricing rates.json` reads USD rates per million tokens with keys `input`, `output`, and `embedding`. Supply rates appropriate to the configured models. Missing rates yield an unknown cost when those tokens are used; no prices are hard-coded. Usage includes both generation attempts, query embeddings, and index-building embeddings. Estimates use gross input/output rates without provider-specific cache discounts. Failed requests may incur unreported usage, so runs with failures have a null cost estimate.

## Review answers

The review template contains the report fingerprint, a reviewer name to fill in, and a null entry for every selected case. Replace each answered case's null with an assessment:

```json
{
  "supported_claims": [true, false],
  "required_facts": [true, false, true],
  "uncertainty_correct": true,
  "forbidden_claims_absent": true,
  "failure_stage": "retrieval",
  "notes": "Explain the evidence and the missing fact here."
}
```

The arrays above are illustrative: `supported_claims` must have one boolean per generated claim, in trace order; `required_facts` must have one boolean per dataset fact, in dataset order. Inspect the saved evidence and pinned files. Matching a keyword or having a valid source location does not establish support. For a claim containing multiple factual assertions, mark support true only when every assertion is supported. Assess the case's uncertainty policy and forbidden claims separately. Use `failure_stage` to record `scanning`, `parsing`, `retrieval`, `context`, or `generation`; use `none` for a pass. Keep failed, skipped, or not-yet-reviewed cases null.

```sh
uv run repo-copilot-answer-eval \
  --report ../.data/development-answers-v1.json \
  --review ../.data/development-review-v1.json \
  --output ../.data/development-reviewed-v1.json --require-gates
```

This command runs offline and writes the review, report fingerprint, and scored summary without modifying the original report. A review of another report, missing cases, or incomplete claim/fact arrays is rejected. Unreviewed answers never count as passes. Failed and skipped cases remain in the full-split recall, required-fact, and case-pass denominators. Confirmed coverage/pass rates are lower bounds until review is complete. Claim support is reported over reviewed claims; its gate stays unknown until all answered cases are reviewed and no cases are skipped.

The gates retain the plan's thresholds: 100% citation validity, 90% file recall at ten candidate chunks, 90% reviewed claim support, 80% complete case passes, and correct uncertainty for every insufficient-evidence case. A case passes only when all its claims and required facts pass, uncertainty is correct, and forbidden claims are absent. No citations means citation validity is unknown, not 100%. Unknown gates never pass. Citation rechecks verify provenance and content hashes, not semantic support.

Exit codes: 0 means the requested measurement/scoring completed without case failures; 1 means a case failed, or `--require-gates` found an unmet/unknown gate; 2 means configuration, source validation, or report input failed before measurement completed. Source-preparation failures stop the run rather than fabricate per-case outcomes. Once evaluation starts, expected indexing/provider failures retain affected cases as failed results and allow the remaining cases to run.

See the [development baseline analysis](../../../evals/reports/development-baseline-v1.md) for measured results and remaining work.
