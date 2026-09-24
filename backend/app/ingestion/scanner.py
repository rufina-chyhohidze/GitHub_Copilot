"""Build a bounded manifest from Git objects, never filesystem checkout paths."""

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

from app.config import Settings
from app.ingestion.clone import GitSource, IngestionError
from app.models.contracts import SourceSpan

SCANNER_VERSION = "source-v1"
IGNORED_DIRECTORIES = {
    ".git",
    "node_modules",
    ".next",
    "dist",
    "build",
    "coverage",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
}
LOCK_FILES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
    "bun.lockb",
    "bun.lock",
    "go.sum",
}
LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".md": "markdown",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".sh": "shell",
}


@dataclass(frozen=True)
class StoredFile:
    path: str
    language: str
    content: str
    content_hash: str
    raw_hash: str
    size_bytes: int
    line_count: int
    parse_status: str = "pending"


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    git_object: str
    size_bytes: int | None
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class ScanResult:
    files: tuple[StoredFile, ...]
    manifest: tuple[ManifestEntry, ...]

    def coverage(self) -> dict:
        return {
            "total_entries": len(self.manifest),
            "stored_files": len(self.files),
            "stored_bytes": sum(file.size_bytes for file in self.files),
            "excluded_by_reason": dict(
                Counter(item.reason for item in self.manifest if item.reason)
            ),
            "parser_status": "not_started",
        }


def index_version(settings: Settings) -> str:
    # File-size policy changes alter coverage, so they must not overwrite an old snapshot.
    policy = json.dumps({"scanner": SCANNER_VERSION, "max_file_bytes": settings.max_file_bytes})
    return f"{SCANNER_VERSION}-{hashlib.sha256(policy.encode()).hexdigest()[:12]}"


def excluded_path(path: str, mode: str, size: int | None, settings: Settings) -> str | None:
    if mode == "120000":
        return "symlink"
    if mode == "160000":
        return "submodule"
    try:
        SourceSpan.relative_path(path)
    except ValueError:
        return "unsupported_path"
    parsed = PurePosixPath(path)
    if any(part in IGNORED_DIRECTORIES for part in parsed.parts[:-1]):
        return "ignored_directory"
    if parsed.name in LOCK_FILES:
        return "lockfile"
    if parsed.name.endswith((".min.js", ".min.css", ".map", "_pb2.py", "_pb2_grpc.py")):
        return "generated"
    if mode not in {"100644", "100755"}:
        return "unsupported_mode"
    if size is None or size > settings.max_file_bytes:
        return "file_too_large"
    return None


def scan(source: GitSource) -> ScanResult:
    settings = source.settings
    listing = source.git(
        "ls-tree",
        "-r",
        "-l",
        "-z",
        "--full-tree",
        source.commit_sha,
        output_limit=min(settings.max_repository_files * 4096, 16 * 1024 * 1024),
    )
    records = [record for record in listing.split(b"\0") if record]
    if len(records) > settings.max_repository_files:
        raise IngestionError("Repository exceeds COPILOT_MAX_REPOSITORY_FILES")
    files = []
    manifest = []
    source_bytes = 0
    for record in records:
        source.check_deadline()
        header, raw_path = record.split(b"\t", 1)
        mode, _, object_id, raw_size = header.decode("ascii").split()
        size = None if raw_size == "-" else int(raw_size)
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            manifest.append(
                ManifestEntry(
                    raw_path.decode("utf-8", errors="backslashreplace"),
                    object_id,
                    size,
                    "excluded",
                    "non_utf8_path",
                )
            )
            continue
        reason = excluded_path(path, mode, size, settings)
        if reason:
            manifest.append(ManifestEntry(path, object_id, size, "excluded", reason))
            continue
        # Object IDs come from ls-tree, not from user-controlled paths or revision expressions.
        raw = source.git("cat-file", "blob", object_id, output_limit=settings.max_file_bytes)
        if len(raw) != size:
            raise IngestionError("Git blob size differs from the manifest")
        reason = None
        if any(byte < 32 and byte not in {9, 10, 12, 13} for byte in raw):
            reason = "binary"
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            reason = reason or "non_utf8_content"
            content = ""
        if raw.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
            reason = "lfs_pointer"
        if any(
            marker in content[:2048].lower()
            for marker in ("@generated", "auto-generated", "automatically generated")
        ):
            reason = reason or "generated"
        if reason:
            manifest.append(ManifestEntry(path, object_id, size, "excluded", reason))
            continue
        source_bytes += len(raw)
        if source_bytes > settings.max_source_bytes:
            raise IngestionError("Stored source exceeds COPILOT_MAX_SOURCE_BYTES")
        # Preserve characters, including a UTF-8 BOM; normalize CRLF/CR for stable line display.
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        language = LANGUAGES.get(PurePosixPath(path).suffix.lower(), "text")
        line_count = content.count("\n") + int(bool(content) and not content.endswith("\n"))
        files.append(
            StoredFile(
                path=path,
                language=language,
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                raw_hash=hashlib.sha256(raw).hexdigest(),
                size_bytes=len(raw),
                line_count=line_count,
            )
        )
        manifest.append(ManifestEntry(path, object_id, size, "stored"))
    source.check_deadline()
    return ScanResult(tuple(files), tuple(manifest))


def manifest_json(result: ScanResult) -> list[dict]:
    return [asdict(entry) for entry in result.manifest]
