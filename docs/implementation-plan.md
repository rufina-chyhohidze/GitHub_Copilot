# GitHub Repository Copilot: implementation plan

## Product goal and delivery boundaries

Help a developer understand an unfamiliar repository through evidence they can inspect. Every answer uses a specific indexed commit. The system should distinguish observed facts, plausible inferences, and missing evidence.

Delivery A proves repository understanding through a Python command-line workflow. Delivery B makes that workflow available through an API and repository workspace, adding a single bounded agent only after establishing a retrieval baseline.

Version 1 ends at read-only repository Q&A with public GitHub repositories, Python/JavaScript/TypeScript parsing, hybrid search, validated citations, streaming, and conversation history. It excludes code execution, automatic modifications, PR creation, private repositories, GitHub App installation, and multiple agents.

## Agreed design decisions

1. **Immutable snapshots:** model each indexed commit explicitly. Files, chunks, conversations, evidence, and runs are scoped to a snapshot. Reindexing creates or reuses another snapshot; it does not mutate the contents referenced by old answers.
2. **Hybrid retrieval immediately:** combine semantic search with exact identifier/text search in the first prototype. Add reranking only if evaluations justify it.
3. **Structured evidence:** tools return evidence IDs backed by snapshot, path, and line ranges. The model cites those IDs. The server resolves citations and validates their bounds and provenance.
4. **Early evaluation:** create the question dataset before retrieval implementation, including absent features, ambiguous identifiers, and multi-file questions. Compare the fixed pipeline and agent on the same snapshots.
5. **Honest code analysis:** declaration extraction, textual occurrences, imports, and resolved references are distinct capabilities. Do not present text matches as confirmed calls.
6. **Background ingestion:** use a separate worker with persisted PostgreSQL jobs, progress, retry state, and recovery. No Redis is required initially.
7. **Incremental language scope:** establish the parser and evidence contracts with Python, then add JavaScript/TypeScript before Delivery B completes.
8. **One agent with limits:** bound tool calls, returned bytes, elapsed time, and model usage. Introduce a custom LangGraph workflow when explicit state or branching is needed.
9. **Inspectable uncertainty:** when evidence is insufficient, explain the search scope and what remains unknown. A failed search alone does not prove a feature is absent.

## Architecture

The Next.js workspace calls FastAPI. FastAPI serves repository metadata, snapshot files, conversations, and run events. It submits ingestion work to a PostgreSQL jobs table. A separate worker clones and indexes repositories. The Q&A service queries snapshot-scoped search and file tools, then generates an answer using retrieved evidence.

PostgreSQL stores metadata, source text for supported text files, chunks, embeddings, jobs, conversations, evidence, and run events. Local checkouts are ingestion workspaces rather than the permanent source of citations. This lets old citations remain available after checkout cleanup.

Keep deterministic ingestion and retrieval usable without the agent framework. Wrap those same services as agent tools later. LangChain's agent abstraction can provide the initial loop; custom LangGraph orchestration is a later implementation choice, not a prerequisite for the prototype.

Docker Compose ultimately runs PostgreSQL/pgvector, the API, worker, and frontend. Model and embedding providers are configuration choices behind small interfaces. Record model IDs, embedding dimensions, and pipeline versions; changing embedding models requires a compatible index or reindex.

## Core data model

| Entity | Important fields and relationships |
| --- | --- |
| Repository | ID, canonical GitHub URL, owner/name, default ref |
| RepositorySnapshot | Repository ID, resolved commit SHA, index version, status, timestamps, indexing coverage summary |
| RepositoryFile | Snapshot ID, normalized path, language, source text, content hash, size, line count, parse status |
| CodeChunk | File ID, symbol name/type, start/end lines, content hash, content, parser version |
| ChunkEmbedding | Chunk ID, provider/model/version, dimensions, embedding input hash, vector |
| IndexJob | Snapshot/repository ID, requested ref, stage, progress, attempts, lease expiry, error, timestamps |
| Conversation | Snapshot ID, title, timestamps |
| Message | Conversation ID, role, content, creation time |
| AnswerRun | Conversation ID, input message ID, status, model, limits, usage, error, timestamps |
| Evidence | Run ID, file ID, inclusive line range, content hash, originating tool |
| RunEvent | Run ID, monotonically increasing sequence, type, payload, timestamp |

Use uniqueness constraints on snapshot identity (repository, SHA, index version), file path within a snapshot, and event sequence within a run. All search queries enforce snapshot scope. Publish a snapshot as ready only once all required indexing stages complete; report skipped and unsupported files separately.

Conversations remain pinned to their original snapshot. A UI action starts a new conversation for an updated snapshot. Do not silently move existing conversations to the latest commit.

For a local prototype, accounts are unnecessary. Before a shared deployment, add user ownership and authorization for repositories, conversations, runs, and events; public source code does not make a user's chat history public.

## Delivery A: prove repository understanding

### Step 1 — Scaffold the backend and define contracts

Status: implemented. See [backend setup](../backend/README.md) for commands and short explanations. Verified dependency installation, lint/format checks, 15 tests, Docker database startup, the initial migration, and the database smoke check.

- Create `backend/pyproject.toml`, `app/`, `tests/`, and CLI entrypoints.
- Configure formatting, linting, test execution, environment variables, and structured logging.
- Add PostgreSQL/pgvector to Docker Compose and database migrations.
- Define typed contracts for snapshots, parsed symbols, chunks, search hits, evidence, and answers.
- Define model and embedding interfaces, configuration validation, and explicit usage limits.
- Add `.env.example` with placeholders and ignore secrets, local checkouts, and build artifacts.

Completion: a clean checkout can start the database, apply migrations, and run a CLI smoke command using documented commands. Missing configuration produces actionable errors.

### Step 2 — Create fixtures and the evaluation dataset

Status: implemented. The [evaluation guide](../evals/datasets/README.md) documents 20 questions (14 development, 6 held-out), a versioned local fixture, two pinned public repositories, source verification, and the review rubric. All 20 cases were verified against their source; the backend suite passes 30 tests. This validates benchmark integrity, not model answer quality.

- Create a small, controlled Python repository fixture with authentication, routes, services, models, and tests.
- Include repeated symbol names, an indirect call, a missing feature, and source comments containing instructions the assistant must ignore.
- Select a few small public repositories and pin their commits for realistic evaluation.
- Write approximately 20 questions with expected files, supporting spans where practical, required facts, and acceptable uncertainty.
- Separate a development set from a held-out set. Record fixture and dataset versions.
- Implement the evaluation input/output format before implementing answer generation.

Completion: cases cover direct lookup, semantic lookup, multi-file explanation, ambiguity, and insufficient evidence. Human reviewers can assess an answer without guessing what “correct” means.

### Step 3 — Implement safe ingestion and immutable snapshots

- Accept canonical public GitHub HTTPS repository URLs; validate owner/repository and keep optional refs separate from URLs.
- Invoke native Git with argument arrays, timeouts, noninteractive credentials, and no shell interpolation. Do not fetch submodules or LFS objects initially.
- Resolve the requested ref to a commit and persist that SHA. Use a bounded temporary workspace for checkout.
- Enforce configurable clone, scan, file-size, file-count, and processing-time limits.
- Exclude `.git`, dependencies, build output, binaries, and generated files from normal indexing. Record exclusion reasons and coverage.
- Keep dependency manifests and configuration. Exclude lockfiles from embeddings initially; report that exclusion rather than silently implying full coverage.
- Reject symlinks for indexed source files and ensure normalized paths remain within the workspace.
- Store source text, hashes, language, and line counts. Define decoding and newline rules so citations match the displayed source.
- Never install dependencies or execute repository scripts. Treat all repository content as untrusted data.

Completion: ingestion produces a reproducible manifest for a pinned commit. Invalid URLs, oversized input, path escapes, decoding failures, and partial clones produce controlled outcomes. Repeated ingestion is idempotent.

### Step 4 — Parse Python and generate code-aware chunks

- Use Python AST to extract classes, functions, methods, declarations, and import syntax.
- Preserve exact line spans, decorators, qualified names, and enclosing symbol metadata.
- Chunk methods/functions separately. Use compact class context rather than duplicating every entire class alongside all its methods.
- Split oversized symbols at sensible boundaries within the configured token limit, preserving line spans and parent-symbol identity.
- Index README/configuration text using a documented text fallback. Record parse failures and fallback usage.
- Store parser and chunker versions so future changes can trigger appropriate rebuilding.

Completion: fixtures verify source spans, nested definitions, decorated methods, oversized symbols, and malformed files. Parsing does not claim to resolve cross-file calls.

### Step 5 — Build deterministic tools and hybrid retrieval

- Implement snapshot-scoped `get_repository_tree`, `read_file`, `search_code`, `find_symbol`, and `get_file_symbols` as ordinary services.
- Make literal search the default; define case sensitivity and bound result size. Do not expose arbitrary shell commands as tools.
- Generate embeddings in batches with retries and cache keys including embedding input and model version.
- Store vectors in pgvector. Start with exact vector similarity for small fixtures; add an approximate index when measured size/latency warrants it.
- Retrieve lexical and semantic candidate sets, fuse their ranks, deduplicate overlapping spans, and expand selected hits into bounded source context.
- Preserve imports, enclosing definitions, paths, and line ranges when assembling context.

Completion: exact identifiers and natural-language questions both retrieve expected evidence. Search cannot return another snapshot's content. Capture retrieval metrics before answer generation is introduced.

### Step 6 — Generate answers with validated citations

- Build a fixed pipeline: question → hybrid retrieval → bounded file reads → evidence registry → structured answer.
- Give the model only evidence IDs it may cite, and require explicit uncertainty where evidence is missing.
- Validate cited IDs belong to the run and resolve to stored snapshot files with valid inclusive line bounds and matching content hashes.
- On invalid structured output or citations, attempt a bounded repair; otherwise return a controlled failure instead of publishing fabricated citations.
- Render CLI citations and immutable GitHub links using the snapshot SHA and line range.
- Record retrieved context, timings, model usage, and answer status without logging credentials.

Completion: a CLI command accepts a repository URL/ref and question, then prints an answer with valid citations. Citation validity is checked mechanically; whether evidence supports each claim is evaluated separately.

### Step 7 — Evaluate and establish the baseline

- Run the dataset against pinned snapshots and save versioned results.
- Measure relevant-file recall at a fixed candidate limit, citation support, required-fact coverage, and uncertainty handling.
- Record indexing duration, Q&A latency, token usage, and estimated provider cost using configurable pricing.
- Review failures by stage: scanning, parsing, retrieval, context assembly, or generation.
- Improve the failing stage before expanding scope.

Initial acceptance targets, to calibrate once against the baseline and then hold fixed for comparison:

- 100% of emitted citations resolve to valid source ranges in the correct snapshot.
- At least 90% relevant-file recall at 10 candidates on the labeled set.
- At least 90% of reviewed cited factual claims are supported by their evidence.
- At least 80% of answer cases satisfy their required-fact rubric.
- All deliberately absent/insufficient-evidence cases avoid an unsupported definitive answer.

Completion: publish a baseline report with per-case results and known limitations. Delivery A is complete when the CLI works and the agreed quality gates pass; a successful demo question alone is insufficient.

## Delivery B: ship the repository workspace

### Step 8 — Add JavaScript and TypeScript parsing

- Add Tree-sitter adapters for JS, JSX, TS, and TSX through the same parser contract.
- Extract declarations, methods, common arrow-function assignments, imports, exports, and source spans.
- Add language-specific fixtures and evaluation cases, including default exports and repeated names.
- Expose import syntax separately from resolved module targets; mark unsupported resolution explicitly.

Completion: supported syntax yields correct symbols and citations; unsupported constructs degrade to searchable text with visible coverage limitations. The existing Python suite still passes.

### Step 9 — Add persisted indexing jobs and FastAPI

- Extract the CLI ingestion stages into an independently runnable worker.
- Claim jobs atomically with PostgreSQL locking; persist stage progress, leases, heartbeat, bounded retries, and terminal errors.
- Recover jobs after worker termination. Keep ingestion idempotent across retries and avoid duplicate embeddings.
- Return an asynchronous job response from ingestion endpoints. Serve only ready snapshots to Q&A.
- Add API validation, pagination, consistent error responses, and snapshot/file endpoints.
- Verify that a failed replacement index leaves earlier ready snapshots usable.

Completion: users can submit a repository, observe progress, recover from worker failure, and browse the completed snapshot without keeping a request open during indexing.

### Step 10 — Introduce the bounded repository agent

- Wrap the existing services as LangChain tools, including semantic search and structured evidence-producing reads.
- Start with a single agent loop. Add `get_imports` as syntactic evidence and label text-based reference searches as candidate occurrences.
- Enforce tool-call, context-size, elapsed-time, and usage budgets outside the model's discretion.
- Keep citation validation as a deterministic final stage.
- Preserve the fixed pipeline as an evaluation baseline and diagnostic path.
- Evaluate whether extra tool calls improve multi-file answers enough to justify their latency and cost.
- Add LangSmith optionally for trace inspection; local structured traces remain available.

Completion: the agent answers tested multi-file questions with auditable tool calls, handles tool failures and exhausted budgets, and does not obey instructions found inside source files. Document its measured tradeoff against the baseline.

### Step 11 — Add conversations and resumable run streaming

- Persist conversations, messages, answer runs, evidence, and ordered events.
- Have message submission create a run and return its ID; use a separate SSE endpoint to observe that run.
- Emit `status`, `tool_started`, `tool_finished`, `answer_delta`, `citation`, `error`, and `done` events with sequence IDs.
- Persist events before emitting them. Support reconnect/replay using the last received event ID and avoid duplicate message submission with an idempotency key.
- Show activity as tool names and concise outcomes, without exposing hidden model reasoning.
- Treat streamed prose as provisional until final validation. Make final answer/citation status explicit.
- Define cancellation and interrupted-run behavior; a disconnected browser must not create a duplicate run.

Completion: disconnecting and reconnecting restores a coherent run, failed runs have terminal events, and final messages retain their snapshot and evidence links.

### Step 12 — Build the Next.js repository workspace

- Add repository submission, indexing progress, actionable failures, and an indexed-commit indicator.
- Build the file tree, code viewer, chat, source citations, and collapsible agent activity panel.
- Make citation clicks load the stored snapshot file and highlight the referenced lines.
- Display indexing exclusions and uncertainty when relevant to an answer.
- Implement conversation history and an explicit action to start a conversation on a newer snapshot.
- Add accessible navigation, loading states, empty states, and a layout usable on smaller screens.

Completion: a user can submit a URL, wait for indexing, ask a question, inspect its evidence, reload the page, and recover the conversation. A newer repository commit does not change old citation contents.

### Step 13 — Validate and package version 1

- Add integration coverage for URL → index job → ready snapshot → question → answer → citation file.
- Verify snapshot isolation, worker restart, stream replay, indexing failures, and resource limits.
- Run the expanded evaluation dataset for Python/JS/TS and publish quality, latency, and usage results.
- Document Docker Compose startup, provider setup, migrations, operational limits, and troubleshooting.
- Before shared hosting, implement authentication/ownership checks, rate limits, and retention/cleanup rules that preserve referenced snapshots.
- Provide a reproducible demo using a pinned public repository.

Completion: a fresh checkout can run the documented workflow, evaluation gates pass, and limitations are visible. Version 1 is complete at this point.

## API outline

| Endpoint | Purpose |
| --- | --- |
| `POST /repositories` | Register a URL and request initial indexing; return repository/job IDs |
| `GET /repositories/{id}` | Repository metadata and snapshot summary |
| `POST /repositories/{id}/index` | Request indexing of an optional ref |
| `GET /index-jobs/{id}` | Progress, coverage, and errors |
| `GET /repositories/{id}/snapshots` | Available indexed versions |
| `GET /snapshots/{id}/tree` | Snapshot-scoped file tree |
| `GET /snapshots/{id}/files?path=...` | Stored source and optional bounded line range |
| `POST /snapshots/{id}/conversations` | Start a pinned conversation |
| `GET /conversations/{id}` | Conversation and persisted messages |
| `POST /conversations/{id}/messages` | Submit a question and create an answer run |
| `GET /runs/{id}/events` | SSE stream with replay |
| `POST /runs/{id}/cancel` | Request run cancellation |

## Planned repository structure

```text
backend/
  app/
    api/          # Repository, snapshot, job, conversation, run endpoints
    ingestion/    # Clone, scan, language adapters, chunking, embeddings
    retrieval/    # Lexical search, semantic search, fusion, context assembly
    tools/        # Snapshot-scoped tree, file, symbol, and search services
    answering/    # Fixed pipeline, evidence registry, citation validation
    agents/       # Tool wrappers, prompts, bounded agent loop
    jobs/         # Worker, leases, retries, progress
    models/       # Persistence models and typed contracts
    db/           # Sessions and migrations
    evaluation/   # Dataset loader, metrics, reports
    cli.py
    main.py
  tests/
    fixtures/
  pyproject.toml
  Dockerfile
frontend/
  app/
  components/
  lib/
evals/
  datasets/
  reports/
docs/
compose.yaml
```

Create modules as their phases require them; this is a destination layout rather than a requirement to scaffold every directory immediately.

## Advanced roadmap after version 1

1. **Architecture explanations:** inspect entrypoints, manifests, routes, models, configuration, and imports. Attach evidence to architectural claims and label inferred connections.
2. **Flow tracing:** add language-specific reference/module resolution where practical. Model edges as confirmed, candidate, or unresolved; do not imply static analysis can establish every runtime path.
3. **Change-impact analysis:** combine resolved dependencies, schemas, tests, and retrieval to propose affected files with evidence and uncertainty. Present it as likely impact, not a complete guarantee.
4. **Incremental indexing:** compare snapshots by file hash, reuse compatible embeddings, handle deleted files, and retain immutable old snapshots. Rebuild derived dependency data affected by changed files.
5. **Implementation planning:** generate an evidence-backed change plan without modifying source.
6. **Patch suggestions and approved PR workflows:** introduce isolated workspaces, validation, explicit approval boundaries, and private-repository authentication as a separately scoped product expansion.

No multi-agent architecture is required by this roadmap. Add orchestration complexity only when a measured requirement calls for it.
