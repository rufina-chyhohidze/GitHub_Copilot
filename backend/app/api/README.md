# Repository API

Step 9 exposes repository registration, persisted indexing jobs, snapshot browsing, and Q&A pipelines through FastAPI. The API submits jobs; a separate [worker](../jobs/README.md) performs indexing. This is the local, single-user prototype. Shared-hosting authorization, conversations and streaming remain later steps.

## Start locally

Configure `.env` as in the [backend guide](../../README.md). From the repository root start PostgreSQL with `docker compose up -d --wait db`, then from `backend/`:

```sh
uv sync --locked
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal, also from `backend/`:

```sh
uv run repo-copilot-worker
```

Interactive API documentation is at `http://127.0.0.1:8000/docs`; OpenAPI JSON is at `/openapi.json`. `GET /health` checks database connectivity. `uv run repo-copilot smoke --database` additionally verifies the migration head and pgvector extension.

## Submit, wait, and browse

```sh
curl -sS http://127.0.0.1:8000/repositories \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://github.com/pypa/sampleproject","ref":"621e4974ca25ce531773def586ba3ed8e736b3fc"}'
```

The `202` response contains `repository_id`, `job_id`, `status`, and `job_url`. It does not wait for a clone or call a model. Embedding configuration is checked before accepting a job; the worker requires its own provider credential. Indexing and answers may incur provider charges.

Use the returned IDs in these requests:

```sh
curl -sS http://127.0.0.1:8000/index-jobs/JOB_ID
curl -sS http://127.0.0.1:8000/repositories/REPOSITORY_ID
curl -sS 'http://127.0.0.1:8000/repositories/REPOSITORY_ID/snapshots?limit=50&offset=0'
curl -sS 'http://127.0.0.1:8000/snapshots/SNAPSHOT_ID/tree?limit=100&offset=0'
curl -sS 'http://127.0.0.1:8000/snapshots/SNAPSHOT_ID/files?path=README.md&start_line=1'
curl -sS http://127.0.0.1:8000/snapshots/SNAPSHOT_ID/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"What does the sample command do?"}'
```

`GET /index-jobs/{id}` exposes stages, progress milestones, attempts, retry availability, heartbeat/lease expiry, source IDs, and safe failure information. It omits private configuration and the lease token. On success, use its `snapshot_id`. A failed replacement job leaves older ready snapshots usable.

## Endpoints

| Method and path | Behavior |
| --- | --- |
| `GET /health` | Database connectivity |
| `POST /repositories` | Validate a public GitHub URL and optional ref; queue indexing |
| `GET /repositories/{id}` | Repository identity and latest/latest-ready snapshot IDs |
| `POST /repositories/{id}/index` | Queue another optional ref; JSON body `{}` defaults to HEAD |
| `GET /index-jobs/{id}` | Persisted progress and outcome |
| `GET /repositories/{id}/snapshots` | Paginated snapshot metadata and coverage |
| `GET /snapshots/{id}` | Snapshot commit, status, and ingestion coverage |
| `GET /snapshots/{id}/tree` | Paginated immediate children, optionally under `path` |
| `GET /snapshots/{id}/files` | Stored source with bounded inclusive line ranges |
| `POST /snapshots/{id}/ask` | A bounded answer with validated citations; optional `pipeline: agent` |

Snapshot lists default to 50 items (maximum 100), and tree lists default to 100 (maximum 500). Both use `offset` and return `next_offset`, or null at the end. Metadata pages are ordered deterministically; newly added snapshots can shift offsets. File reads retain the tool's 200-line/16-KiB limits and report truncation. Paths remain snapshot-scoped and cannot escape stored source. Source is never read from a live checkout.

Tree, file, and Q&A routes require a published ready index. Q&A also requires a publication for the configured embedding profile; it always uses that publication's parsing-run ID, even when a newer unindexed run exists. Unready snapshots return `409` before generation. `/ask` is synchronous and returns only after validation; it does not yet persist conversations or stream output. Its existing time, context, output, and tool limits still apply.

## Errors and validation

Errors use `{"error":{"code":"...","message":"..."}}`. Invalid request fields return `422` with field locations/types but without echoing submitted values. Unknown resources return `404`, unready snapshots `409`, invalid source ranges/tool limits `400`, provider configuration or database availability problems `503`, and controlled answer-generation failure `502`. Unexpected errors return a generic `500`. Database/provider internals and credentials are not returned.

Only canonical public GitHub HTTPS URLs and bounded plain refs are accepted. Extra JSON fields, malformed UUIDs, out-of-range pagination, and oversized/empty questions are rejected. For current scope use the loopback startup command above; user ownership and authorization are required before shared deployment.

Integration tests exercise submission → worker → ready index → file → cited answer with a fake Git source and deterministic providers, plus failed/unready paths, pagination, errors, and explicit selection of the published parsing run. This validates infrastructure, not live answer quality. Fresh live measurements are recorded in the [Step 10 comparison](../../../evals/reports/step10-comparison.md); semantic quality review remains separate.

Implementation references: [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/) and [testing](https://fastapi.tiangolo.com/tutorial/testing/).

Step 10 accepts `"pipeline":"agent"` in the question body; omitted or `"fixed"` keeps the baseline. Both use the same published source identity and citation validation. See the [agent guide](../agents/README.md) for additional budgets and trace behavior.
