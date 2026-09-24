"""Source locations use one-based, inclusive lines in an immutable snapshot."""

from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Snapshot(Contract):
    id: UUID
    repository_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    index_version: str = Field(min_length=1)
    status: Literal["pending", "ingested", "indexing", "ready", "failed"] = "pending"


class SourceSpan(Contract):
    snapshot_id: UUID
    file_id: UUID
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in value
            or "\x00" in value
            or str(path) != value
            or value == "."
        ):
            raise ValueError("Expected a normalized repository-relative POSIX path")
        return value

    @model_validator(mode="after")
    def ordered_lines(self) -> "SourceSpan":
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class ParsedSymbol(Contract):
    source: SourceSpan
    name: str = Field(min_length=1)
    kind: Literal["class", "function", "method", "variable", "module"]
    language: str
    parent_name: str | None = None


class CodeChunk(Contract):
    id: UUID
    source: SourceSpan
    content: str = Field(min_length=1)
    content_hash: Sha256
    parser_version: str
    symbol: ParsedSymbol | None = None


class SearchHit(Contract):
    chunk: CodeChunk
    score: float = Field(allow_inf_nan=False)
    method: Literal["lexical", "semantic", "hybrid"]


class Evidence(Contract):
    id: UUID
    run_id: UUID
    source: SourceSpan
    content_hash: Sha256
    originating_tool: str


class Answer(Contract):
    snapshot_id: UUID
    run_id: UUID
    text: str = Field(min_length=1)
    evidence_ids: tuple[UUID, ...] = ()
    uncertainty: str | None = None


class UsageLimits(Contract):
    max_tool_calls: int = Field(default=12, gt=0)
    max_context_tokens: int = Field(default=16000, gt=0)
    max_output_tokens: int = Field(default=2000, gt=0)
    timeout_seconds: int = Field(default=90, gt=0)
