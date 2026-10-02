# Python, JavaScript, and TypeScript parsing

Steps 4 and 8 turn stored files into searchable units with exact source locations. Python uses AST; JavaScript, JSX, TypeScript, and TSX use Tree-sitter. Parsing never executes source or resolves runtime calls. Embedding and answering remain separate stages.

## Try it

From `backend/`:

```sh
uv sync --locked
uv run alembic upgrade head
uv run repo-copilot parse YOUR_SNAPSHOT_ID
```

Replace `YOUR_SNAPSHOT_ID` with the `snapshot_id` returned by `repo-copilot ingest`. The command reports file, symbol, import, and chunk counts, along with fallback counts and a parsing-run ID. It reads the stored source and needs neither a network connection nor a model API key.

Repeating the command with the same parser and chunk settings reuses the completed run. Changing the chunk budget or parser/chunker version creates another run while preserving earlier results and original source. Parsing alone does not publish readiness; its stage summary retains `ready_for_qa: false`. The Step 9 worker publishes a ready index only after embeddings complete. A new parsing run does not invalidate an older published run.

## Why these files exist

| File | Purpose |
| --- | --- |
| `parser.py` | Uses Python's built-in AST parser to identify classes, functions, methods, simple assignment declarations, and imports. It stores syntax and line locations without claiming to know runtime behavior. |
| `javascript.py` | Uses the JavaScript, TypeScript, and TSX Tree-sitter grammars to extract declarations and ES module syntax, recording unsupported extraction forms as limitations. |
| `chunker.py` | Partitions source by its enclosing definition, keeping methods separate from class context. Every chunk is an exact source slice with a hash and inclusive line references. |
| `parsing.py` | Stores a complete versioned parsing run and its chunks in one transaction. Errors or resource-limit failures cannot publish a partially completed run. |

## Symbols and imports

Symbols have qualified names such as `Service.create`, a kind, a parent name, and one-based start/end lines. Decorators are included in definition spans; nested functions retain their enclosing name, and async methods are recognized as methods.

Assignments to simple names, including tuple/list unpacking and annotations, are recorded as syntactic declarations. This is not full Python binding analysis: attribute writes, dynamic assignments, and `global`/`nonlocal` resolution are not inferred. Imports retain module/name, alias, relative level, source lines, and enclosing scope; no module or reference resolution is performed.

## JavaScript and TypeScript

The JavaScript grammar handles `.js`, `.mjs`, `.cjs`, and `.jsx`. Stored `.ts` files use the TypeScript grammar; `.tsx` paths select TSX even though ingestion labels both as `typescript`. The path is passed into parsing so JSX does not change TypeScript assertion syntax. Direct parser callers can also specify `jsx` or `tsx` as the language.

Source locations are calculated from UTF-8 byte offsets and LF boundaries, avoiding native `Point` access. This works around crashes consistent with the [reported Tree-sitter 0.26 Point memory corruption issue](https://github.com/tree-sitter/py-tree-sitter/issues/487). The `syntax-v3` parser identity requires a fresh parsing run; existing published runs remain available. Chunking rejects symbol spans outside the stored source bounds instead of creating invalid citations.

Extraction includes named functions and generators, classes, methods, simple variables, assigned arrow/function expressions, class fields, interfaces, type aliases, enums, and namespaces. Nested declarations retain qualified parent names. A directly exported anonymous function/class has the synthetic symbol name `default`; its export record has no local name. Default exports of identifiers retain their source name. Decorator and export prefixes are included where they belong to a declaration's span.

ES imports and exports preserve aliases, literal module specifiers, one-based inclusive spans, namespace/star forms, and `type_only` flags. Import `name: default` identifies a default binding; `name: null` with no alias identifies a side-effect import. Export `name` identifies the source binding and `alias` the exported name. JS relative paths remain in `module` with `level: 0`; Python relative-import levels keep their existing meaning. Module strings preserve source escape spelling. Every import/export has `resolution: unresolved`: no filesystem, package, alias, binding, or reference resolution is claimed.

`repo-copilot symbols SNAPSHOT_ID PATH` exposes symbols, imports, exports, and limitations, with bounded lists and a truncation flag. Older parsing runs without exports/limitations remain readable. CommonJS require/export forms, dynamic imports, destructured declarations, computed/literal method names, anonymous callbacks, and TypeScript import-equals/export-assignment forms remain searchable source text with limitation notes when encountered. Anonymous callback bodies are not assigned invented lexical scopes. Extraction is intentionally narrower than the grammar and is not a type checker or full binding analysis.

## Chunk boundaries and size

A class contributes its own header, documentation, and fields as context. Its methods get separate chunks, and nested functions get their own chunks. Parent context can therefore occupy several separate source ranges rather than duplicating a whole class or function.

TypeScript interfaces, enums, and namespaces also provide chunk ownership. Ownership is line-based: declarations sharing a physical line cannot have fully separate chunks, and the later visited declaration owns that line. Symbol spans still identify each declaration's source lines, and concatenated chunks preserve the entire file exactly.

Large spans split at line boundaries where possible. A line larger than the budget is split at Unicode character boundaries; zero-based character offsets (`start_char`, exclusive `end_char`) identify the exact substring, while line references still point to the original source. Joining a file's chunks in ordinal order reconstructs all its stored text, including whitespace.

`COPILOT_MAX_CHUNK_TOKENS` defaults to 1024. Until an embedding tokenizer is selected, the implementation uses UTF-8 byte length as a conservative upper bound for byte-level tokenizers. `token_upper_bound` is **not** an actual model token count, and the budget covers source text only; later embedding adapters must count their full payload with the selected provider's tokenizer. This keeps Step 4 offline and avoids guessing tokens from character counts.

## Fallbacks and versions

- Supported language parsed successfully: `parsed`, with syntax metadata and definition-aware chunks. JS/TS limitations may be nonempty even when the grammar accepts the source.
- Invalid or too-deep source: `parse_error`, a concise diagnostic, and bounded text chunks. Tree-sitter error recovery is not published as authoritative partial symbols.
- Other languages, README files, and configuration: `text_fallback`, with bounded text chunks.
- Empty files: recorded normally, with no chunks.

The `parsing_runs` table stores per-file status, diagnostic, symbols, imports, exports, limitations, and summary counts including `files_with_limitations`. New metadata fits the existing JSON columns; no database migration is needed. `code_chunks` stores ordered source slices and enclosing-symbol metadata; file paths/languages and snapshot identity are reached through their file/run relationships. The earlier `repository_files.parse_status` and snapshot ingestion coverage remain ingestion-time metadata; per-run status is authoritative for parsing.

Parser identity includes the implementation version, Python runtime minor version, and installed Tree-sitter core/grammar versions. The compact `ts`, `js`, and `tsx` labels refer to core, JavaScript grammar, and TypeScript/TSX grammar versions respectively. Chunker version, counting policy, and configured budget also contribute to the pipeline fingerprint. A source behavior change requires the corresponding version constant to be updated. Reparse existing snapshots to create new runs; old runs, chunks, and cited source remain available. Embedding indexes are scoped to their parsing run and must be built for a new run before hybrid search uses it.

`COPILOT_MAX_SNAPSHOT_CHUNKS` defaults to 50,000 and stops splitting before creating excessive chunk objects. `COPILOT_PARSING_TIMEOUT_SECONDS` defaults to 180; deadlines are checked between parsing/chunking/database stages, and database statements use the remaining time. Like ingestion, this local prototype is not an operating-system resource sandbox.

The focused tests exercise source reconstruction, Unicode boundaries, decorators, nested scopes, fallbacks, metadata, and transactional versioning. AST locations follow [Python's AST documentation](https://docs.python.org/3/library/ast.html).

Step 8 adds `tests/fixtures/web_shop`, parser/storage/citation tests, and six versioned questions in `evals/datasets/web-qa-v1.json` (five development, one held-out). Verify fixture hashes and evidence offline from `backend/`:

```sh
uv run repo-copilot-eval --dataset ../evals/datasets/web-qa-v1.json --require-all
```

The same dataset can be passed to `repo-copilot-answer-eval --dataset ...`; model quality for these cases has not been measured. Existing Python evaluation data is unchanged.

Implementation references: [Tree-sitter Python bindings](https://github.com/tree-sitter/py-tree-sitter), [JavaScript grammar](https://github.com/tree-sitter/tree-sitter-javascript), and [TypeScript/TSX grammars](https://github.com/tree-sitter/tree-sitter-typescript).
