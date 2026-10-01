# Bounded repository agent

Step 10 adds an opt-in single agent loop. The fixed answering pipeline remains the default. From `backend/`, configure the same database, model and embedding provider as the [answering guide](../answering/README.md), then run:

```sh
uv run repo-copilot ask https://github.com/pypa/sampleproject \
  'How does the command reach its implementation?' --pipeline agent --json
```

For an already published snapshot, POST `{"question":"How does this work?","pipeline":"agent"}` to `/snapshots/{id}/ask`. Both pipelines pin the published parsing run. No migrations are needed for Step 10.

## Execution and evidence

`planner.py` requests a strict structured decision from the Responses API. `service.py` executes those decisions through LangChain `StructuredTool` wrappers; this is a custom bounded loop, not native provider function calling or a LangGraph workflow. It first searches for the question, then allows search, tree, symbol, imports, candidate references and bounded file reads. There is no shell, network, source execution or write tool. All services remain bound to one snapshot and parsing run.

Search results are navigation hints. Only complete file reads register citable evidence. Oversized results are withheld and any associated evidence IDs are removed. Imports are syntax records, not resolved module edges; references are candidate text occurrences, not confirmed calls. Source text is marked untrusted in planning and answering instructions. Tool names, arguments, paths and resource limits are validated outside the model; instruction following alone is not a prompt-injection guarantee.

Planning reserves model calls for a final structured answer and one possible repair. Final citations are checked deterministically against the evidence registry. Exhausted budgets can produce an explicit insufficient-evidence answer; failed validation never publishes unvalidated claims. An empty search does not prove a feature absent.

## Limits and traces

Existing `COPILOT_MAX_TOOL_CALLS` (12), `COPILOT_MAX_CONTEXT_TOKENS` (16000), `COPILOT_MAX_OUTPUT_TOKENS` (2000 per answer call), and `COPILOT_RUN_TIMEOUT_SECONDS` (90) apply. Agent-specific defaults are:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `COPILOT_AGENT_MAX_MODEL_CALLS` | 8 | Planning, final generation and repair combined |
| `COPILOT_AGENT_INPUT_TOKEN_BUDGET` | 96000 | Cumulative input usage, with conservative preflight checks |
| `COPILOT_AGENT_OUTPUT_TOKEN_BUDGET` | 6000 | Cumulative output usage |
| `COPILOT_AGENT_TOOL_RESULT_BYTES` | 6000 | Serialized bytes per observation |
| `COPILOT_AGENT_TOTAL_TOOL_BYTES` | 24000 | Serialized bytes across observations |
| `COPILOT_AGENT_LANGSMITH_ENABLED` | false | Explicit tracing opt-in |

Query embeddings share the existing embedding-token budget. Calls that fail before usage is returned may incur unreported provider charges. Local traces retain tool arguments/results, evidence, stop reasons, timings, and aggregate usage, without requesting or storing private reasoning. They can contain repository excerpts and questions.

LangSmith tracing is disabled by default, including when ambient tracing is enabled. To opt in, set `COPILOT_AGENT_LANGSMITH_ENABLED=true` and configure LangSmith credentials/project in the process environment. Tool telemetry may then be sent to that service; local traces remain available independently.

See the [evaluation guide](../evaluation/README.md) for matched comparisons. Implementation references: [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs) and [LangChain StructuredTool](https://reference.langchain.com/python/langchain-core/tools/structured/StructuredTool/).
