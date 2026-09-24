# reports

Future answer evaluations use `app.evaluation.results.EvaluationRun`: dataset identity/fingerprint, pipeline version, model, split, and one result per case. Each result includes answer text, citations, retrieved files, latency, token usage, errors, and review status.

Unreviewed answers are not passes. Dataset integrity checks are also not answer-quality scores; Step 2 verifies the benchmark material, while the baseline answer report arrives after retrieval and generation exist.

`lexical-development-v1.json` is the Step 5 retrieval-only baseline: 14 development cases, pinned source versions, and per-case relevant-file recall at ten candidate chunks. Macro recall is 82.1%; no model was called and no answer was evaluated. Local snapshot/run IDs identify the measured installation; reproduce on another database using `repo-copilot-retrieval-eval --prepare`.
