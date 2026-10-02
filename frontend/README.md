# Repository workspace

Step 12 provides a Next.js/TypeScript workspace for public GitHub repositories: indexing progress, file browsing, stored source, cited chat, saved conversations, and resumable answer activity. The homepage puts a pausable workflow preview on the left, the repository link form on the right, and three short instructions below. The UI uses Lucide icons and plain CSS; no external fonts or image service is required.

## Run locally

Start PostgreSQL, migrate the backend, and configure the providers in the root `.env` as described in the [backend guide](../backend/README.md). Keep these three processes running in separate terminals from `backend/`:

```sh
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
uv run repo-copilot-worker
uv run repo-copilot-answer-worker
```

Then, from `frontend/`, with Node.js 22+ and npm installed:

```sh
npm ci
cp .env.example .env.local
npm run dev
```

Open [localhost:3000](http://localhost:3000). Submit a public GitHub HTTPS repository URL, optionally choose a ref, and watch indexing progress. Once a saved version is ready, start a conversation. **Standard** uses the fixed retrieval pipeline; **Investigate** selects the bounded agent and can take longer.

For a production build, use `npm run build` then `npm start`. These commands bind the frontend to loopback. `COPILOT_API_URL` is a server-only setting (default `http://127.0.0.1:8000`). Never put model credentials in frontend variables. Shared hosting still needs the authentication and operational controls planned for Step 13.

## Workspace behavior

- The sidebar remembers recently opened repository IDs in this browser. Shareable URLs identify the repository, saved snapshot, conversation, and any active indexing job. Source, messages, and run history live in PostgreSQL.
- Every conversation stays pinned to its original commit. **Check for updates** indexes HEAD. If a newer commit is ready, **Start chat on latest** creates a separate conversation; existing answers keep their original sources. The version selector also lets you browse older conversation history.
- File folders load on expansion. Excluded files are visibly unavailable; **Index coverage** shows exclusion counts. The source viewer displays bounded excerpts with navigation, and citation clicks highlight the exact stored line range. Extremely long single lines are explicitly shown as clipped.
- Questions use a saved idempotency key. If delivery is uncertain, **Retry question** resends the same request. Reloading or reconnecting only observes an existing run. A pending request saved in this browser can be recovered after a lost response.
- Tool activity streams live. Answer prose is provisional until a completed, validated event; failed or cancelled runs never become final assistant messages. Completed answers retain a collapsible activity log, uncertainty, citations, and immutable GitHub links.
- **Cancel answer** requests cancellation independently of the browser connection. Reloading restores the conversation and any active run; closing a tab does not cancel it.
- Desktop shows files, source, and chat together. Narrow screens use keyboard-navigable panel tabs. Controls have labels, focus indicators, loading/error states, a skip link, and reduced-motion support. Repository/model content is rendered as plain text, never interpreted as HTML.

## Checks

```sh
npm run typecheck
npm run format:check
npm run build
npx playwright install chromium
npm test
```

Browser tests require the backend environment (`cd ../backend && uv sync --locked`) and running PostgreSQL. They start a real FastAPI server and both workers against a unique temporary database schema, plus the built Next.js server. Only Git acquisition and model/embedding providers are deterministic fixtures: **no GitHub fetches or paid model calls occur**. The normal development tables are untouched, and fixture schemas are removed on shutdown.

Set `COPILOT_TEST_DATABASE_URL` to use a separate test database, or tests read only the database address from the root `.env`. The database user needs permission to create/drop test schemas. Ports 18000 and 3100 must be free. Build before running browser tests; the runner intentionally tests production output. Tests cover submission through citations, source inspection, reloads, idempotent retries, cancellation, snapshot pinning, saved activity, mobile navigation, input limits, safe text rendering, and accessibility checks. Screenshots/traces are saved to ignored `test-results/` on failures.

## Architecture and limits

`app/api/[...path]/route.ts` is a fixed-destination same-origin proxy for the existing FastAPI endpoints. It forwards JSON and SSE bodies without buffering run events, preserves replay/idempotency headers, disables caching, and returns an actionable error if the API is unavailable. It does not forward provider secrets or accept an arbitrary upstream URL. See the [Next.js Route Handler reference](https://nextjs.org/docs/app/api-reference/file-conventions/route) for the response streaming API.

`components/home.tsx` provides the animated introduction and link form; `components/workspace.tsx` owns repository/snapshot navigation; `files.tsx` handles source reads; `chat.tsx` owns message delivery and active run observation; `activity.tsx` replays saved public tool activity. The API client follows paginated history endpoints. Native EventSource reconnects with the last received event ID; a status poll also recovers terminal states if an intermediary drops the stream.

Provider adapters currently buffer structured generation, so answer deltas arrive after generation, not token by token. Source retrieval remains bounded by backend read limits. A conversation's prior answers are context, not fresh evidence. The local recent-repository list is not an account or a shared repository directory; opening a conversation link still restores its server-side history. Browser checks establish the workflow, not live answer quality. Step 7/10 quality review and Step 13 packaging remain separate.
