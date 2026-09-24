# reports

Future answer evaluations use `app.evaluation.results.EvaluationRun`: dataset identity/fingerprint, pipeline version, model, split, and one result per case. Each result includes answer text, citations, retrieved files, latency, token usage, errors, and review status.

Unreviewed answers are not passes. Dataset integrity checks are also not answer-quality scores; Step 2 verifies the benchmark material, while the baseline answer report arrives after retrieval and generation exist.
