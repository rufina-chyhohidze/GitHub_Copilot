# Answers with validated citations

The fixed pipeline uses hybrid retrieval → bounded stored-file reads → run-local evidence registry → structured claims → citation validation. It makes at most two generation calls: the second repairs an invalid first answer, or reviews a valid draft for completeness and citation support. The review uses the same evidence and question, never evaluation labels. An invalid final attempt produces a failed result without publishing its text or citations.

Step 7 versions the current pipeline as `fixed-answer-v4` (v2 and v3 identify intermediate development measurements). Retrieved files of at most 3,000 UTF-8 bytes are expanded in full; larger files can expand to a complete enclosing symbol of at most 3,000 bytes. Existing aggregate context limits and exact-chunk fallback still apply. This exposes nearby validation, defaults, and side effects without changing the ten-candidate retrieval ranking.

A completeness review includes the validated draft only when it fits the reserved input budget. Otherwise the first valid answer is retained and the trace records `completeness_review: skipped_context_budget`. Completed reviews are marked `completed`; attempt `purpose` distinguishes initial generation, repair, and completeness. Review is a model-assisted revision, not an independent correctness guarantee or human benchmark assessment.

From `backend/`, after the database is started and migrations are applied:

```sh
uv run repo-copilot ask https://github.com/pypa/sampleproject \
  "Where is authentication implemented?" --ref YOUR_PINNED_COMMIT_SHA \
  --trace ../.data/answer-trace.json
```

Set `COPILOT_MODEL_ID` to a model that supports Responses structured outputs and `COPILOT_PROVIDER_API_KEY` in your private `.env`. Hybrid mode also requires the embedding settings documented in the [retrieval guide](../retrieval/README.md). This command ingests, parses, and embeds as needed, reusing existing snapshots and embedding caches. It makes paid provider calls. `--mode lexical` skips embeddings; generation still requires a provider. `--json` prints the full result. Ref defaults to HEAD, but every result records the resolved immutable commit.

Each factual claim contains evidence IDs. The application validates their run, snapshot, file ID, inclusive line bounds, full-file hash and excerpt hash, then creates commit-pinned GitHub links. Truncated file reads are excluded. Link paths are URL-encoded. Source code is treated as untrusted data and is never executed. These checks establish citation provenance, not whether a claim is logically supported; that needs Step 7 evaluation.

The Responses schema restricts claim evidence IDs to an enum built from that request's supplied evidence. Server validation remains mandatory. Failed attempts record a bounded `validation_error` code and safe message (invalid JSON, structure, unknown ID, provenance, content, or empty answer); a repair receives that feedback without receiving the invalid generated text. The v1 traces did not record these categories, so the original failure cause cannot be recovered retroactively.

An empty evidence set returns explicit uncertainty without a generation call. Failed retrieval cannot establish that a feature is absent. The prompt requires uncertainty about incomplete coverage and separates inference from observed facts.

The context budget uses a conservative UTF-8 byte upper bound with room reserved for instructions, schema and framing. `COPILOT_MAX_OUTPUT_TOKENS` applies per generation attempt (at most two). `COPILOT_RUN_TIMEOUT_SECONDS` bounds the answering phase; ingestion and indexing have their own budgets. Evidence reads are limited by `COPILOT_MAX_TOOL_CALLS` minus one retrieval operation. The API adapter does not retry transport failures, and rejects incomplete or refused responses.

`--trace` saves retrieved context, selected evidence, parsing run, model IDs, per-attempt usage, embedding query usage, elapsed time and terminal status. Index-building usage is outside the answering trace. No provider settings, credentials, or invalid generated prose are included. Traces contain source and questions; choose a private output path. Conversation and evidence database persistence comes in Step 11; this prototype uses a run-local registry and optional JSON trace. Failures before answering (for example acquisition/configuration errors) use the CLI error path rather than an answer trace.

API implementation reference: [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

Tests use mocked HTTP and deterministic generation. PostgreSQL integration tests exercise hybrid retrieval, valid answers, successful repair, exhausted repair and insufficient evidence. Live quality measurements and their review status are recorded separately in evaluation reports; passing infrastructure tests does not establish answer quality.
