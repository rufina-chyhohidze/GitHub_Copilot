# ingestion

Step 3 implements acquisition and scanning. Parsing, chunking, and embeddings come next; an ingested snapshot is not yet ready for questions.

| File | Why it exists |
| --- | --- |
| `clone.py` | Validates public GitHub URLs and fetches one commit into a temporary bare repository. Reading objects directly avoids executing checkout hooks, filters, or repository scripts. |
| `scanner.py` | Builds the source manifest and explains exclusions. Stable paths, hashes, and line counts give future citations a reliable foundation. |
| `service.py` | Stores the manifest and source files in one database transaction. Uniqueness constraints reuse an existing snapshot without overwriting its source. |

## Run ingestion

From `backend/`, with the local database running:

```sh
uv run alembic upgrade head
uv run repo-copilot ingest https://github.com/pypa/sampleproject \
  --ref 621e4974ca25ce531773def586ba3ed8e736b3fc
```

`--ref` defaults to the remote `HEAD`; branch names, tag names, full refs, and commit SHAs are accepted. The resolved commit SHA is stored, so later branch movement never changes an existing snapshot. URLs with credentials, queries, non-GitHub hosts, alternate protocols, or `/tree/...` paths are rejected; pass the branch separately.

The JSON response includes repository/snapshot IDs, commit SHA, index version, inclusion/exclusion counts, and `reused`. Running the same command twice reuses the snapshot; it still fetches/scans to resolve the requested ref and enforce the current limits.

## What is stored

- `repositories`: canonical URL and creation time.
- `repository_snapshots`: immutable commit identity, ingestion policy version, `ingested` status, timestamps, coverage, and a per-path manifest.
- `repository_files`: supported UTF-8 text, relative path, language, raw byte size, original-byte SHA-256, stored-text SHA-256, line count, and pending parse status.

These records survive temporary-workspace cleanup. No embeddings, chunks, conversations, or jobs are created by this step. Acquisition/scanning failures publish no snapshot; detailed persistent job failures arrive in Step 9.

## Text and exclusion policy

Text is decoded strictly as UTF-8. A UTF-8 BOM is retained, CRLF and standalone CR become LF, and line numbers are one-based with inclusive end lines. Empty files have zero lines; a terminal newline does not add an extra line. Original and normalized text hashes are separate because normalization changes bytes.

Unknown extensions are retained as `text` when decodable. README files, dependency manifests, and configuration are included. The manifest records exclusions for dependency/build directories, lockfiles, generated/minified output, symlinks, submodules, LFS pointers, oversized files, binary content, invalid paths, and non-UTF-8 content or filenames. Generated-file detection is a heuristic based on names and header markers; inspect the manifest if a relevant file appears missing.

Git paths are never used as filesystem read targets: file content is read using object IDs. Symlinks are recorded and excluded, never followed. Git redirects, credential helpers, inherited Git configuration, interactive authentication, checkout, submodule recursion, and LFS downloads are disabled.

## Limits and failure behavior

| Setting | Default | Effect |
| --- | --- | --- |
| `COPILOT_CLONE_TIMEOUT_SECONDS` | 60 | Abort a stalled fetch. |
| `COPILOT_INGESTION_TIMEOUT_SECONDS` | 180 | Bound acquisition, scanning, and persistence work. |
| `COPILOT_MAX_CLONE_BYTES` | 100 MiB | Monitor temporary repository and command-output disk use. |
| `COPILOT_MAX_FILE_BYTES` | 1 MiB | Exclude an oversized file with a recorded reason. |
| `COPILOT_MAX_SOURCE_BYTES` | 20 MiB | Abort if included source exceeds the aggregate limit. |
| `COPILOT_MAX_REPOSITORY_FILES` | 10,000 | Abort if the Git tree exceeds this entry count, including excluded files. |

Git metadata/output has an additional cap. Timeout and disk checks poll running Git processes; disk usage can briefly overshoot between checks, so this is not an operating-system quota or a complete hostile-workload sandbox. A hosted service will need worker/container resource isolation before accepting arbitrary public traffic. The command runner currently targets macOS/Linux.

Failure kills the running Git process group, cleans its unique temporary workspace, and reports a controlled error. Database writes are transactional and statement timeouts respect the remaining ingestion budget. A different file-size policy changes the index version because it changes coverage; scanner behavior changes must bump `SCANNER_VERSION`.

## Verification

The ordinary test suite exercises URL/ref restrictions, object scanning, exclusions, encoding, limits, timeouts, and cleanup without network access. PostgreSQL tests are optional in the default suite and run in unique schemas whose transactions are rolled back afterward.

For the default local development database:

```sh
COPILOT_TEST_DATABASE_URL=postgresql+psycopg://copilot:copilot_local@localhost:5433/copilot \
  uv run pytest tests/test_snapshot_storage.py
```

Use the matching URL if your local database credentials differ. The Git behavior follows the official [fetch documentation](https://git-scm.com/docs/git-fetch) and [tree listing documentation](https://git-scm.com/docs/git-ls-tree).
