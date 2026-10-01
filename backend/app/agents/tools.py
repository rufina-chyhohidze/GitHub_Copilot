"""Run-local LangChain tools bound to one immutable snapshot and parsing run."""

import json
import time

from langchain_core.tools import StructuredTool
from pydantic import Field
from sqlalchemy import text

from app.models.contracts import Contract
from app.retrieval.service import retrieve


class ReadArgs(Contract):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class PathArgs(Contract):
    path: str
    limit: int = Field(default=20, ge=1, le=50)


class TreeArgs(Contract):
    path: str = ""
    limit: int = Field(default=20, ge=1, le=50)


class NameArgs(Contract):
    name: str
    limit: int = Field(default=20, ge=1, le=50)


class SearchArgs(Contract):
    query: str
    limit: int = Field(default=10, ge=1, le=10)


class RepositoryAgentTools:
    def __init__(self, engine, tools, registry, settings, provider, mode, deadline):
        self.engine, self.tools, self.registry = engine, tools, registry
        self.settings, self.provider, self.mode, self.deadline = settings, provider, mode, deadline
        self.embedding_tokens = 0
        self.first_retrieval = None
        self.evidence = []
        self.inspected_files = []
        self.calls = 0
        self.returned_bytes = 0
        self.events = []
        self.observations = []
        self.warnings = []
        self.catalog = {}
        specs = [
            (
                "search",
                SearchArgs,
                self.search,
                "Search this snapshot; returns navigation spans, not evidence.",
            ),
            (
                "read_file",
                ReadArgs,
                self.read,
                "Read exact lines and register evidence IDs. Narrow spans to fit the byte budget.",
            ),
            (
                "get_repository_tree",
                TreeArgs,
                self.tools.get_repository_tree,
                "List bounded immediate file/directory children.",
            ),
            (
                "find_symbol",
                NameArgs,
                self.tools.find_symbol,
                "Find syntactic declarations by exact or unqualified name.",
            ),
            (
                "get_file_symbols",
                PathArgs,
                self.tools.get_file_symbols,
                "List declarations and syntactic metadata in one file.",
            ),
            (
                "get_imports",
                PathArgs,
                self.tools.get_imports,
                "Inspect import/export syntax; module targets are unresolved.",
            ),
            (
                "find_references",
                NameArgs,
                self.tools.find_references,
                "Find literal candidate occurrences; these are not confirmed calls.",
            ),
        ]
        for name, schema, function, description in specs:

            async def invoke(_function=function, **kwargs):
                if _function == self.search:
                    return await _function(**kwargs)
                return _function(**kwargs)

            self.catalog[name] = StructuredTool.from_function(
                name=name,
                description=description,
                args_schema=schema,
                coroutine=invoke,
            )

    def read(self, path, start_line, end_line):
        content = self.tools.file(path)["content"]
        lines = content.split("\n")
        if lines[-1:] == [""]:
            lines.pop()
        if not 1 <= start_line <= end_line <= len(lines):
            return {
                "error": "invalid_line_range",
                "available_lines": len(lines),
                "message": "Use an inclusive range within available_lines, starting at 1.",
            }
        entry = self.registry.add(path, start_line, end_line)
        if entry is None:
            return {
                "error": "read_truncated_or_empty",
                "message": "Request a smaller nonempty span.",
            }
        return {"evidence": entry}

    async def search(self, query, limit=10):
        remaining = self.settings.embedding_token_budget - self.embedding_tokens
        bounded_settings = self.settings.model_copy(
            update={"embedding_token_budget": max(0, remaining)}
        )
        result = await retrieve(
            self.engine,
            self.tools.snapshot_id,
            query,
            bounded_settings,
            mode=self.mode,
            provider=self.provider,
            run_id=self.tools.run["id"],
            limit=limit,
        )
        self.embedding_tokens += result["input_tokens"]
        if self.first_retrieval is None:
            self.first_retrieval = result
        return {
            "retrieved_files": result["retrieved_files"],
            "spans": [
                {key: row[key] for key in ("path", "start_line", "end_line", "symbol")}
                for row in result["context"]
            ],
            "kind": "navigation_only",
        }

    async def invoke(self, name, arguments):
        if self.calls >= self.settings.max_tool_calls:
            raise ValueError("tool_budget")
        if self.settings.agent_total_tool_bytes - self.returned_bytes < 256:
            raise ValueError("tool_bytes_exhausted")
        if time.monotonic() >= self.deadline:
            raise TimeoutError
        self.calls += 1
        started = time.monotonic()
        before = set(self.registry.entries)
        try:
            if name not in self.catalog:
                raise ValueError("unknown tool")
            # Bound synchronous database statements inside the overall async run deadline.
            self.tools.connection.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": str(max(1, int((self.deadline - time.monotonic()) * 1000)))},
            )
            output = await self.catalog[name].ainvoke(arguments, config={"callbacks": []})
            encoded = json.dumps(output, ensure_ascii=False).encode()
            evidence_over_budget = "evidence" in output and len(
                json.dumps(self.evidence + [output["evidence"]], ensure_ascii=False).encode()
            ) > max(0, self.settings.max_context_tokens - 7000)
            if (
                len(encoded) > self.settings.agent_tool_result_bytes
                or self.returned_bytes + len(encoded) > self.settings.agent_total_tool_bytes
                or evidence_over_budget
            ):
                output = {
                    "error": "tool_bytes_exhausted",
                    "message": "Output withheld; request a smaller result.",
                }
                self.warnings.append("tool_bytes_exhausted")
                for eid in set(self.registry.entries) - before:
                    del self.registry.entries[eid]
            else:
                if "evidence" in output:
                    self.evidence.append(output["evidence"])
                    self.inspected_files.append(output["evidence"]["path"])
        except (ValueError, KeyError):
            for eid in set(self.registry.entries) - before:
                del self.registry.entries[eid]
            output = {
                "error": "invalid_tool_request",
                "message": "Check the tool arguments, path and line range.",
            }
        self.returned_bytes += len(json.dumps(output, ensure_ascii=False).encode())
        self.events.append(
            {
                "tool": name,
                "status": "error" if "error" in output else "completed",
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "returned_bytes": len(json.dumps(output, ensure_ascii=False).encode()),
            }
        )
        self.observations.append({"tool": name, "arguments": arguments, "result": output})
        if time.monotonic() >= self.deadline:
            raise TimeoutError
        return output
