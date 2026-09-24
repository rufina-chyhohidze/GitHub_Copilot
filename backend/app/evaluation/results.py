"""Output format for future runs; an unreviewed answer is never a passing result."""

from typing import Literal

from pydantic import Field, model_validator

from app.evaluation.dataset import Dataset, dataset_fingerprint
from app.models.contracts import Contract, Sha256


class ResultCitation(Contract):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class CaseResult(Contract):
    case_id: str
    status: Literal["answered", "failed", "skipped"]
    answer: str | None = None
    citations: tuple[ResultCitation, ...] = ()
    retrieved_files: tuple[str, ...] = ()
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    error: str | None = None
    review: Literal["unreviewed", "pass", "fail"] = "unreviewed"
    reviewer_notes: str | None = None

    @model_validator(mode="after")
    def consistent_outcome(self) -> "CaseResult":
        if self.status == "answered" and not self.answer:
            raise ValueError("Answered result requires answer text")
        if self.status == "failed" and not self.error:
            raise ValueError("Failed result requires an error")
        if self.review == "pass" and self.status != "answered":
            raise ValueError("Only answered cases can pass review")
        if self.review != "unreviewed" and not self.reviewer_notes:
            raise ValueError("A review requires notes explaining the rubric assessment")
        return self


class EvaluationRun(Contract):
    schema_version: Literal[1] = 1
    dataset_id: str
    dataset_version: str
    dataset_sha256: Sha256
    pipeline_version: str
    model_id: str
    split: Literal["development", "held_out"]
    results: tuple[CaseResult, ...]

    def validate_against(self, dataset: Dataset) -> None:
        if (self.dataset_id, self.dataset_version, self.dataset_sha256) != (
            dataset.id,
            dataset.version,
            dataset_fingerprint(dataset),
        ):
            raise ValueError("Results do not match this exact dataset version/content")
        expected = {case.id for case in dataset.cases if case.split == self.split}
        ids = [result.case_id for result in self.results]
        if len(ids) != len(set(ids)) or set(ids) != expected:
            raise ValueError("Include every case in the selected split exactly once")
