# Python parsing and source chunks

Step 4 turns stored files into searchable units with exact source locations. It does not run Python, resolve calls across files, create embeddings, or answer questions yet.

## Try it

From `backend/`:

```sh
uv run alembic upgrade head
uv run repo-copilot parse YOUR_SNAPSHOT_ID
```

Replace `YOUR_SNAPSHOT_ID` with the `snapshot_id` returned by `repo-copilot ingest`. The command reports file, symbol, import, and chunk counts, along with fallback counts and a parsing-run ID. It reads the stored source and needs neither a network connection nor a model API key.

Repeating the command with the same parser and chunk settings reuses the completed run. Changing the chunk budget or parser/chunker version creates another run while preserving earlier results and original source. Snapshots remain `ingested`, and `ready_for_qa` is false until the later indexing stages exist.

## Why these files exist

| File | Purpose |
| --- | --- |
| `parser.py` | Uses Python's built-in AST parser to identify classes, functions, methods, simple assignment declarations, and imports. It stores syntax and line locations without claiming to know runtime behavior. |
| `chunker.py` | Partitions source by its enclosing definition, keeping methods separate from class context. Every chunk is an exact source slice with a hash and inclusive line references. |
| `parsing.py` | Stores a complete versioned parsing run and its chunks in one transaction. Errors or resource-limit failures cannot publish a partially completed run. |

## Symbols and imports

Symbols have qualified names such as `Service.create`, a kind, a parent name, and one-based start/end lines. Decorators are included in definition spans; nested functions retain their enclosing name, and async methods are recognized as methods.

Assignments to simple names, including tuple/list unpacking and annotations, are recorded as syntactic declarations. This is not full Python binding analysis: attribute writes, dynamic assignments, and `global`/`nonlocal` resolution are not inferred. Imports retain module/name, alias, relative level, source lines, and enclosing scope; no module or reference resolution is performed.

## Chunk boundaries and size

A class contributes its own header, documentation, and fields as context. Its methods get separate chunks, and nested functions get their own chunks. Parent context can therefore occupy several separate source ranges rather than duplicating a whole class or function.

Large spans split at line boundaries where possible. A line larger than the budget is split at Unicode character boundaries; zero-based character offsets (`start_char`, exclusive `end_char`) identify the exact substring, while line references still point to the original source. Joining a file's chunks in ordinal order reconstructs all its stored text, including whitespace.

`COPILOT_MAX_CHUNK_TOKENS` defaults to 1024. Until an embedding tokenizer is selected, the implementation uses UTF-8 byte length as a conservative upper bound for byte-level tokenizers. `token_upper_bound` is **not** an actual model token count, and the budget covers source text only; later embedding adapters must count their full payload with the selected provider's tokenizer. This keeps Step 4 offline and avoids guessing tokens from character counts.

## Fallbacks and versions

- Python parsed successfully: `parsed`, with symbols/imports and definition-aware chunks.
- Invalid or too-deep Python: `parse_error`, a concise diagnostic, and bounded text chunks.
- Other languages, README files, and configuration: `text_fallback`, with bounded text chunks.
- Empty files: recorded normally, with no chunks.

The `parsing_runs` table stores per-file status, diagnostic, symbols, imports, and summary counts. `code_chunks` stores ordered source slices and enclosing-symbol metadata; file paths/languages and snapshot identity are reached through their file/run relationships. The earlier `repository_files.parse_status` and snapshot ingestion coverage remain ingestion-time metadata; per-run status is authoritative for parsing.

Parser identity includes the implementation version and Python runtime minor version because supported syntax can differ. Chunker version, counting policy, and configured budget also contribute to the pipeline fingerprint. A source behavior change requires the corresponding version constant to be updated.

`COPILOT_MAX_SNAPSHOT_CHUNKS` defaults to 50,000 and stops splitting before creating excessive chunk objects. `COPILOT_PARSING_TIMEOUT_SECONDS` defaults to 180; deadlines are checked between parsing/chunking/database stages, and database statements use the remaining time. Like ingestion, this local prototype is not an operating-system resource sandbox.

The focused tests exercise source reconstruction, Unicode boundaries, decorators, nested scopes, fallbacks, metadata, and transactional versioning. AST locations follow [Python's AST documentation](https://docs.python.org/3/library/ast.html).
