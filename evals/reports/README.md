# reports

Answer evaluations embed `app.evaluation.results.EvaluationRun`: dataset identity/fingerprint, pipeline version, model, split, and one result per case. Each result includes answer text, citations, retrieved files, latency, token usage, errors, and review status. The report also preserves traces, indexing metadata, and separate mechanical metrics. Reviews reference the exact report fingerprint.

Unreviewed answers are not passes. Dataset integrity checks are also not answer-quality scores. See the [evaluation workflow](../../backend/app/evaluation/README.md) for generation, review, and quality-gate commands.

`lexical-development-v1.json` is the Step 5 retrieval-only baseline: 14 development cases, pinned source versions, and per-case relevant-file recall at ten candidate chunks. Macro recall is 82.1%; no model was called and no answer was evaluated. Local snapshot/run IDs identify the measured installation; reproduce on another database using `repo-copilot-retrieval-eval --prepare`.

`development-baseline-v1.json` exercises the Step 7 report format against the same 14 development questions with lexical retrieval. All generation outcomes are explicit skips, model usage is zero, and answer-quality gates remain unknown. The [analysis](development-baseline-v1.md) explains the five retrieval misses and next measurements. Held-out questions were not run or used for tuning.
