# tools

`repository.py` provides tree browsing, bounded file reads, literal search, symbol lookup, and per-file symbols. Every service is tied to a snapshot, and symbol queries also select a parsing run; a later agent will call these same services.

Literal search is case-sensitive by default, supports multiline strings, and never interprets input as regex or shell commands. File reads cap content at 200 lines/16 KiB; every serialized tool result has a 32 KiB ceiling and either reports truncation or requests a narrower query. See the [retrieval guide](../retrieval/README.md) for commands.
