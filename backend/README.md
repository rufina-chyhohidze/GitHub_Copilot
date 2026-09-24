# Backend

Steps 1–3 provide the backend scaffold, evaluation dataset, and public-repository ingestion into immutable source snapshots. Parsing, search, model calls, and HTTP endpoints will be implemented in later steps.

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

`smoke --provider` checks that provider settings exist; it does not call a model or verify credentials remotely. The provider interfaces define limits now, while enforcing those limits on real network calls belongs to the future adapters.

## Check changes

Step 2 adds `uv run repo-copilot-eval` to check the local benchmark without Docker or a model. See the [dataset guide](../evals/datasets/README.md) for the rubric and optional public-source verification.

Step 3 adds `uv run repo-copilot ingest URL --ref COMMIT_OR_BRANCH`. Apply the latest migration first; see the [ingestion guide](app/ingestion/README.md) for an example, limits, and source-storage behavior. No model API key is needed.

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
| `app/providers/` | Define small model interfaces without selecting a provider or making paid calls yet. |
| `app/db/` | Centralize database connections and Alembic migrations so schema changes are reproducible. |
| `app/cli.py` | Provide a quick executable check before adding the HTTP API. |
| `tests/` | Catch boundary failures such as invalid line ranges before they reach citations. |

Other folders contain short descriptions of their future responsibilities. Their existence does not mean those features are implemented.

The first migration enables pgvector; the second adds repositories, snapshots, and source files. Vector columns arrive once embedding dimensions are selected. Downgrading the second migration deletes those source tables, so use forward migrations for normal development; the initial migration intentionally retains the shared vector extension on downgrade.

## Troubleshooting

- If Docker cannot connect, start Docker Desktop and retry `docker compose up -d --wait db` from the root.
- If the database smoke check fails, verify `.env`, database health, and `uv run alembic upgrade head`.
- Docker stores database files in a named volume. `docker compose stop` stops the service without deleting the data.
- Source spans validate path shape and line ordering; checking that a file exists, that lines are in bounds, and that evidence supports an answer comes in later stages.

Setup references: [uv dependency locking](https://docs.astral.sh/uv/concepts/projects/sync/), [Alembic migrations](https://alembic.sqlalchemy.org/en/latest/tutorial.html), and [pgvector](https://github.com/pgvector/pgvector).
