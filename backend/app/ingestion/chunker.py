"""Partition source into non-overlapping chunks while preserving exact source slices."""

import hashlib
import json
from bisect import bisect_right
from dataclasses import dataclass

from app.ingestion.parser import PARSER_VERSION, ParseResult, Symbol

CHUNKER_VERSION = "source-chunks-v1"


@dataclass(frozen=True)
class Chunk:
    content: str
    content_hash: str
    start_line: int
    end_line: int
    start_char: int
    end_char: int
    symbol_name: str | None
    symbol_type: str | None
    parent_name: str | None
    token_upper_bound: int


def pipeline_version(max_tokens: int) -> str:
    identity = json.dumps([PARSER_VERSION, CHUNKER_VERSION, "utf8-byte-bound-v1", max_tokens])
    return hashlib.sha256(identity.encode()).hexdigest()


def chunks_for_source(
    content: str, parsed: ParseResult, max_tokens: int, *, max_chunks: int = 50000
) -> tuple[Chunk, ...]:
    if max_tokens < 4:
        raise ValueError("Chunk budget must allow at least one UTF-8 character (4 bytes)")
    if not content:
        return ()
    # Split only on LF, matching stored-file line numbering (not Unicode splitlines semantics).
    if "\r" in content:
        raise ValueError("Chunking requires ingestion-normalized LF source")
    lines = content.split("\n")
    lines = [line + "\n" for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    owners: list[Symbol | None] = [None] * len(lines)
    for symbol in parsed.symbols:
        if symbol.kind not in {"class", "function", "method"}:
            continue
        for index in range(symbol.start_line - 1, symbol.end_line):
            owners[index] = symbol

    # A parent's context consists of its own lines; child definitions get their own chunks.
    segments = []
    start = 0
    for index in range(1, len(lines) + 1):
        if index == len(lines) or owners[index] != owners[start]:
            segments.append((offsets[start], offsets[index], owners[start]))
            start = index

    chunks = []
    for start, stop, owner in segments:
        while start < stop:
            if len(chunks) >= max_chunks:
                raise ValueError("Parsing exceeded COPILOT_MAX_SNAPSHOT_CHUNKS")
            end = start
            size = 0
            last_newline = None
            while end < stop:
                width = len(content[end].encode("utf-8"))
                if size + width > max_tokens:
                    break
                size += width
                if content[end] == "\n":
                    last_newline = end + 1
                end += 1
            # Prefer line boundaries; only split inside a line when that line exceeds the budget.
            if end < stop and last_newline is not None:
                end = last_newline
            piece = content[start:end]
            chunks.append(
                Chunk(
                    content=piece,
                    content_hash=hashlib.sha256(piece.encode()).hexdigest(),
                    start_line=bisect_right(offsets, start),
                    end_line=bisect_right(offsets, end - 1),
                    start_char=start,
                    end_char=end,
                    symbol_name=owner.name if owner else None,
                    symbol_type=owner.kind if owner else None,
                    parent_name=owner.parent_name if owner else None,
                    token_upper_bound=len(piece.encode("utf-8")),
                )
            )
            start = end
    return tuple(chunks)
