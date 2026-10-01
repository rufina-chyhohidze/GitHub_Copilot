# Saved live development baseline: fixed-answer-v4

The saved run dated 2026-09-30 used hybrid retrieval and `gpt-4.1-mini-2025-04-14` for all 14 development questions. Its [summary and source-file fingerprint](development-live-v4-summary.json) preserve the measured status. Full answers, evidence, and traces remain in local `.data/development-answers-v4.json`; the matching review template is `.data/development-review-v4.json`. Held-out questions were not included.

| Measurement | Saved result |
| --- | --- |
| Answered / failed / skipped | 14 / 0 / 0 |
| Macro relevant-file recall at ten candidate chunks | 92.86% |
| Citation provenance validity | 100% |
| Mean case latency | 4.91 seconds |
| Completeness reviews completed / skipped for budget | 13 / 1 |
| Generation input / output tokens | 57,521 / 5,230 |
| Embedding tokens charged to this run | 0 |
| Estimated cost | Unknown; pricing was not supplied |
| Human quality review | Pending |
| All acceptance gates passed | No |

Citation validity establishes source identity, bounds, and hashes, not whether the claims accurately explain the evidence. The model's completeness revision does not count as rubric review. Zero embedding tokens in this run does not imply that preparing the cached index was free.

An earlier local v3 AI assessment recorded 92.31% supported claims, 84.62% required-fact coverage, and 57.14% complete case passes. These preliminary scores belong to v3 and must not be attributed to v4 or treated as human sign-off.

## Resumption on 2026-10-01

The interrupted workspace contained v4 documentation and traces, while the answering service still identified itself as v3 and returned after the first valid generation. The resumed implementation restores the documented second-call completeness review, preserving the two-call ceiling, bounded context, citation validation, and explicit review status. Tests cover successful revision, repair, invalid final output, and retaining the first valid draft when review exceeds the reserved input budget.

The saved live measurement predates this restoration; it is historical evidence, not a fresh measurement of the resumed code. Validation of the resumed implementation passed all 172 tests, including PostgreSQL integration tests, plus Ruff lint and formatting checks. Tests use deterministic or mocked providers.

## Remaining acceptance work

Run a fresh development baseline for the restored implementation and inspect its fingerprinted answers against the rubric. A human must assess claim support, required facts, forbidden claims, and uncertainty before acceptance. Improve any failing stages using development cases, then evaluate the held-out split at the milestone. Step 7 and Delivery A remain incomplete until the agreed quality gates pass.
