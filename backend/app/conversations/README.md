# Conversations and resumable answer streams

Step 11 adds durable conversations, user/assistant messages, answer runs, cited evidence, and ordered public events. A conversation pins the snapshot **and** its published parsing run and embedding profile. New indexes do not move old conversations or citations.

## Start the services

From `backend/`, after starting PostgreSQL and configuring providers in the root `.env`:

```sh
uv sync --locked
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In separate terminals, run `uv run repo-copilot-worker` for indexing and `uv run repo-copilot-answer-worker` for answers. The answer worker accepts `--once` for a single queue attempt. It uses the existing fixed pipeline by default; message requests can choose `"pipeline":"agent"`. The original synchronous `/snapshots/{id}/ask` endpoint and CLI still work.

## HTTP workflow

1. `POST /snapshots/{snapshot_id}/conversations` with `{"title":"Authentication"}` (or `{}`). Returns a pinned conversation. The snapshot must have a published ready index.
2. `POST /conversations/{conversation_id}/messages` with `{"question":"Where is authentication checked?","pipeline":"fixed"}` and an `Idempotency-Key` header. Returns HTTP 202 with `run_id`, `status`, and `events_url`; it makes no provider calls.
3. `GET /runs/{run_id}/events` observes the persisted run independently of submission. For example: `curl -N http://127.0.0.1:8000/runs/RUN_ID/events`.
4. Reconnect to that same URL with `Last-Event-ID: 12` (or `?after=12`) to receive only events after sequence 12. The header takes precedence. Never resubmit a message to reconnect.
5. `GET /conversations/{conversation_id}` restores messages and their runs. Messages retain the final structured answer, snapshot, commit, and citation links. `GET /snapshots/{snapshot_id}/conversations` lists conversations. Both support `limit` and `offset` pagination.
6. `GET /runs/{run_id}` returns run status, configured model/limits, available usage, and safe errors. `POST /runs/{run_id}/cancel` cancels a queued or running answer; repeating it is safe.

Keys are scoped to a conversation, contain 1–128 printable ASCII characters without spaces, and must be retained by clients for retries. The same key and request return the original run even after completion, failure, or cancellation. Reusing a key with different text or pipeline returns 409. Only one run can be active per conversation; another submission returns 409 until the run ends. A deliberate retry after failure uses a new key.

## Event contract

Each SSE event has an integer `id`, an `event` name, and JSON `data`. IDs start at 1, increase within the run, and are committed before emission. Consumers should deduplicate using `(run_id, id)` and close the stream after `done`.

| Event | Public payload |
| --- | --- |
| `status` | Queue/run state or generation phase; no hidden reasoning |
| `tool_started` | Tool name |
| `tool_finished` | Tool name and concise outcome; agent calls also report duration/bytes |
| `answer_delta` | Text chunk with `provisional: true` |
| `citation` | Server-validated evidence ID, immutable source span/hash, and GitHub commit link |
| `error` | Safe error code, without provider exceptions or source content |
| `done` | Terminal status and `validated`; successful runs include the authoritative structured answer, citations, and message ID |

Tool events are live. The structured model adapters currently buffer generation, so answer deltas are emitted in 512-character chunks **after generation and validation**, not as provider tokens arrive. They remain provisional until the final message transaction commits. On completed `done`, replace the provisional display with its authoritative structured answer. On any other terminal status, discard provisional prose. A started tool can remain unfinished when cancellation or a failure interrupts it; `done` terminates that activity.

Cited evidence, the assistant message, citation events, and successful `done` commit atomically. Failed validation publishes no assistant answer or citations. SSE reconnects replay durable events, not model work. An already-terminal run whose entire stream has been consumed returns HTTP 204, which tells EventSource to stop reconnecting. Cursors beyond the latest persisted sequence are rejected. Idle streams send heartbeat comments every 15 seconds.

## Worker lifecycle and limits

Workers claim queued runs using PostgreSQL row locks with `SKIP LOCKED`. A fixed deadline of the configured run timeout plus 15 seconds protects publication. Expired running runs become `interrupted` with terminal `error`/`done` events when a worker polls or an API observer checks runs. No automatic provider retry is made after worker loss; queued runs remain available to another worker. Run state and a unique ownership token fence late results.

Cancellation commits a terminal state immediately. A live worker checks ownership while awaiting providers, cancels pending async work, and checks ownership again at event/final publication. Synchronous database work may finish before cancellation is observed; a provider may still bill a request already received. Browser disconnects only stop observation and do not cancel execution.

Up to eight completed prior turns, bounded to 4 KB and a quarter of the configured context budget, are passed as untrusted conversation context. Failed/cancelled turns are excluded. Previous claims are not source evidence; every answer needs fresh validated citations. Fixed retrieval uses the current question, so very vague follow-ups may still need explicit identifiers. Worker provider configuration must match the conversation's pinned embedding profile; mismatches terminate safely.

This remains a local service without accounts. Shared-hosting authorization, retention policy, and the Next.js UI belong to later steps. These deterministic tests do not establish live answer-quality acceptance.

## Validation

`tests/test_conversations_storage.py` covers both pipelines, idempotency races, worker claim contention, ordered replay, live stream closure, follow-up context, cancellation, stale-worker fencing, safe failures, invalid citations, and immutable snapshot links. Run with `COPILOT_TEST_DATABASE_URL` set to a disposable/test-capable PostgreSQL database; tests create isolated schemas and remove them afterward. HTTP input validation is also covered without PostgreSQL.
