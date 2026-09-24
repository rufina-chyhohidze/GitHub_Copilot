"""Fuse rankings and build inspectable source context with an explicit byte budget."""

from app.tools.repository import RepositoryTools


def fuse_rankings(*rankings: list[dict], limit: int = 10) -> list[dict]:
    combined = {}
    for ranking in rankings:
        seen = set()
        for rank, item in enumerate(ranking, 1):
            key = str(item["id"])
            if key in seen:
                continue
            seen.add(key)
            if key not in combined:
                combined[key] = {**item, "score": 0.0}
            combined[key]["score"] += 1 / (60 + rank)
    ordered = sorted(
        combined.values(), key=lambda item: (-item["score"], item["path"], item["start_char"])
    )
    selected = []
    for item in ordered:
        if any(
            other["file_id"] == item["file_id"]
            and item["start_char"] < other["end_char"]
            and item["end_char"] > other["start_char"]
            for other in selected
        ):
            continue
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def assemble_context(
    tools: RepositoryTools, hits: list[dict], max_bytes: int = 16384
) -> list[dict]:
    import json

    output = []
    for hit in hits:
        file = tools.file(hit["path"])
        lines = file["content"].split("\n")
        if lines[-1:] == [""]:
            lines.pop()
        start = max(1, hit["start_line"] - 3)
        end = min(len(lines), hit["end_line"] + 3)
        detail = next(item for item in tools.run["files"] if item["path"] == hit["path"])
        parents = [s for s in detail["symbols"] if s["name"] == hit["parent_name"]]
        item = {
            "chunk_id": str(hit["id"]),
            "path": hit["path"],
            "language": file["language"],
            "start_line": start,
            "end_line": end,
            "content": "\n".join(lines[start - 1 : end]),
            "symbol": hit["symbol_name"],
            "parent_symbols": parents[:5],
            "imports": detail["imports"][:20],
            "score": hit["score"],
        }
        overlaps = [
            index
            for index, previous in enumerate(output)
            if previous["path"] == item["path"]
            and "start_char" not in previous
            and start <= previous["end_line"]
            and end >= previous["start_line"]
        ]
        if overlaps:
            start = min([start, *(output[index]["start_line"] for index in overlaps)])
            end = max([end, *(output[index]["end_line"] for index in overlaps)])
            merged = {
                **output[overlaps[0]],
                "start_line": start,
                "end_line": end,
                "content": "\n".join(lines[start - 1 : end]),
            }
            candidate = [row for index, row in enumerate(output) if index not in overlaps]
            candidate.insert(overlaps[0], merged)
            if len(json.dumps(candidate, ensure_ascii=False).encode()) <= max_bytes:
                output = candidate
            # If the union exceeds the budget, retain the already-selected higher-ranked context.
            continue
        # Fall back to the exact chunk if expanding long lines or metadata would exceed the budget.
        if len(json.dumps(output + [item], ensure_ascii=False).encode()) > max_bytes:
            item.update(
                content=hit["content"],
                start_line=hit["start_line"],
                end_line=hit["end_line"],
                start_char=hit["start_char"],
                end_char=hit["end_char"],
                imports=[],
                parent_symbols=[],
            )
        if len(json.dumps(output + [item], ensure_ascii=False).encode()) <= max_bytes:
            output.append(item)
    return output
