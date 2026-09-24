"""Bound every result and bind every lookup to an explicitly selected snapshot."""

import json
from functools import wraps
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.db.schema import parsing_runs, repository_files, snapshots
from app.models.contracts import SourceSpan


def bounded_text(value: str, max_bytes: int) -> str:
    return value.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def literal_query(query: str) -> str:
    if not query.strip() or len(query.encode()) > 512 or "\x00" in query:
        raise ValueError("Provide a nonempty search query of at most 512 UTF-8 bytes")
    return query


def bounded_result(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        result = function(*args, **kwargs)
        if len(json.dumps(result, ensure_ascii=False).encode()) > 32768:
            raise ValueError(
                "Tool output exceeds 32 KiB; narrow the path, line range, or result limit"
            )
        return result

    return wrapped


class RepositoryTools:
    def __init__(self, connection: Connection, snapshot_id: UUID, run_id: UUID | None = None):
        self.connection = connection
        self.snapshot_id = snapshot_id
        self.requested_run = run_id
        self._run = None
        self.snapshot = (
            connection.execute(select(snapshots).where(snapshots.c.id == snapshot_id))
            .mappings()
            .one_or_none()
        )
        if not self.snapshot:
            raise ValueError("Snapshot not found")

    @property
    def run(self):
        if self._run is None:
            query = select(parsing_runs).where(parsing_runs.c.snapshot_id == self.snapshot_id)
            if self.requested_run:
                query = query.where(parsing_runs.c.id == self.requested_run)
            self._run = (
                self.connection.execute(
                    query.order_by(parsing_runs.c.created_at.desc(), parsing_runs.c.id).limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if not self._run:
                raise ValueError(
                    "Parsing run not found in this snapshot; run repo-copilot parse first"
                )
        return self._run

    def file(self, path: str):
        SourceSpan.relative_path(path)
        file = (
            self.connection.execute(
                select(repository_files).where(
                    repository_files.c.snapshot_id == self.snapshot_id,
                    repository_files.c.path == path,
                )
            )
            .mappings()
            .one_or_none()
        )
        if file is None:
            raise ValueError("Stored file not found in this snapshot; it may have been excluded")
        return file

    @bounded_result
    def get_repository_tree(self, path: str = "", limit: int = 100) -> dict:
        if not 1 <= limit <= 500:
            raise ValueError("Tree limit must be between 1 and 500")
        if path:
            SourceSpan.relative_path(path)
        prefix = path + "/" if path else ""
        entries = {}
        for item in self.snapshot["manifest"]:
            if not item["path"].startswith(prefix):
                continue
            relative = item["path"][len(prefix) :]
            name, separator, _ = relative.partition("/")
            key = prefix + name
            entries[key] = (
                {"path": key, "type": "directory"}
                if separator
                else {
                    "path": key,
                    "type": "file",
                    "status": item["status"],
                    "reason": item["reason"],
                }
            )
        ordered = [entries[key] for key in sorted(entries)]
        return {
            "snapshot_id": str(self.snapshot_id),
            "entries": ordered[:limit],
            "truncated": len(ordered) > limit,
        }

    @bounded_result
    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> dict:
        file = self.file(path)
        lines = file["content"].split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if start_line < 1 or (end_line is not None and end_line < start_line):
            raise ValueError("Use one-based inclusive line ranges")
        if not lines:
            return {
                "snapshot_id": str(self.snapshot_id),
                "path": path,
                "content": "",
                "start_line": None,
                "end_line": None,
                "truncated": False,
            }
        if start_line > len(lines) or (end_line is not None and end_line > len(lines)):
            raise ValueError("Requested lines are outside the stored file")
        requested_end = end_line or len(lines)
        end = min(requested_end, start_line + 199)
        content = "\n".join(lines[start_line - 1 : end])
        clipped = bounded_text(content, 16384)
        return {
            "snapshot_id": str(self.snapshot_id),
            "file_id": str(file["id"]),
            "path": path,
            "start_line": start_line,
            "end_line": start_line + clipped.count("\n"),
            "content": clipped,
            "truncated": end < requested_end or clipped != content,
        }

    @bounded_result
    def search_code(self, query: str, *, case_sensitive: bool = True, limit: int = 20) -> dict:
        query = literal_query(query)
        if not 1 <= limit <= 100:
            raise ValueError("Search limit must be between 1 and 100")
        column = repository_files.c.content
        needle = query if case_sensitive else query.lower()
        sql_column = column if case_sensitive else func.lower(column)
        files = self.connection.execute(
            select(repository_files.c.path, column)
            .where(
                repository_files.c.snapshot_id == self.snapshot_id,
                func.strpos(sql_column, needle) > 0,
            )
            .order_by(repository_files.c.path)
        ).mappings()
        hits = []
        for file in files:
            content = file["content"]
            searchable = content if case_sensitive else content.lower()
            # Search at character offsets so literal multiline queries are supported as well.
            position = 0
            while (found := searchable.find(needle, position)) >= 0:
                line = searchable.count("\n", 0, found) + 1
                end = line + needle.count("\n") - int(needle.endswith("\n"))
                excerpt = "\n".join(content.split("\n")[line - 1 : end])
                hits.append(
                    {
                        "path": file["path"],
                        "start_line": line,
                        "end_line": end,
                        "content": bounded_text(excerpt, 512),
                    }
                )
                if len(hits) > limit:
                    return {"hits": hits[:limit], "truncated": True}
                position = found + len(needle)
        return {"hits": hits, "truncated": False}

    @bounded_result
    def get_file_symbols(self, path: str, limit: int = 100) -> dict:
        self.file(path)
        if not 1 <= limit <= 500:
            raise ValueError("Symbol limit must be between 1 and 500")
        detail = next(item for item in self.run["files"] if item["path"] == path)
        return {
            "path": path,
            "parsing_run_id": str(self.run["id"]),
            "status": detail["status"],
            "error": detail["error"],
            "symbols": detail["symbols"][:limit],
            "truncated": len(detail["symbols"]) > limit,
        }

    @bounded_result
    def find_symbol(self, name: str, limit: int = 20) -> dict:
        name = literal_query(name)
        if not 1 <= limit <= 100:
            raise ValueError("Symbol limit must be between 1 and 100")
        hits = []
        for file in self.run["files"]:
            for symbol in file["symbols"]:
                if symbol["name"] == name or symbol["name"].rsplit(".", 1)[-1] == name:
                    hits.append({"path": file["path"], **symbol})
                    if len(hits) > limit:
                        return {"symbols": hits[:limit], "truncated": True}
        return {"symbols": hits, "truncated": False}
