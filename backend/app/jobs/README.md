# Persisted indexing jobs

Step 9 runs repository acquisition, parsing, embedding, and publication in a separate worker. HTTP submission stores a PostgreSQL job and returns immediately. The worker calls the same ingestion/parsing/indexing services as the CLI; it never executes repository code.

## Run the worker

From `backend/`, after configuring the database and embedding provider in the root `.env`:

```sh
uv sync --locked
uv run alembic upgrade head
uv run repo-copilot-worker
```

`uv run repo-copilot-worker --once` attempts one available job and exits, or exits immediately if there is no work. A processed job can still be queued for retry or terminally failed: inspect `GET /index-jobs/{id}` for its outcome. Start the API in another terminal using the [API guide](../api/README.md). Indexing makes paid embedding calls when compatible vectors are not already cached. Tests use deterministic providers.

## Job lifecycle

`index_jobs` stores repository/ref, resolved commit, snapshot/parsing-run IDs, nonsecret indexing configuration, stage, progress, attempt count, lease ownership, availability time, safe error, and timestamps. Stages are `queued`, `ingesting`, `parsing`, `embedding`, `publishing`, and `complete`. Progress is a stage milestone, not a count of bytes or files; retries may revisit an earlier milestone.

- Repeated submissions for the same repository/ref/configuration reuse an active job. A submission after completion creates a new job so a moving branch can be checked again.
- Workers claim available jobs atomically with `FOR UPDATE SKIP LOCKED`. A lease token fences heartbeats, failure updates, and final publication. Database time determines expiry.
- A separate heartbeat thread renews the lease every one-third of its duration, including during synchronous acquisition/parsing. A failed renewal prevents later checkpoints and publication. A running stage may finish bounded work before observing the lost lease.
- A PostgreSQL session advisory lock serializes worker jobs for the same repository across stage transactions. If that lock is busy, the job is deferred without spending an attempt. Session/process death releases the lock.
- The worker persists the resolved SHA before scanning. After that checkpoint, retries use that commit rather than a moving branch. Saved snapshots and parsing runs are reused; completed embedding batches remain cached.
- Expected stage failures retry with bounded exponential delay. Configuration/version mismatches fail immediately. Expired final attempts become terminal failures on the next worker poll. Terminal jobs can be retried by submitting a new indexing request.
- Generic failure codes and safe messages are persisted; provider bodies, credentials, and arbitrary exception text are excluded. The API exposes the failing stage so operators can inspect configuration and limits.

The settings captured at submission include ingestion/parsing/embedding limits and model identity, but never the API key or database URL. Retries use those captured settings with the worker's current credential. Parser and ingestion policy fingerprints must still match; if code changes those fingerprints, submit a new job.

## Ready snapshots and replacement failures

`ready_indexes` publishes the exact snapshot, parsing run, and embedding profile only after a complete `embedding_indexes` record exists. Publication and job success commit together after ownership is checked. Snapshot status then becomes `ready`. No database migration rewrites existing source files or old indexes.

Failures leave earlier publications intact. New parsing runs do not replace the Q&A run until they are successfully indexed and published. The API selects a published run matching the configured embedding profile and passes that run ID explicitly into answering. Existing CLI-only snapshots need a worker indexing job before the API considers them ready. Ordinary CLI parsing/search remains available for diagnostics.

The worker serializes equivalent repository jobs and reuses completed cache entries. This is not an exactly-once guarantee for external billing: a process can die after the provider accepts a request but before its vectors are stored. Independently launched CLI indexing or jobs for different repositories may also race on shared embedding inputs.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `COPILOT_JOB_LEASE_SECONDS` | 60 | Lease duration; heartbeat interval is one-third of this |
| `COPILOT_JOB_MAX_ATTEMPTS` | 3 | Attempts per submitted job, including recovered leases |
| `COPILOT_JOB_RETRY_SECONDS` | 10 | Base retry delay; also used for busy repository locks |
| `COPILOT_WORKER_POLL_SECONDS` | 2 | Idle polling interval |

Ingestion, parsing, and embedding retain their existing operation budgets. Source snapshots are immutable; job status is authoritative for an attempt, so a failed job does not mark an older ready snapshot failed. Worker events log job IDs, not source or provider content. Shutdown interruption leaves unfinished leases recoverable.

Tests cover concurrent claims with locked rows, active-job deduplication, lease expiry and stale-owner rejection, heartbeat renewal, simulated interruption after commit resolution, cache reuse after a failed batch, version mismatch, and preservation of ready indexes after replacement failure. PostgreSQL tests use isolated disposable schemas.

Reference: [PostgreSQL explicit and advisory locks](https://www.postgresql.org/docs/17/explicit-locking.html).
