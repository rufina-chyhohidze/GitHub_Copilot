# retrieval

Step 5 retrieves source evidence using lexical search, semantic search, or both. It returns ranked context and source locations; it does not generate an answer yet.

## Start with local search

From `backend/`, install the new dependencies and apply the migration:

```sh
uv sync --locked
uv run alembic upgrade head
uv run repo-copilot tree 6996d939-9845-4ba8-8871-36d1ca684cda
uv run repo-copilot read 6996d939-9845-4ba8-8871-36d1ca684cda src/sample/simple.py
uv run repo-copilot search-code 6996d939-9845-4ba8-8871-36d1ca684cda add_one
uv run repo-copilot find-symbol 6996d939-9845-4ba8-8871-36d1ca684cda add_one
uv run repo-copilot symbols 6996d939-9845-4ba8-8871-36d1ca684cda src/sample/simple.py
uv run repo-copilot search 6996d939-9845-4ba8-8871-36d1ca684cda "Where is add_one defined?"
```

That ID is the sample snapshot created during development; replace it with your ingestion output on a fresh database. `search` defaults to lexical mode and requires no API key. `search-code` performs a literal substring search; use `--ignore-case` if needed. All commands accept `--run-id` to pin a parsing version; otherwise symbol/retrieval operations choose the latest completed parsing run within the requested snapshot.

## Enable semantic and hybrid search

The implemented adapter uses OpenAI embeddings. Put these settings in your existing root `.env`, without replacing your database settings:

```dotenv
COPILOT_EMBEDDING_MODEL_ID=text-embedding-3-small
COPILOT_EMBEDDING_DIMENSIONS=1536
COPILOT_EMBEDDING_MODEL_VERSION=v1
COPILOT_PROVIDER_API_KEY=your-real-key
```

Keep your key private; `.env` is ignored by Git. Unlike local search, the following commands send source inputs or the query to the embedding API and incur provider usage:

```sh
uv run repo-copilot embed YOUR_SNAPSHOT_ID
uv run repo-copilot search YOUR_SNAPSHOT_ID "Where is authentication implemented?" --mode hybrid
```

`embed` batches full inputs containing path, language, symbol, and code. The adapter counts those complete inputs with `cl100k_base` before requests; the tokenizer's first use downloads its vocabulary into ignored `.data/tiktoken/`. This tokenizer setup does not call the embeddings API.

The adapter checks input limits, response ordering, dimensions, finite/nonzero vectors, and model identity. It retries transient failures at most twice and applies a total request timeout. API errors show status codes without echoing response bodies or keys. The implementation follows the [official embeddings guide](https://developers.openai.com/api/docs/guides/embeddings) and [API reference](https://developers.openai.com/api/reference/python/resources/embeddings/methods/create).

## Why these components exist

| Component | Purpose |
| --- | --- |
| `lexical.py` | Ranks literal terms and boosts exact symbol matches. Exact identifiers remain useful even without embeddings. |
| `embedding_store.py` | Caches vectors by full input hash, provider, model, dimensions, and version. Successful batches survive interruptions; an incomplete index is not searchable. |
| `semantic.py` | Uses exact pgvector cosine distance, scoped to the selected snapshot, parsing run, and embedding profile. Approximate indexes can wait for measured scale needs. |
| `context.py` | Combines rankings using reciprocal rank fusion and deduplicates overlapping source. It expands relevant code with nearby lines, imports, and enclosing-symbol metadata within a 16 KiB JSON budget. |
| `service.py` | Coordinates retrieval and reports timings, usage, selected files, and bounded context. It is independent of future answer generation and agent orchestration. |

The database adds embedding profiles, a reusable vector cache, chunk-to-vector membership, and completed-index records. Changing the model, dimensions, input format, or configured model-version namespace creates a different profile. Cache sharing never changes which chunks belong to a snapshot. Bump the model-version namespace if an upstream model alias changes and a fresh cache is required.

Whitespace-only chunks are not embedded. Repeated `embed` commands reuse a completed index; interrupted commands reuse completed cached batches. Simultaneous indexing processes may duplicate provider work before their database writes converge, so the future job worker should serialize equivalent jobs.

Embedding batches default to 32 inputs and at most 16,000 input tokens. The whole index preflight defaults to 200,000 uncached tokens and a 300-second async deadline. These are input/work limits, not a currency cap; retries can incur additional usage. Source files parsed into overly large embedding payloads must be reparsed with a smaller chunk budget.

## Measure retrieval

```sh
uv run repo-copilot-retrieval-eval --prepare
```

This explicitly ingests the two pinned public evaluation repositories and the controlled local fixture, parses them, and evaluates the 14 development questions in lexical mode. The fixture is labeled `fixture://tiny-shop` and uses a content-manifest hash as its synthetic snapshot identity, not a Git commit.

For later runs, pass `--snapshot SOURCE_ID=SNAPSHOT_ID` for each of `tiny-shop`, `sampleproject`, and `itsdangerous` to reuse prepared sources. The JSON report includes exact dataset identity, parsing runs, per-case file recall at ten retrieved chunks, timings, and usage. `--mode hybrid` additionally builds the embedding indexes and uses the configured API; `--split held_out` runs the reserved questions when deliberately requested.

The initial lexical development baseline is 82.1% macro relevant-file recall at ten chunks. This is below the later 90% retrieval target and highlights questions that need semantic search or additional investigation. It is not an answer-quality score. The live OpenAI semantic baseline has not been run; pgvector, caching, retries, and hybrid orchestration are verified with controlled test embeddings and mocked HTTP responses.
