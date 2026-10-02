"use client";
import {
  ChevronDown,
  ChevronRight,
  FileCode2,
  Folder,
  FolderOpen,
  LoaderCircle,
  ArrowLeft,
  ArrowRight,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, errorText } from "@/lib/api";
import type { Entry, Selection, Source, Tree } from "@/lib/types";

function Directory({
  snapshot,
  path,
  onFile,
  selected,
  root = false,
}: {
  snapshot: string;
  path: string;
  onFile: (s: Selection) => void;
  selected?: string;
  root?: boolean;
}) {
  const [open, setOpen] = useState(root);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [offset, setOffset] = useState<number | null>(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const loaded = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  async function load(next: number) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<Tree>(
        `/snapshots/${snapshot}/tree?path=${encodeURIComponent(path)}&limit=100&offset=${next}`,
      );
      if (!mounted.current) return;
      setEntries((old) =>
        next ? [...old, ...result.entries] : result.entries,
      );
      setOffset(result.next_offset);
      loaded.current = true;
    } catch (e) {
      if (mounted.current) setError(errorText(e));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  useEffect(() => {
    if (open && !loaded.current) void load(0);
  }, [open]); // One fetch per expanded directory.
  return (
    <>
      {!root && (
        <button
          className="tree-row directory"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          {open ? <FolderOpen size={15} /> : <Folder size={15} />}
          <span>{path.split("/").at(-1)}</span>
        </button>
      )}
      {open && (
        <ul className={root ? "tree-list" : "tree-list nested"}>
          {entries.map((entry) => (
            <li key={entry.path}>
              {entry.type === "directory" ? (
                <Directory
                  snapshot={snapshot}
                  path={entry.path}
                  onFile={onFile}
                  selected={selected}
                />
              ) : (
                <button
                  className={`tree-row ${selected === entry.path ? "selected" : ""}`}
                  disabled={entry.status !== "stored"}
                  title={
                    entry.reason
                      ? `Excluded: ${entry.reason.replaceAll("_", " ")}`
                      : entry.path
                  }
                  aria-current={selected === entry.path ? "true" : undefined}
                  onClick={() =>
                    onFile({ snapshot, path: entry.path, start: 1 })
                  }
                >
                  <FileCode2 size={15} />
                  <span>{entry.path.split("/").at(-1)}</span>
                  {entry.status !== "stored" && (
                    <span className="excluded-tag">excluded</span>
                  )}
                </button>
              )}
            </li>
          ))}
          {busy && (
            <li className="inline-loading">
              <LoaderCircle className="spin" size={14} />
              Loading files…
            </li>
          )}
          {error && (
            <li className="mini-error" role="alert">
              {error}
              <button onClick={() => void load(offset || 0)}>
                Retry files
              </button>
            </li>
          )}
          {!busy && !error && loaded.current && !entries.length && (
            <li className="muted pad">No files in this folder.</li>
          )}
          {!busy && loaded.current && offset !== null && (
            <li>
              <button className="text-button" onClick={() => void load(offset)}>
                Load more files
              </button>
            </li>
          )}
        </ul>
      )}
    </>
  );
}
export function FileTree(props: {
  snapshot: string;
  onFile: (s: Selection) => void;
  selected?: string;
}) {
  return (
    <nav aria-label="Repository files" className="file-tree">
      <Directory key={props.snapshot} {...props} path="" root />
    </nav>
  );
}
export function CodeViewer({
  selection,
  onPage,
}: {
  selection: Selection | null;
  onPage: (s: Selection) => void;
}) {
  const [source, setSource] = useState<Source | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const highlight = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let current = true;
    setSource(null);
    setError("");
    if (!selection) return;
    setBusy(true);
    const params = new URLSearchParams({
      path: selection.path,
      start_line: String(selection.start),
    });
    // Backend bounds each source response; pagination never fetches outside the pinned snapshot.
    api<Source>(`/snapshots/${selection.snapshot}/files?${params}`)
      .then((s) => {
        if (current) setSource(s);
      })
      .catch((e) => {
        if (current) setError(errorText(e));
      })
      .finally(() => {
        if (current) setBusy(false);
      });
    return () => {
      current = false;
    };
  }, [selection, retry]);
  useEffect(() => {
    highlight.current?.scrollIntoView({ block: "nearest" });
  }, [source]);
  if (!selection)
    return (
      <div className="source-empty">
        <div className="outline-icon">
          <FileCode2 size={30} strokeWidth={1.3} />
        </div>
        <h2>The source of the answer.</h2>
        <p>
          Open a file or choose a citation to inspect the code behind an
          explanation.
        </p>
        <span className="mono muted">Read-only · pinned to a commit</span>
      </div>
    );
  return (
    <section className="source-view" aria-label="Source code">
      <div className="source-toolbar">
        <FileCode2 size={16} />
        <span className="mono file-path" title={selection.path}>
          {selection.path}
        </span>
        {source && (
          <span className="source-range">
            {source.start_line === null
              ? "Empty file"
              : `L${source.start_line}–${source.end_line}`}
          </span>
        )}
      </div>
      {selection.end !== undefined && (
        <div className="citation-banner">
          Cited evidence · lines {selection.start}–{selection.end}
        </div>
      )}
      {busy && (
        <div className="source-notice" role="status">
          <LoaderCircle className="spin" size={16} />
          Loading stored source…
        </div>
      )}
      {error && (
        <div className="source-notice" role="alert">
          {error}
          <button onClick={() => setRetry(retry + 1)}>Retry source</button>
        </div>
      )}
      {source && (
        <div
          className="code-scroll"
          tabIndex={0}
          aria-label={`Code in ${selection.path}`}
        >
          {source.start_line === null ? (
            <p className="source-notice">This file is empty.</p>
          ) : (
            source.content.split("\n").map((line, i) => {
              const number = source.start_line! + i;
              const selected =
                selection.end !== undefined &&
                number >= selection.start &&
                number <= selection.end;
              return (
                <div
                  key={number}
                  ref={
                    number === selection.start && selected
                      ? highlight
                      : undefined
                  }
                  className={`code-line ${selected ? "highlighted" : ""}`}
                  data-line={number}
                >
                  <span className="line-number" aria-hidden="true">
                    {number}
                  </span>
                  <code>{line || " "}</code>
                </div>
              );
            })
          )}
        </div>
      )}
      {source && (
        <div className="source-footer">
          <span>
            {source.truncated
              ? source.end_line === selection.start
                ? "This line exceeds the display limit; only its beginning is shown."
                : "Showing a bounded excerpt. More source is available."
              : "End of stored file"}
          </span>
          <div className="button-group">
            <button
              aria-label="Previous source excerpt"
              disabled={selection.start <= 1}
              onClick={() =>
                onPage({
                  ...selection,
                  start: Math.max(1, selection.start - 100),
                  end: undefined,
                })
              }
            >
              <ArrowLeft size={14} />
            </button>
            <button
              aria-label="Next source excerpt"
              disabled={
                !source.truncated ||
                source.end_line === null ||
                source.end_line === selection.start
              }
              onClick={() =>
                onPage({
                  ...selection,
                  start: source.end_line!,
                  end: selection.end,
                })
              }
            >
              <ArrowRight size={14} />
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
