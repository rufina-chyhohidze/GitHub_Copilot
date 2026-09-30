# Development baseline, 2026-09-30

The [machine-readable report](development-baseline-v1.json) measures all 14 development questions against existing pinned snapshots using lexical retrieval. Generation was explicitly disabled because the local configuration has no model, API key, or embedding settings. This is an infrastructure and retrieval measurement, not a live answer-quality baseline. Held-out questions were not run.

| Measurement | Result |
| --- | --- |
| Macro relevant-file recall at ten candidate chunks | 82.14%, below the 90% target |
| Cases with every expected file retrieved | 9 of 14 |
| Mean case latency on existing local snapshots | 9.28 ms |
| Generation | 14 skipped, 0 answered, 0 failed |
| Citation validity and factual support | Unmeasured |
| Provider token usage and additional cost | 0 tokens, $0 |
| All acceptance gates passed | No |

The recall matches the earlier Step 5 lexical baseline. Existing parsed snapshots were reused, so this run does not measure initial acquisition/parsing time or embedding index construction. The report stores null preparation time; lexical mode performs no embedding work.

## Retrieval failures

| Case | Missing expected file | Observed result |
| --- | --- | --- |
| shop-01, authentication location | `shop/routes.py` | README, authentication implementation, and notes retrieved; the route integration is missing. |
| shop-06, constructing a User | `shop/services.py` | Seven files retrieved, including the model and repository, but not the construction site. |
| shop-08, JWT algorithm | `shop/auth.py` | README, app, and several other files retrieved; authentication context is missing. |
| shop-09, Google OAuth | `shop/app.py` | Only the README is retrieved; application context for a scoped absence answer is missing. |
| sample-01, sample command | `src/sample/__init__.py` | Packaging and workflow files are retrieved, but the command implementation is missing. |

These are retrieval-stage misses: the expected supporting files exist in the verified snapshots but are outside the returned candidate set. The current lexical scorer uses substring matches and deterministic path ordering for score ties. Generic question words and indirect references are plausible contributors; this run does not isolate their individual effects. Missing retrieval is never proof that authentication, JWT, or OAuth is absent.

## Remaining work

Configure the provider and run the same development split with hybrid retrieval and answer generation. Compare candidate recall and inspect evidence selection before changing generation prompts. Review claim support, required facts, forbidden claims, and uncertainty using the fingerprinted review template. Use stage-specific findings to improve retrieval or context assembly, rerun development cases, and reserve held-out cases for milestone evaluation after tuning.

Record actual model IDs, supplied pricing, cache state, and indexing usage in the new report. Do not promote mocked-provider integration checks to model-quality evidence. Step 7 and Delivery A remain incomplete until live review and the agreed quality gates pass.
