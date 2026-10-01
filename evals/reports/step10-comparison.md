# Step 10: fixed and bounded-agent development comparison

Measured on 2026-10-01 with `gpt-4.1-mini-2025-04-14`, hybrid retrieval, and the same snapshots, parsing runs, embedding profile and 14 Python development questions. The [machine-readable comparison](step10-comparison.json) records report fingerprints, source identities, usage and per-case measurements. Held-out and web-language questions were not evaluated.

| Measurement | Fixed | Agent |
| --- | ---: | ---: |
| Answered / failed | 14 / 0 | 13 / 1 |
| Initial file recall at ten candidate chunks | 92.86% | 92.86% |
| Emitted citation provenance validity | 100% | 100% |
| Mean latency, including failed cases | 6.04 s | 16.57 s |
| Reported model input tokens | 55,695 | 144,820 |
| Reported model output tokens | 5,498 | 4,436 |
| Query embedding tokens | 0 | 4 |
| Human quality review | Pending | Pending |

The agent's additional investigation reached multiple files in eight cases. It also repeated reads and frequently exhausted planning or context budgets. The `signing-01` case failed after 89.74 seconds during a provider call; no unvalidated answer was published. The safe trace does not distinguish an upstream failure from the provider deadline. Failed calls may have unreported usage. Provider cost is unknown because no pricing was supplied. Embedding caches were warm; timings are observations, not cold-start guarantees.

The measured agent adds 10.53 seconds of mean latency and 89,125 reported input tokens across the split. Lower output usage does not establish efficiency or completeness: one case failed and the answers differ. Citation validation establishes source provenance, line bounds and hashes, not semantic support. Initial retrieval recall is shared by design and does not measure whether later reads collected every required fact.

Keep the fixed pipeline as the default. This run does not establish that extra tool calls improve answer quality enough to justify their overhead. The implementation and failure handling are tested; agent quality acceptance is still pending, and the failed case prevents this run from passing every gate.

## Reproduce and review

Full local reports are `.data/step10-fixed.json` and `.data/step10-agent.json`. Their review templates are `.data/step10-fixed-review.json` and `.data/step10-agent-review.json`. A readable local view of questions, answers and exact evidence is `.data/step10-answers.md`. Follow the [evaluation guide](../../backend/app/evaluation/README.md) to score each fingerprinted report and regenerate the comparison. The earlier human approval of the September 30 answers does not apply to these new outputs.

The comparison was generated with:

```sh
cd backend
uv run repo-copilot-compare \
  --fixed ../.data/step10-fixed.json --agent ../.data/step10-agent.json \
  --output ../evals/reports/step10-comparison.json
```

For fresh generation, run `repo-copilot-answer-eval` once per `--pipeline fixed` and `--pipeline agent`, using the matching snapshot mappings recorded in the comparison. Keep the configured model, embedding profile, mode and split identical. Use new output paths to preserve the existing reports.

Live testing found and corrected two integration problems: missing tool parameter schemas in the planning prompt, and unhelpful feedback for reads beyond EOF. Earlier diagnostic runs remain locally under `step10-agent-initial*` and `step10-agent-schema*`; their timings and usage are excluded from this final comparison. The tracked report represents the final implementation, not the best result selected across reruns.

Validation: all 247 tests pass with PostgreSQL integration enabled, including bounded multi-file investigation, invalid tool requests, line-range recovery, usage limits, timeout handling, citation repair, API source identity, and matched-report comparison. Ruff lint and formatting checks pass.
