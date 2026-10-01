# Backend

Steps 1–10 provide the backend scaffold, evaluation datasets, ingestion, Python/JS/TS parsing, repository tools, hybrid retrieval, cited Q&A, review workflows, a FastAPI service, a recoverable indexing worker, and an opt-in bounded agent. The saved v4 run has passed human review; fresh development measurements are recorded in the [Step 10 comparison](../evals/reports/step10-comparison.md), with new answer review pending. Live semantic search and answer generation require provider configuration.

## Run locally

Install Python 3.12–3.14, uv, and Docker Desktop (or Docker Engine with Compose). Run the following from the repository root:

```sh
cp .env.example .env
docker compose up -d --wait db
cd backend
uv sync --locked
uv run alembic upgrade head
uv run repo-copilot smoke --database
```

The database uses localhost port 5433 to avoid the common default PostgreSQL port. If you change its credentials or port, update both the `POSTGRES_*` values and `COPILOT_DATABASE_URL` in the root `.env`.

For a smoke check without Docker or provider credentials:

```sh
cd backend
uv sync --locked
uv run repo-copilot smoke
```

`smoke --provider` checks that provider settings exist; it does not call a model or verify credentials remotely. The embedding and answer adapters enforce configured input, output, and time budgets when making provider calls.

## Check changes

Step 2 adds `uv run repo-copilot-eval` to check the local benchmark without Docker or a model. See the [dataset guide](../evals/datasets/README.md) for the rubric and optional public-source verification.

Step 3 adds `uv run repo-copilot ingest URL --ref COMMIT_OR_BRANCH`. Apply the latest migration first; see the [ingestion guide](app/ingestion/README.md) for an example, limits, and source-storage behavior. No model API key is needed.

Step 4 adds `uv run repo-copilot parse SNAPSHOT_ID`. The [parsing guide](app/ingestion/PARSING.md) explains symbols, chunk sizes, fallback behavior, and versioned results.

Step 5 adds `tree`, `read`, `search-code`, `symbols`, `find-symbol`, `embed`, and `search`. See the [retrieval guide](app/retrieval/README.md) for local commands, optional OpenAI setup, short component explanations, and baseline evaluation.

Step 6 adds `ask URL QUESTION --ref COMMIT_OR_BRANCH`, with validated source citations, bounded repair or completeness review, and optional JSON traces. See the [answering guide](app/answering/README.md) for provider setup, usage, and limitations.

Step 7 adds `repo-copilot-answer-eval` for baseline runs, usage/cost reporting, citation rechecks, and explicit rubric review. See the [evaluation workflow](app/evaluation/README.md) and [saved live development results](../evals/reports/development-live-v4.md). The saved v4 run passed human review on 2026-10-01; new Step 10 answers still need their own quality review. AI assessments cannot grant acceptance.

Step 8 extends `parse` to JavaScript, JSX, TypeScript, and TSX using Tree-sitter. `symbols` now includes imports, exports, and extraction limitations. Run `uv sync --locked` to install the grammar packages, then reparse a snapshot to create a new versioned run. See the [parsing guide](app/ingestion/PARSING.md) for supported syntax, limitations, and the web-language fixture validation command.

Step 9 adds the [HTTP API](app/api/README.md) and [indexing worker](app/jobs/README.md). After `uv sync --locked` and `uv run alembic upgrade head`, run `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000` and `uv run repo-copilot-worker` in separate terminals. Open `http://127.0.0.1:8000/docs` for interactive endpoints. Repository submission returns an indexing job; file browsing and HTTP Q&A require a published ready index. Existing CLI snapshots can be indexed through a worker job without overwriting their source.

Step 10 adds `--pipeline agent` to `repo-copilot ask` and `"pipeline":"agent"` to HTTP Q&A. The fixed pipeline remains the default. See the [agent guide](app/agents/README.md) for tools, limits, traces and the [evaluation guide](app/evaluation/README.md) for matched comparisons.

From `backend/`:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The tests check citation locations, configuration, dataset integrity, and ingestion boundaries. PostgreSQL storage tests are skipped unless `COPILOT_TEST_DATABASE_URL` is set; the ingestion guide shows how to run them. Database startup and migration verification use `smoke --database`.

## Why these pieces exist

| Piece | Purpose |
| --- | --- |
| `pyproject.toml` and `uv.lock` | Declare dependencies and lock exact versions so installations are reproducible. |
| `app/config.py` | Validate environment settings in one place and keep credentials out of displayed errors. |
| `app/logging.py` | Emit structured JSON events that can later feed tracing and debugging tools. Never log raw settings or credentials. |
| `app/models/contracts.py` | Define shared input/output shapes so ingestion, retrieval, and answering agree on snapshot and source identity. |
| `app/providers/` | Define small model interfaces and bounded OpenAI adapters for embeddings and structured answers. |
| `app/db/` | Centralize database connections and Alembic migrations so schema changes are reproducible. |
| `app/cli.py` | Provide deterministic local commands and diagnostic checks. |
| `app/main.py` | Serve validated repository, job, snapshot, and bounded Q&A endpoints. |
| `app/jobs/` | Claim and recover persisted jobs, renew leases, and publish completed indexes. |
| `tests/` | Catch boundary failures such as invalid line ranges before they reach citations. |

Other folders contain short descriptions of their future responsibilities. Their existence does not mean those features are implemented.

The migrations enable pgvector and add source snapshots, parsing runs, chunks, versioned embedding caches, leased jobs, and ready-index publications. Downgrading a data-table migration deletes its tables, so use forward migrations for normal development; the initial migration intentionally retains the shared vector extension on downgrade.

## Troubleshooting

- If Docker cannot connect, start Docker Desktop and retry `docker compose up -d --wait db` from the root.
- If the database smoke check fails, verify `.env`, database health, and `uv run alembic upgrade head`.
- Docker stores database files in a named volume. `docker compose stop` stops the service without deleting the data.
- Citation validation checks stored files, snapshot identity, line bounds, and content hashes. Whether the cited evidence supports each claim still requires answer-quality evaluation.

Setup references: [uv dependency locking](https://docs.astral.sh/uv/concepts/projects/sync/), [Alembic migrations](https://alembic.sqlalchemy.org/en/latest/tutorial.html), and [pgvector](https://github.com/pgvector/pgvector).
