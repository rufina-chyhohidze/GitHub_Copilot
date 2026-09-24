"""Load evaluation data and verify evidence without importing repository code."""

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.contracts import Contract, Sha256, SourceSpan


class ExpectedSpan(Contract):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    anchor: str = Field(min_length=1)

    _path = field_validator("path")(SourceSpan.relative_path.__func__)

    @model_validator(mode="after")
    def ordered_lines(self) -> "ExpectedSpan":
        if self.end_line < self.start_line:
            raise ValueError("Evidence end_line precedes start_line")
        return self


class EvaluationSource(Contract):
    id: str = Field(min_length=1)
    kind: Literal["fixture", "github"]
    version: str = Field(min_length=1)
    local_path: str | None = None
    url: str | None = None
    commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    files: dict[str, Sha256] = Field(default_factory=dict)

    @model_validator(mode="after")
    def source_identity(self) -> "EvaluationSource":
        if self.kind == "fixture":
            if not self.local_path or not self.files or self.url or self.commit_sha:
                raise ValueError("Fixture requires local_path and file hashes, not a Git URL")
            SourceSpan.relative_path(self.local_path)
        elif not self.url or not self.commit_sha or self.local_path:
            raise ValueError("GitHub source requires URL and commit SHA, not a local_path")
        if self.url and not self.url.startswith("https://github.com/"):
            raise ValueError("Expected a public GitHub HTTPS URL")
        for path in self.files:
            SourceSpan.relative_path(path)
        return self


class EvaluationCase(Contract):
    id: str = Field(min_length=1)
    source_id: str
    split: Literal["development", "held_out"]
    category: Literal[
        "direct", "semantic", "multi_file", "ambiguity", "insufficient_evidence", "adversarial"
    ]
    question: str = Field(min_length=1)
    expected_files: tuple[str, ...] = Field(min_length=1)
    supporting_spans: tuple[ExpectedSpan, ...] = Field(min_length=1)
    required_facts: tuple[str, ...] = Field(min_length=1)
    uncertainty_policy: str = Field(min_length=1)
    forbidden_claims: tuple[str, ...] = ()

    @model_validator(mode="after")
    def evidence_matches_files(self) -> "EvaluationCase":
        for path in self.expected_files:
            SourceSpan.relative_path(path)
        if set(self.expected_files) != {span.path for span in self.supporting_spans}:
            raise ValueError("Every expected file must have a supporting span")
        return self


class Dataset(Contract):
    schema_version: Literal[1] = 1
    id: str
    version: str
    sources: tuple[EvaluationSource, ...] = Field(min_length=1)
    cases: tuple[EvaluationCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_identifiers(self) -> "Dataset":
        source_ids = [source.id for source in self.sources]
        case_ids = [case.id for case in self.cases]
        if len(source_ids) != len(set(source_ids)) or len(case_ids) != len(set(case_ids)):
            raise ValueError("Source and case IDs must be unique")
        if any(case.source_id not in source_ids for case in self.cases):
            raise ValueError("Case references an unknown source")
        return self


def load_dataset(path: Path) -> Dataset:
    return Dataset.model_validate_json(path.read_text(encoding="utf-8"))


def dataset_fingerprint(dataset: Dataset) -> str:
    canonical = json.dumps(dataset.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def contained_file(root: Path, relative: str) -> Path:
    SourceSpan.relative_path(relative)
    candidate = root / relative
    if any(part.is_symlink() for part in (candidate, *candidate.parents)):
        raise ValueError(f"Symlink is not accepted as evidence: {relative}")
    if not candidate.resolve().is_relative_to(root.resolve()) or not candidate.is_file():
        raise ValueError(f"Evidence file is missing or outside the source: {relative}")
    return candidate


def verify_source(dataset: Dataset, source: EvaluationSource, root: Path) -> int:
    """Verify source identity and all labeled spans; never execute or import its code."""
    root = root.resolve()
    if source.kind == "github":
        try:
            head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            dirty = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"Cannot inspect Git checkout for {source.id}") from exc
        if head != source.commit_sha or dirty:
            raise ValueError(f"{source.id} must be a clean checkout of {source.commit_sha}")
    else:
        actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        if actual != set(source.files):
            raise ValueError(f"Fixture file list changed: {source.id}; version its manifest")

    for path, expected_hash in source.files.items():
        content = contained_file(root, path).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise ValueError(f"Source hash mismatch: {source.id}/{path}")

    count = 0
    for case in dataset.cases:
        if case.source_id != source.id:
            continue
        for span in case.supporting_spans:
            lines = contained_file(root, span.path).read_text(encoding="utf-8").splitlines()
            if span.end_line > len(lines):
                raise ValueError(f"Evidence lines outside file: {case.id}/{span.path}")
            if span.anchor not in "\n".join(lines[span.start_line - 1 : span.end_line]):
                raise ValueError(f"Evidence anchor mismatch: {case.id}/{span.path}")
        count += 1
    return count
