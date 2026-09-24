"""Acquire a single public GitHub commit without checkout or repository execution."""

import os
import re
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings


class IngestionError(ValueError):
    """A controlled ingestion failure safe to display without remote stderr."""


def canonical_url(value: str) -> str:
    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})/?",
        value,
    )
    if not match:
        raise IngestionError("Use https://github.com/owner/repository with no credentials or query")
    owner, name = match.groups()
    if name.endswith(".git"):
        name = name[:-4]
    if not name or name in {".", ".."} or name.startswith("-"):
        raise IngestionError("Invalid GitHub repository name")
    return f"https://github.com/{owner.lower()}/{name.lower()}"


def validate_ref(value: str) -> str:
    # Accept ordinary branch/tag names, full refs, and SHAs, not Git revision expressions.
    if (
        len(value) > 255
        or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", value)
        or ".." in value
        or "//" in value
        or any(part.startswith(".") or part.endswith((".", ".lock")) for part in value.split("/"))
        or value.endswith("/")
    ):
        raise IngestionError(
            "Use a branch, tag, or commit SHA for --ref, not a revision expression"
        )
    return value


def git_environment() -> dict[str, str]:
    # Do not inherit credentials, SSH agents, Git overrides, or user/system Git configuration.
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/usr/bin/false",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }


def workspace_size(root: Path) -> int:
    total = 0
    for directory, _, names in os.walk(root):
        for name in names:
            try:
                total += (Path(directory) / name).stat().st_size
            except FileNotFoundError:
                pass  # Git may rename or remove a temporary pack between scans.
    return total


@dataclass
class GitSource:
    workspace: Path
    settings: Settings
    deadline: float
    commit_sha: str = ""

    @property
    def repository(self) -> Path:
        return self.workspace / "repository.git"

    def check_deadline(self) -> None:
        if time.monotonic() >= self.deadline:
            raise IngestionError("Repository ingestion exceeded its time limit")

    def run(self, args: list[str], *, output_limit: int = 65536, timeout: int = 30) -> bytes:
        self.check_deadline()
        command = [
            "git",
            "-c",
            "credential.helper=",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "http.followRedirects=false",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.https.allow=always",
            "-c",
            "gc.auto=0",
            "-c",
            "maintenance.auto=false",
            *args,
        ]
        deadline = min(self.deadline, time.monotonic() + timeout)
        # Disk-backed output avoids pipe deadlocks and limits in-memory allocations.
        with (
            tempfile.TemporaryFile(dir=self.workspace) as stdout,
            tempfile.TemporaryFile(dir=self.workspace) as stderr,
        ):
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.workspace,
                    env=git_environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
            except OSError as exc:
                raise IngestionError("Cannot start Git; install the Git CLI") from exc
            try:
                while True:
                    code = process.poll()
                    if time.monotonic() >= deadline:
                        raise IngestionError("Git operation exceeded its time limit")
                    if os.fstat(stdout.fileno()).st_size > output_limit:
                        raise IngestionError("Git output exceeded its size limit")
                    if os.fstat(stderr.fileno()).st_size > 65536:
                        raise IngestionError("Git diagnostic output exceeded its size limit")
                    disk_bytes = (
                        workspace_size(self.workspace)
                        + os.fstat(stdout.fileno()).st_size
                        + os.fstat(stderr.fileno()).st_size
                    )
                    if disk_bytes > self.settings.max_clone_bytes:
                        raise IngestionError("Repository download exceeded COPILOT_MAX_CLONE_BYTES")
                    if code is not None:
                        break
                    time.sleep(0.02)
                if code != 0:
                    raise IngestionError(
                        "Git operation failed; check the public URL, ref, and network connection"
                    )
                stdout.seek(0)
                return stdout.read(output_limit + 1)
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # The process may finish between poll and killpg.
                process.wait()

    def git(self, *args: str, output_limit: int = 65536) -> bytes:
        return self.run(["--git-dir", str(self.repository), *args], output_limit=output_limit)


@contextmanager
def acquire(url: str, ref: str, settings: Settings) -> Iterator[GitSource]:
    url = canonical_url(url)
    ref = validate_ref(ref)
    # TemporaryDirectory only cleans up the unique directory created for this invocation.
    with tempfile.TemporaryDirectory(prefix="repository-copilot-") as directory:
        source = GitSource(
            Path(directory), settings, time.monotonic() + settings.ingestion_timeout_seconds
        )
        source.run(["init", "--bare", "--template=", str(source.repository)])
        source.run(
            [
                "--git-dir",
                str(source.repository),
                "fetch",
                "--quiet",
                "--depth=1",
                "--no-tags",
                "--no-recurse-submodules",
                "--",
                url + ".git",
                ref,
            ],
            timeout=settings.clone_timeout_seconds,
        )
        source.commit_sha = (
            source.git("rev-parse", "--verify", "FETCH_HEAD^{commit}").decode().strip()
        )
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source.commit_sha):
            raise IngestionError("Git did not resolve a valid commit SHA")
        yield source
