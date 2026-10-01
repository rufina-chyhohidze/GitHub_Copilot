"""Only the application creates source locations and links, never the model."""

from hashlib import sha256
from urllib.parse import quote
from uuid import uuid4

from pydantic import Field

from app.models.contracts import Contract, Evidence, SourceSpan


class Claim(Contract):
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class Draft(Contract):
    claims: list[Claim] = Field(
        description=(
            "All relevant supported facts, including documented source limitations and scoped "
            "absence statements. Each fact must cite supplied evidence."
        )
    )
    uncertainty: str | None = Field(
        description=(
            "Remaining unknowns and search limitations. Source-backed factual statements belong "
            "in cited claims, even when the overall answer is uncertain."
        )
    )


class EvidenceRegistry:
    def __init__(self, tools, run_id):
        self.tools = tools
        self.run_id = run_id
        self.entries = {}

    def add(self, path, start, end):
        read = self.tools.read_file(path, start, end)
        # A clipped partial line is not an exact inclusive source span.
        if read["truncated"] or read["start_line"] is None:
            return None
        file = self.tools.file(path)
        evidence = Evidence(
            id=uuid4(),
            run_id=self.run_id,
            source=SourceSpan(
                snapshot_id=self.tools.snapshot_id,
                file_id=file["id"],
                path=path,
                start_line=start,
                end_line=end,
            ),
            content_hash=sha256(read["content"].encode()).hexdigest(),
            originating_tool="read_file",
        )
        self.entries[str(evidence.id)] = (evidence, read["content"])
        return {
            "id": str(evidence.id),
            "path": path,
            "start_line": start,
            "end_line": end,
            "content": read["content"],
        }

    def resolve(self, evidence_id):
        if evidence_id not in self.entries:
            raise ValueError("Unknown evidence ID")
        evidence, content = self.entries[evidence_id]
        span = evidence.source
        file = self.tools.file(span.path)
        lines = file["content"].split("\n")
        if lines[-1:] == [""]:
            lines.pop()
        if (
            evidence.run_id != self.run_id
            or span.snapshot_id != self.tools.snapshot_id
            or file["snapshot_id"] != self.tools.snapshot_id
            or file["id"] != span.file_id
            or not 1 <= span.start_line <= span.end_line <= len(lines)
            or sha256(file["content"].encode()).hexdigest() != file["content_hash"]
        ):
            raise ValueError("Evidence provenance is invalid")
        actual = "\n".join(lines[span.start_line - 1 : span.end_line])
        if actual != content or sha256(actual.encode()).hexdigest() != evidence.content_hash:
            raise ValueError("Evidence content changed")
        return evidence

    def validate(self, draft):
        if not draft.claims and not (draft.uncertainty or "").strip():
            raise ValueError("An answer needs cited claims or explicit uncertainty")
        for claim in draft.claims:
            if not claim.text.strip():
                raise ValueError("Empty claim")
            for evidence_id in claim.evidence_ids:
                self.resolve(evidence_id)


def citation(evidence, url, sha):
    span = evidence.source
    return {
        **evidence.model_dump(mode="json"),
        "url": f"{url}/blob/{sha}/{quote(span.path, safe='/')}#L{span.start_line}-L{span.end_line}",
    }


def render(result):
    if result["status"] != "completed":
        return f"Answer failed: {result['error']}"
    lines = [f"Commit: {result['commit_sha']}"]
    citations = {row["id"]: row for row in result["citations"]}
    for claim in result["answer"]["claims"]:
        lines.append("\n" + claim["text"])
        for evidence_id in dict.fromkeys(claim["evidence_ids"]):
            row = citations[evidence_id]
            span = row["source"]
            lines.append(f"  {span['path']}:{span['start_line']}-{span['end_line']} {row['url']}")
    if result["answer"]["uncertainty"]:
        lines.append("\nUncertainty: " + result["answer"]["uncertainty"])
    return "\n".join(lines)
