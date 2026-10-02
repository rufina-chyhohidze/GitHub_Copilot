"use client";
import {
  ArrowUp,
  Check,
  ChevronDown,
  MessageSquarePlus,
  Sparkles,
  Square,
  Terminal,
  ExternalLink,
  LoaderCircle,
  RotateCcw,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  api,
  errorText,
  loadConversation,
  pendingRequest,
  post,
  store,
  terminal,
} from "@/lib/api";
import { SavedActivity } from "./activity";
import type {
  Citation,
  Conversation,
  Pending,
  Run,
  Selection,
} from "@/lib/types";

function CitationButton({
  citation,
  onSource,
}: {
  citation: Citation;
  onSource: (s: Selection) => void;
}) {
  const s = citation.source;
  return (
    <button
      className="citation-chip"
      title={`Open ${s.path}, lines ${s.start_line}–${s.end_line}`}
      aria-label={`Open ${s.path}, lines ${s.start_line}–${s.end_line}`}
      onClick={() =>
        onSource({
          snapshot: s.snapshot_id,
          path: s.path,
          start: s.start_line,
          end: s.end_line,
        })
      }
    >
      <FileMark />
      <span>{s.path.split("/").at(-1)}</span>
      <span className="mono">
        L{s.start_line}
        {s.end_line !== s.start_line ? `–${s.end_line}` : ""}
      </span>
    </button>
  );
}
function FileMark() {
  return (
    <span className="citation-mark" aria-hidden="true">
      ↗
    </span>
  );
}
const phase = (value: string) =>
  ({
    queued: "Waiting for the answer worker…",
    running: "Investigating the repository…",
    generating: "Writing and checking the answer…",
    reconnecting: "Connection lost. Reconnecting to this answer…",
    cancelled: "Answer cancelled.",
    interrupted: "The answer worker stopped. Send a new question to try again.",
    failed:
      "The answer could not be validated. Try again or check the answer worker.",
  })[value] || value;

function LiveRun({
  id,
  onDone,
  onError,
}: {
  id: string;
  onDone: () => void;
  onError: (s: string) => void;
}) {
  const [state, setState] = useState("queued");
  const [prose, setProse] = useState("");
  const [activity, setActivity] = useState<
    { id: number; tool: string; status: string }[]
  >([]);
  const [cancelling, setCancelling] = useState(false);
  const [stopped, setStopped] = useState(false);
  const callbacks = useRef({ onDone, onError });
  callbacks.current = { onDone, onError };
  useEffect(() => {
    let alive = true,
      done = false,
      last = 0,
      polling = false;
    const stream = new EventSource(`/api/runs/${id}/events`);
    const complete = (status: string) => {
      if (done || !alive) return;
      done = true;
      stream.close();
      setStopped(true);
      setProse("");
      if (status !== "completed") callbacks.current.onError(phase(status));
      callbacks.current.onDone();
    };
    const handler = (kind: string) => (raw: Event) => {
      if (!(raw instanceof MessageEvent) || !alive) return;
      const sequence = Number(raw.lastEventId);
      if (!Number.isSafeInteger(sequence) || sequence <= last) return;
      let value;
      try {
        value = JSON.parse(raw.data);
      } catch {
        return;
      }
      last = sequence;
      if (kind === "status") setState(value.status);
      if (kind === "answer_delta") setProse((old) => old + value.text);
      if (kind === "tool_started" || kind === "tool_finished") {
        setActivity((old) => [
          ...old.slice(-99),
          {
            id: sequence,
            tool: value.tool,
            status: kind === "tool_started" ? "started" : value.status,
          },
        ]);
      }
      if (kind === "done") complete(value.status);
    };
    for (const name of [
      "status",
      "tool_started",
      "tool_finished",
      "answer_delta",
      "citation",
      "error",
      "done",
    ])
      stream.addEventListener(name, handler(name));
    stream.onerror = (event) => {
      if (!(event instanceof MessageEvent) && alive && !done)
        setState("reconnecting");
    };
    // Also recover terminal state if a proxy closes the stream or the client missed done.
    const timer = setInterval(async () => {
      if (polling || done) return;
      polling = true;
      try {
        const run = await api<Run>(`/runs/${id}`);
        if (alive && terminal(run.status)) complete(run.status);
      } catch {
        /* EventSource keeps retrying without resubmitting the message. */
      } finally {
        polling = false;
      }
    }, 3000);
    return () => {
      alive = false;
      stream.close();
      clearInterval(timer);
    };
  }, [id]);
  async function cancel() {
    setCancelling(true);
    try {
      await post(`/runs/${id}/cancel`);
    } catch (e) {
      onError(errorText(e));
      setCancelling(false);
    }
  }
  return (
    <div className="live-answer">
      <div className="live-status" role="status">
        {!stopped && <LoaderCircle size={15} className="spin" />}
        {stopped ? "Refreshing conversation…" : phase(state)}
      </div>
      <details className="activity" open={undefined}>
        <summary>
          <Terminal size={14} />
          Repository activity <span>{activity.length}</span>
          <ChevronDown size={13} />
        </summary>
        <ul>
          {activity.map((event) => (
            <li key={event.id}>
              <span
                className={
                  event.status === "completed" ? "dot complete" : "dot"
                }
              />
              <code>{event.tool.replaceAll("_", " ")}</code>
              <span>{event.status}</span>
            </li>
          ))}
        </ul>
        {!activity.length && (
          <p className="muted">Activity will appear when the worker starts.</p>
        )}
      </details>
      {prose && (
        <div className="provisional">
          <span className="eyebrow">
            Provisional · awaiting final publication
          </span>
          <p>{prose}</p>
        </div>
      )}
      {!stopped && (
        <button
          className="text-button"
          disabled={cancelling}
          onClick={() => void cancel()}
        >
          <Square size={12} />
          {cancelling ? "Cancelling…" : "Cancel answer"}
        </button>
      )}
    </div>
  );
}

export function Chat({
  id,
  onSource,
  onNew,
}: {
  id: string;
  onSource: (s: Selection) => void;
  onNew: () => void;
}) {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [question, setQuestion] = useState("");
  const [pipeline, setPipeline] = useState<"fixed" | "agent">("fixed");
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const mounted = useRef(true);
  const sending = useRef(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const refresh = useCallback(async () => {
    try {
      const result = await loadConversation(id);
      if (mounted.current) {
        setConversation(result);
        setError("");
      }
    } catch (e) {
      if (mounted.current) setError(errorText(e));
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [id]);
  useEffect(() => {
    mounted.current = true;
    setPending(pendingRequest(id));
    void refresh();
    return () => {
      mounted.current = false;
    };
  }, [id, refresh]);
  const active = conversation?.runs.find((r) => !terminal(r.status));
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [conversation?.messages.length]);
  async function submit(retry = false) {
    if (sending.current || active || (!retry && !question.trim())) return;
    sending.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    const request: Pending =
      retry && pending
        ? pending
        : {
            conversation: id,
            key: crypto.randomUUID(),
            question: question.trim(),
            pipeline,
          };
    store(`pending:${id}`, request);
    setPending(request);
    try {
      await post(
        `/conversations/${id}/messages`,
        { question: request.question, pipeline: request.pipeline },
        { "Idempotency-Key": request.key },
      );
      store(`pending:${id}`, null);
      if (mounted.current) {
        setPending(null);
        setQuestion("");
        await refresh();
      }
    } catch (e) {
      if (mounted.current) setError(errorText(e));
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        store(`pending:${id}`, null);
        if (mounted.current) {
          setPending(null);
          void refresh();
        }
      }
    } finally {
      sending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  const byteLength = new TextEncoder().encode(question.trim()).length;
  return (
    <section
      id="panel-chat"
      className="chat-panel"
      aria-label="Repository conversation"
    >
      <div className="panel-heading">
        <div>
          <Sparkles size={17} />
          <h2>Ask the repository</h2>
        </div>
        <button
          className="icon-button"
          title="Start a new conversation"
          aria-label="Start a new conversation"
          onClick={onNew}
        >
          <MessageSquarePlus size={17} />
        </button>
      </div>
      <div className="chat-scroll">
        {loading && (
          <p className="inline-loading" role="status">
            <LoaderCircle size={16} className="spin" />
            Restoring conversation…
          </p>
        )}
        {!loading && conversation && !conversation.messages.length && (
          <div className="chat-welcome">
            <span className="spark-tile">
              <Sparkles size={23} />
            </span>
            <h3>A little context goes a long way.</h3>
            <p>
              Ask how something works. Follow the answer back to the source.
            </p>
            {[
              "Where does this application start?",
              "How is authentication handled?",
              "How is this repository organized?",
            ].map((q) => (
              <button
                className="suggestion"
                key={q}
                onClick={() => {
                  setQuestion(q);
                  textarea.current?.focus();
                }}
              >
                {q}
                <ArrowUp size={14} />
              </button>
            ))}
          </div>
        )}
        {conversation?.messages.map((message) => (
          <article key={message.id} className={`message ${message.role}`}>
            <div className="message-label">
              {message.role === "user" ? (
                <>
                  <span className="avatar">Y</span>You
                </>
              ) : (
                <>
                  <span className="avatar copilot">
                    <Sparkles size={13} />
                  </span>
                  Copilot
                  <span className="verified">
                    <Check size={11} />
                    Citations validated
                  </span>
                </>
              )}
            </div>
            {message.role === "user" ? (
              <p>{message.content.text}</p>
            ) : (
              <>
                {message.content.answer?.claims.map((claim, index) => (
                  <div className="claim" key={index}>
                    <p>{claim.text}</p>
                    <div className="citation-list">
                      {[...new Set(claim.evidence_ids)].map((eid) => {
                        const citation = message.content.citations?.find(
                          (c) => c.id === eid,
                        );
                        return citation ? (
                          <CitationButton
                            key={eid}
                            citation={citation}
                            onSource={onSource}
                          />
                        ) : null;
                      })}
                    </div>
                  </div>
                ))}
                {message.content.answer?.uncertainty && (
                  <div className="uncertainty">
                    <span className="eyebrow">What remains uncertain</span>
                    <p>{message.content.answer.uncertainty}</p>
                  </div>
                )}
                {message.content.run_id && (
                  <SavedActivity run={message.content.run_id} />
                )}
                {!!message.content.citations?.length && (
                  <details className="source-links">
                    <summary>
                      View sources on GitHub <ExternalLink size={11} />
                    </summary>
                    {message.content.citations
                      .filter((c) => c.url.startsWith("https://github.com/"))
                      .map((c) => (
                        <a
                          key={c.id}
                          href={c.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {c.source.path} · L{c.source.start_line}–
                          {c.source.end_line}
                        </a>
                      ))}
                  </details>
                )}
              </>
            )}
            {message.role === "user" &&
              conversation.runs
                .filter(
                  (r) =>
                    r.input_message_id === message.id &&
                    terminal(r.status) &&
                    r.status !== "completed",
                )
                .map((run) => (
                  <p className="run-outcome" key={run.id}>
                    {phase(run.status)}
                  </p>
                ))}
          </article>
        ))}
        {active && (
          <LiveRun
            key={active.id}
            id={active.id}
            onDone={() => void refresh()}
            onError={setNotice}
          />
        )}
        {notice && (
          <p className="notice" role="status">
            {notice}
          </p>
        )}
        {error && (
          <div className="error-box" role="alert">
            {error}
            <button className="text-button" onClick={() => void refresh()}>
              <RotateCcw size={13} />
              Reload conversation
            </button>
          </div>
        )}
        {pending && !busy && (
          <div className="notice">
            <p>
              Delivery of your question is unconfirmed. Retry safely using the
              same request.
            </p>
            <p className="mono">{pending.question}</p>
            <button
              className="secondary"
              disabled={!!active}
              onClick={() => void submit(true)}
            >
              Retry question
            </button>
            {active && (
              <button
                className="text-button"
                onClick={() => {
                  store(`pending:${id}`, null);
                  setPending(null);
                }}
              >
                Dismiss saved request
              </button>
            )}
          </div>
        )}
        <div ref={bottom} />
      </div>
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label className="sr-only" htmlFor="question">
          Ask a question about this repository
        </label>
        <textarea
          ref={textarea}
          id="question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask about this codebase…"
          rows={3}
          disabled={loading || !!active || busy || !!pending}
          aria-describedby="question-help"
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              if (byteLength <= 512) void submit();
            }
          }}
        />
        <div className="composer-tools">
          <label>
            <span className="sr-only">Answer mode</span>
            <select
              aria-label="Answer mode"
              value={pipeline}
              onChange={(e) => setPipeline(e.target.value as "fixed" | "agent")}
              disabled={!!active || busy || !!pending}
            >
              <option value="fixed">Standard</option>
              <option value="agent">Investigate</option>
            </select>
          </label>
          <button
            className="send-button"
            type="submit"
            aria-label="Send question"
            disabled={
              loading ||
              busy ||
              !!active ||
              !!pending ||
              !question.trim() ||
              byteLength > 512
            }
          >
            {busy ? (
              <LoaderCircle size={17} className="spin" />
            ) : (
              <ArrowUp size={19} />
            )}
          </button>
        </div>
        <p
          id="question-help"
          className={byteLength > 512 ? "field-error" : "composer-hint"}
        >
          {byteLength > 512
            ? "Shorten your question to 512 UTF-8 bytes."
            : pipeline === "agent"
              ? "Investigate uses additional searches and may take longer."
              : "Answers are grounded in this saved commit. ⌘ / Ctrl + Enter to send."}
        </p>
      </form>
    </section>
  );
}
