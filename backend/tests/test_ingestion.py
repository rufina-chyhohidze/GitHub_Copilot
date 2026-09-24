import hashlib
import os
import subprocess
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.ingestion.clone import (
    GitSource,
    IngestionError,
    acquire,
    canonical_url,
    git_environment,
    validate_ref,
)
from app.ingestion.scanner import excluded_path, index_version, scan


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/owner/repo",
        "https://github.com.evil.test/owner/repo",
        "https://user:password@github.com/owner/repo",
        "https://github.com/owner/repo?token=secret",
        "https://github.com/owner/repo/tree/main",
        "file:///tmp/repository",
        "git@github.com:owner/repo",
        "https://github.com/owner/..",
        "https://github.com/owner/%2e%2e",
        "--upload-pack=evil",
    ],
)
def test_url_rejects_noncanonical_or_unsafe_inputs(url):
    with pytest.raises(IngestionError):
        canonical_url(url)


def test_url_identity_is_canonical():
    assert canonical_url("https://github.com/PyPA/SampleProject.git/") == (
        "https://github.com/pypa/sampleproject"
    )


@pytest.mark.parametrize(
    "ref", ["--help", "main:other", "HEAD~1", "a..b", "a//b", "a.lock", "a/", ""]
)
def test_ref_rejects_options_and_revision_expressions(ref):
    with pytest.raises(IngestionError):
        validate_ref(ref)


@pytest.mark.parametrize("ref", ["HEAD", "main", "feature/orders", "refs/tags/v1.0", "a" * 40])
def test_plain_refs_are_supported(ref):
    assert validate_ref(ref) == ref


def test_git_does_not_inherit_credentials_or_configuration(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "url.file:///tmp/.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://github.com/")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    environment = git_environment()
    assert "GITHUB_TOKEN" not in environment
    assert "GIT_CONFIG_COUNT" not in environment
    assert environment["GIT_TERMINAL_PROMPT"] == "0"


class MemorySource:
    """Supply the same tree/blob protocol without a network or executable fixture."""

    def __init__(self, files, **settings):
        self.settings = Settings(_env_file=None, **settings)
        self.commit_sha = "a" * 40
        self.blobs = {}
        entries = []
        for number, (path, mode, content) in enumerate(files):
            oid = f"{number:040x}"
            kind = "commit" if mode == "160000" else "blob"
            size = "-" if mode == "160000" else str(len(content))
            entries.append(f"{mode} {kind} {oid} {size}\t{path}".encode() + b"\0")
            self.blobs[oid] = content
        self.listing = b"".join(entries)
        self.read_objects = []

    def check_deadline(self):
        pass

    def git(self, *args, output_limit=65536):
        if args[0] == "ls-tree":
            return self.listing
        self.read_objects.append(args[-1])
        return self.blobs[args[-1]]


def test_manifest_records_exclusions_without_reading_unsafe_objects():
    source = MemorySource(
        [
            ("src/app.py", "100644", b"value = 1\n"),
            ("package.json", "100644", b"{}\n"),
            ("config.toml", "100644", b"port = 8000\n"),
            ("node_modules/lib/index.js", "100644", b"ignored"),
            ("package-lock.json", "100644", b"ignored"),
            ("outside", "120000", b"/etc/passwd"),
            ("vendor/module", "160000", b""),
            ("blob.bin", "100644", b"\x00\x01"),
            ("latin.txt", "100644", b"\xff"),
            ("generated.py", "100644", b"# @generated\n"),
            ("large.txt", "100644", b"x" * 100),
            ("asset", "100644", b"version https://git-lfs.github.com/spec/v1\n"),
        ],
        max_file_bytes=64,
    )
    result = scan(source)
    assert {file.path for file in result.files} == {"src/app.py", "package.json", "config.toml"}
    reasons = {entry.path: entry.reason for entry in result.manifest}
    assert reasons["outside"] == "symlink"
    assert reasons["vendor/module"] == "submodule"
    assert reasons["large.txt"] == "file_too_large"
    assert reasons["latin.txt"] == "non_utf8_content"
    assert reasons["asset"] == "lfs_pointer"
    assert result.coverage()["excluded_by_reason"] == {
        "ignored_directory": 1,
        "lockfile": 1,
        "symlink": 1,
        "submodule": 1,
        "binary": 1,
        "non_utf8_content": 1,
        "generated": 1,
        "file_too_large": 1,
        "lfs_pointer": 1,
    }
    assert f"{5:040x}" not in source.read_objects
    assert f"{6:040x}" not in source.read_objects


@pytest.mark.parametrize(
    "raw,normalized,lines",
    [
        (b"a\r\nb\r", "a\nb\n", 2),
        (b"a\nb", "a\nb", 2),
        (b"", "", 0),
        (b"\n", "\n", 1),
        (b"\xef\xbb\xbfhello\n", "\ufeffhello\n", 1),
    ],
)
def test_normalized_text_hashes_and_line_counts(raw, normalized, lines):
    file = scan(MemorySource([("readme.txt", "100644", raw)])).files[0]
    assert file.content == normalized
    assert file.raw_hash == hashlib.sha256(raw).hexdigest()
    assert file.content_hash == hashlib.sha256(normalized.encode()).hexdigest()
    assert file.line_count == lines
    assert file.size_bytes == len(raw)


@pytest.mark.parametrize("settings", [{"max_repository_files": 1}, {"max_source_bytes": 1}])
def test_repository_limits_fail_instead_of_publishing_partial_results(settings):
    source = MemorySource([("a.py", "100644", b"abc"), ("b.py", "100644", b"def")], **settings)
    with pytest.raises(IngestionError):
        scan(source)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a\\b", "a/../../b"])
def test_unsafe_paths_are_excluded(path):
    assert excluded_path(path, "100644", 1, Settings(_env_file=None)) == "unsupported_path"


def test_policy_changes_create_a_distinct_index_version():
    first = Settings(_env_file=None)
    second = Settings(_env_file=None, max_file_bytes=12)
    assert index_version(first) != index_version(second)


def test_scan_real_git_objects_without_checkout(tmp_path):
    source = GitSource(tmp_path, Settings(_env_file=None), time.monotonic() + 20)
    source.run(["init", "--bare", "--template=", str(source.repository)])
    blob = (
        subprocess.run(
            ["git", "--git-dir", str(source.repository), "hash-object", "-w", "--stdin"],
            input=b"print('source only')\r\n",
            capture_output=True,
            check=True,
            env=git_environment(),
        )
        .stdout.decode()
        .strip()
    )
    source.git("update-index", "--add", "--cacheinfo", "100644", blob, "src/main.py")
    tree = source.git("write-tree").decode().strip()
    source.commit_sha = (
        source.run(
            [
                "--git-dir",
                str(source.repository),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit-tree",
                tree,
                "-m",
                "Source fixture",
            ]
        )
        .decode()
        .strip()
    )
    result = scan(source)
    assert result.files[0].path == "src/main.py"
    assert result.files[0].content == "print('source only')\n"
    assert not (tmp_path / "src").exists()


def test_git_runner_checks_disk_and_output_limits(tmp_path):
    source = GitSource(tmp_path, Settings(_env_file=None), time.monotonic() + 10)
    with pytest.raises(IngestionError, match="output"):
        source.run(["--version"], output_limit=1)
    (tmp_path / "large").write_bytes(b"x" * 100)
    source.settings = Settings(_env_file=None, max_clone_bytes=10)
    with pytest.raises(IngestionError, match="CLONE_BYTES"):
        source.run(["--version"])


def test_expired_deadline_starts_no_process(tmp_path, monkeypatch):
    source = GitSource(tmp_path, Settings(_env_file=None), time.monotonic() - 1)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("Started a process"))
    with pytest.raises(IngestionError, match="time limit"):
        source.run(["--version"])


def test_running_git_timeout_kills_group_and_reaps_process(tmp_path, monkeypatch):
    class RunningProcess:
        pid = 123456
        waited = False

        def poll(self):
            return None

        def wait(self):
            self.waited = True

    process = RunningProcess()
    killed = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append(pid))
    source = GitSource(tmp_path, Settings(_env_file=None), time.monotonic() + 10)
    with pytest.raises(IngestionError, match="time limit"):
        source.run(["--version"], timeout=0)
    assert killed == [process.pid]
    assert process.waited


def test_failed_fetch_cleans_up_workspace(monkeypatch):
    locations = []

    def fail_fetch(self, args, **kwargs):
        locations.append(self.workspace)
        (self.workspace / "partial-pack").write_bytes(b"partial")
        if "fetch" in args:
            raise IngestionError("Simulated interrupted fetch")
        return b""

    monkeypatch.setattr(GitSource, "run", fail_fetch)
    with pytest.raises(IngestionError, match="interrupted"):
        with acquire("https://github.com/pypa/sampleproject", "HEAD", Settings(_env_file=None)):
            pytest.fail("Yielded an incomplete repository")
    assert locations and all(not Path(location).exists() for location in locations)
