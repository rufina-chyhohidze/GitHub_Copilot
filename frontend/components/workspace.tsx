"use client";
import {
  ArrowRight,
  Check,
  ChevronRight,
  CircleHelp,
  ExternalLink,
  FileCode2,
  GitBranch,
  Github,
  Layers,
  LoaderCircle,
  MessageSquare,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { allPages, api, errorText, post, recall, store } from "@/lib/api";
import type {
  Conversation,
  Job,
  Repository,
  Selection,
  Snapshot,
} from "@/lib/types";
import { Chat } from "./chat";
import { Home } from "./home";
import {
  DeleteRepositoryDialog,
  type SavedRepository,
} from "./repository-library";
import { RepoRunner } from "./repo-runner";
import { CodeViewer, FileTree } from "./files";

type Location = {
  repository?: string;
  snapshot?: string;
  conversation?: string;
  job?: string;
};
type Recent = { id: string; name: string };
function readLocation(): Location {
  const params = new URLSearchParams(window.location.search);
  return Object.fromEntries(
    ["repository", "snapshot", "conversation", "job"].flatMap((key) => {
      const value = params.get(key);
      return value && /^[\w-]+$/.test(value) ? [[key, value]] : [];
    }),
  );
}
function shortName(url: string) {
  return url.replace("https://github.com/", "");
}
export default function Workspace() {
  const [location, setLocation] = useState<Location>({});
  const [initialized, setInitialized] = useState(false);
  const [repository, setRepository] = useState<Repository | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [histories, setHistories] = useState<Conversation[]>([]);
  const [conversation, setConversation] = useState<string | null>(null);
  const [recent, setRecent] = useState<Recent[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [mobile, setMobile] = useState("chat");
  const [deleteTarget, setDeleteTarget] = useState<SavedRepository | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [libraryVersion, setLibraryVersion] = useState(0);
  const deleteInFlight = useRef(false);
  function requestDelete(item: SavedRepository) {
    setDeleteError("");
    setDeleteTarget(item);
  }
  async function confirmDelete() {
    if (!deleteTarget || deleteInFlight.current) return;
    deleteInFlight.current = true;
    setDeleting(true);
    setDeleteError("");
    try {
      await api(`/repositories/${deleteTarget.id}`, { method: "DELETE" });
      setRecent((items) => {
        const next = items.filter((item) => item.id !== deleteTarget.id);
        store("repositories", next);
        return next;
      });
      if (
        repository?.id === deleteTarget.id ||
        location.repository === deleteTarget.id
      )
        navigate({});
      setLibraryVersion((value) => value + 1);
      setDeleteTarget(null);
    } catch (reason) {
      setDeleteError(errorText(reason));
    } finally {
      deleteInFlight.current = false;
      setDeleting(false);
    }
  }
  const epoch = useRef(0);
  const operation = useRef(false);
  function navigate(next: Location) {
    const query = new URLSearchParams(
      Object.entries(next).filter(([, value]) => !!value) as [string, string][],
    );
    history.pushState(null, "", query.size ? `/?${query}` : "/");
    store("location", next);
    setLocation(next);
    setError("");
  }
  useEffect(() => {
    const saved = readLocation();
    const initial = Object.keys(saved).length
      ? saved
      : recall<Location>("location") || {};
    setLocation(initial);
    setRecent(recall<Recent[]>("repositories") || []);
    setInitialized(true);
    const pop = () => {
      setLocation(readLocation());
      setError("");
    };
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  useEffect(() => {
    if (!initialized) return;
    const ticket = ++epoch.current;
    setRepository(null);
    setSnapshot(null);
    setConversation(null);
    setSelection(null);
    setHistories([]);
    setSnapshots([]);
    setJob(null);
    if (!location.repository && !location.conversation) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    (async () => {
      let sid = location.snapshot,
        rid = location.repository;
      if (location.conversation) {
        const conv = await api<Conversation>(
          `/conversations/${location.conversation}`,
        );
        sid = conv.snapshot_id;
        if (!rid)
          rid = (await api<Snapshot>(`/snapshots/${sid}`)).repository_id;
      }
      if (!rid) throw new Error("Choose a repository to open this workspace.");
      const [repo, available] = await Promise.all([
        api<Repository>(`/repositories/${rid}`),
        allPages<Snapshot>(`/repositories/${rid}/snapshots`),
      ]);
      const chosen = available.find(
        (s) => s.id === (sid || repo.latest_ready_snapshot_id),
      );
      if (sid && !chosen)
        throw new Error(
          "This saved version does not belong to the selected repository.",
        );
      const ready = chosen?.status === "ready" ? chosen : null;
      const history = ready
        ? await allPages<Conversation>(`/snapshots/${ready.id}/conversations`)
        : [];
      if (ticket !== epoch.current) return;
      setRepository(repo);
      setSnapshots(available.filter((s) => s.status === "ready"));
      setSnapshot(ready);
      setHistories(history.reverse());
      setConversation(location.conversation || null);
      const items = [
        { id: repo.id, name: shortName(repo.canonical_url) },
        ...(recall<Recent[]>("repositories") || []).filter(
          (item) => item.id !== repo.id,
        ),
      ].slice(0, 12);
      setRecent(items);
      store("repositories", items);
      const pinned = {
        ...location,
        repository: repo.id,
        ...(ready ? { snapshot: ready.id } : {}),
      };
      store("location", pinned);
      window.history.replaceState(
        null,
        "",
        `/?${new URLSearchParams(pinned as Record<string, string>)}`,
      );
    })()
      .catch((e) => {
        if (ticket === epoch.current) setError(errorText(e));
      })
      .finally(() => {
        if (ticket === epoch.current) setLoading(false);
      });
  }, [location, initialized, retry]);
  useEffect(() => {
    if (!location.job) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await api<Job>(`/index-jobs/${location.job}`);
        if (!alive) return;
        setJob(next);
        if (next.status === "succeeded" && next.snapshot_id) {
          // Keep an existing conversation pinned; offer the new commit separately.
          if (location.conversation) {
            setRetry((n) => n + 1);
            return;
          }
          navigate({
            repository: next.repository_id,
            snapshot: next.snapshot_id,
          });
          return;
        }
        if (next.status === "failed") return;
      } catch (e) {
        if (alive) setError(errorText(e));
      }
      if (alive) timer = setTimeout(poll, 1500);
    };
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [location.job, location.conversation]);
  async function register(event: React.FormEvent) {
    event.preventDefault();
    if (operation.current) return;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await post<{ repository_id: string; job_id: string }>(
        "/repositories",
        { url: url.trim(), ref: ref.trim() || "HEAD" },
      );
      navigate({ repository: result.repository_id, job: result.job_id });
      setUrl("");
      setRef("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      operation.current = false;
      setBusy(false);
    }
  }
  async function newConversation(sid = snapshot?.id) {
    if (!sid || operation.current) return;
    const ticket = epoch.current;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      const created = await post<Conversation>(
        `/snapshots/${sid}/conversations`,
        {
          title: `Conversation · ${new Date().toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}`,
        },
      );
      if (ticket === epoch.current) {
        navigate({
          repository: repository?.id,
          snapshot: sid,
          conversation: created.id,
        });
        setMobile("chat");
      }
    } catch (e) {
      if (ticket === epoch.current) setError(errorText(e));
    } finally {
      operation.current = false;
      setBusy(false);
    }
  }
  async function reindex() {
    if (!repository || operation.current) return;
    const ticket = epoch.current;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await post<{ job_id: string }>(
        `/repositories/${repository.id}/index`,
        { ref: "HEAD" },
      );
      if (ticket === epoch.current)
        navigate({
          repository: repository.id,
          snapshot: snapshot?.id,
          conversation: conversation || undefined,
          job: result.job_id,
        });
    } catch (e) {
      if (ticket === epoch.current) setError(errorText(e));
    } finally {
      operation.current = false;
      setBusy(false);
    }
  }
  function openSource(next: Selection) {
    setSelection(next);
    setMobile("source");
  }
  const newest = repository?.latest_ready_snapshot_id;
  const indexing = job && !["succeeded", "failed"].includes(job.status);
  const excluded = snapshot?.coverage.excluded_by_reason || {};
  return (
    <div
      className={`app-shell ${!location.repository && !location.conversation ? "landing-shell" : ""}`}
    >
      {deleteTarget && (
        <DeleteRepositoryDialog
          repository={deleteTarget}
          busy={deleting}
          error={deleteError}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={() => void confirmDelete()}
        />
      )}
      <a href="#workspace-main" className="skip-link">
        Skip to workspace
      </a>
      <aside className="rail" aria-label="Workspace navigation">
        <button
          className="brand"
          onClick={() => navigate({})}
          aria-label="GitHub Copilot home"
        >
          <span className="brand-symbol">
            <Github size={23} />
          </span>
          <span>
            GitHub<span className="brand-sub">COPILOT</span>
          </span>
        </button>
        <button className="add-repo" onClick={() => navigate({})}>
          <Plus size={16} />
          Add repository
        </button>
        <div className="rail-section-title">
          YOUR WORKSPACE{" "}
          <span>{recent.length.toString().padStart(2, "0")}</span>
        </div>
        <nav className="repository-list" aria-label="Recent repositories">
          {recent.map((item) => (
            <button
              key={item.id}
              title={item.name}
              aria-label={`Open ${item.name}`}
              className={repository?.id === item.id ? "active" : ""}
              onClick={() => navigate({ repository: item.id })}
            >
              <Github size={16} />
              <span>
                {item.name.split("/").at(-1)}
                <small>{item.name.split("/")[0]}</small>
              </span>
              {repository?.id === item.id && <span className="rail-dot" />}
            </button>
          ))}
          {!recent.length && (
            <p className="rail-empty">Your repositories will appear here.</p>
          )}
        </nav>
        <div className="rail-bottom">
          <div className="read-only">
            <ShieldCheck size={16} />
            <span>
              Read-only by design<small>Your source stays untouched.</small>
            </span>
          </div>
          <a href="https://github.com" target="_blank" rel="noreferrer">
            <Github size={15} />
            Public GitHub repositories
            <ExternalLink size={12} />
          </a>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <span>Workspace</span>
            <ChevronRight size={14} />
            <strong>
              {repository ? shortName(repository.canonical_url) : "Get started"}
            </strong>
          </div>
          <span className="top-label">
            <span className="dot complete" />
            Repository understanding
          </span>
        </header>
        <main id="workspace-main" className="workspace-main">
          {error && (
            <div className="global-error" role="alert">
              <span>{error}</span>
              <button
                className="secondary"
                onClick={() => setRetry((n) => n + 1)}
              >
                Retry loading
              </button>
              <button className="text-button" onClick={() => navigate({})}>
                Add repository
              </button>
            </div>
          )}
          {loading && (
            <div className="workspace-loading" role="status">
              <LoaderCircle className="spin" size={22} />
              <span>Opening your workspace…</span>
            </div>
          )}
          {!location.repository && !location.conversation && !loading && (
            <Home
              url={url}
              setUrl={setUrl}
              branch={ref}
              setBranch={setRef}
              busy={busy}
              onSubmit={register}
              libraryVersion={libraryVersion}
              onOpenRepository={(id) => navigate({ repository: id })}
              onDeleteRepository={requestDelete}
            />
          )}
          {repository && !loading && (
            <>
              <section className="repo-heading">
                <div>
                  <span className="eyebrow">REPOSITORY WORKSPACE</span>
                  <h1>
                    <Github size={24} />
                    {shortName(repository.canonical_url)}
                  </h1>
                  <p>
                    Explore the code. Ask better questions. Follow the evidence.
                  </p>
                </div>
                <div className="repo-actions">
                  <button
                    className="secondary repo-delete"
                    onClick={() =>
                      requestDelete({
                        id: repository.id,
                        name: shortName(repository.canonical_url),
                      })
                    }
                    aria-label="Delete saved repository"
                    title="Delete saved repository"
                  >
                    <Trash2 size={15} />
                    <span>Delete</span>
                  </button>
                  <button
                    className="secondary"
                    disabled={busy || !!indexing}
                    onClick={() => void reindex()}
                  >
                    <RefreshCw size={14} />
                    {job?.status === "failed"
                      ? "Retry indexing"
                      : "Check for updates"}
                  </button>
                </div>
              </section>
              {job && (
                <div
                  className={`index-status ${job.status === "failed" ? "failed" : ""}`}
                  role="status"
                >
                  {indexing ? (
                    <LoaderCircle size={17} className="spin" />
                  ) : (
                    <Check size={17} />
                  )}
                  <div>
                    <strong>
                      {job.status === "failed"
                        ? "Indexing could not finish"
                        : job.status === "succeeded"
                          ? "Updated version is ready"
                          : job.status === "queued"
                            ? job.attempts
                              ? "Waiting to retry indexing"
                              : "Waiting for indexing worker"
                            : job.lease_expires_at &&
                                Date.parse(job.lease_expires_at) <= Date.now()
                              ? "Indexing worker stopped responding"
                              : `Preparing repository · ${job.stage.replaceAll("_", " ")}`}
                    </strong>
                    <p>
                      {job.status === "failed"
                        ? `${job.error?.message || job.error?.code?.replaceAll("_", " ") || "The indexing worker could not complete this request."} Check the repository URL and worker configuration, then retry.`
                        : job.status === "queued" ||
                            (job.status === "running" &&
                              job.lease_expires_at &&
                              Date.parse(job.lease_expires_at) <= Date.now())
                          ? "Progress shows the last completed stage. Indexing will resume when a worker picks up this job."
                          : indexing
                            ? "You can leave this page. Indexing continues in the background."
                            : "Existing conversations keep their original commit."}
                    </p>
                  </div>
                  {indexing && (
                    <div className="progress">
                      <progress
                        value={job.progress}
                        max={100}
                        aria-label="Indexing progress"
                      />
                      <span>{job.progress}%</span>
                    </div>
                  )}
                </div>
              )}
              {indexing && <RepoRunner key={job.id} />}
              {snapshot && (
                <>
                  <div className="snapshot-bar">
                    <div className="commit-label">
                      <GitBranch size={15} />
                      <span>Saved commit</span>
                      <code title={snapshot.commit_sha}>
                        {snapshot.commit_sha.slice(0, 7)}
                      </code>
                      <span className="ready-badge">Ready</span>
                    </div>
                    <label className="version-select">
                      <span>Browse version</span>
                      <select
                        aria-label="Saved version"
                        value={snapshot.id}
                        onChange={(e) =>
                          navigate({
                            repository: repository.id,
                            snapshot: e.target.value,
                          })
                        }
                      >
                        {snapshots.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.commit_sha.slice(0, 7)}
                            {s.id === newest ? " · latest" : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                    <details className="coverage">
                      <summary>
                        <Layers size={14} />
                        Index coverage
                      </summary>
                      <div className="coverage-popover">
                        <strong>What this snapshot includes</strong>
                        <p>
                          {String(
                            snapshot.coverage.stored_files ??
                              snapshot.coverage.stored ??
                              "—",
                          )}{" "}
                          files stored. Only indexed source can support answers.
                        </p>
                        {Object.entries(excluded).length ? (
                          <ul>
                            {Object.entries(excluded).map(([reason, count]) => (
                              <li key={reason}>
                                <span>{reason.replaceAll("_", " ")}</span>
                                <b>{count}</b>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p>No file exclusions reported.</p>
                        )}
                      </div>
                    </details>
                  </div>
                  {newest && newest !== snapshot.id && (
                    <div className="new-version">
                      <GitBranch size={15} />
                      <p>
                        A newer saved commit is available. This conversation
                        stays on <code>{snapshot.commit_sha.slice(0, 7)}</code>.
                      </p>
                      <button
                        disabled={busy}
                        onClick={() => void newConversation(newest)}
                      >
                        Start chat on latest
                        <ArrowRight size={14} />
                      </button>
                    </div>
                  )}
                  <div
                    className="mobile-tabs"
                    role="tablist"
                    aria-label="Workspace panels"
                  >
                    {[
                      ["files", "Files"],
                      ["source", "Source"],
                      ["chat", "Chat"],
                    ].map(([key, label]) => (
                      <button
                        key={key}
                        role="tab"
                        id={`tab-${key}`}
                        aria-controls={`panel-${key}`}
                        tabIndex={mobile === key ? 0 : -1}
                        onKeyDown={(event) => {
                          const panels = ["files", "source", "chat"];
                          const current = panels.indexOf(key);
                          const next =
                            event.key === "ArrowRight"
                              ? panels[(current + 1) % 3]
                              : event.key === "ArrowLeft"
                                ? panels[(current + 2) % 3]
                                : event.key === "Home"
                                  ? panels[0]
                                  : event.key === "End"
                                    ? panels[2]
                                    : null;
                          if (next) {
                            event.preventDefault();
                            setMobile(next);
                            document.getElementById(`tab-${next}`)?.focus();
                          }
                        }}
                        aria-selected={mobile === key}
                        onClick={() => setMobile(key)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <div className={`workspace-grid show-${mobile}`}>
                    <aside
                      id="panel-files"
                      className="files-panel"
                      aria-label="Files and history"
                    >
                      <div className="panel-heading">
                        <div>
                          <FileCode2 size={16} />
                          <h2>Files</h2>
                        </div>
                        <span className="mono tiny">
                          {snapshot.commit_sha.slice(0, 7)}
                        </span>
                      </div>
                      <FileTree
                        snapshot={snapshot.id}
                        onFile={openSource}
                        selected={selection?.path}
                      />
                      <div className="history-heading">
                        <MessageSquare size={14} />
                        <h2>Conversations</h2>
                        <button
                          aria-label="New conversation"
                          className="icon-button"
                          disabled={busy}
                          onClick={() => void newConversation()}
                        >
                          <Plus size={15} />
                        </button>
                      </div>
                      <nav
                        className="history-list"
                        aria-label="Conversation history"
                      >
                        {histories.map((c) => (
                          <button
                            key={c.id}
                            className={conversation === c.id ? "active" : ""}
                            onClick={() => {
                              navigate({
                                repository: repository.id,
                                snapshot: c.snapshot_id,
                                conversation: c.id,
                              });
                              setMobile("chat");
                            }}
                          >
                            <MessageSquare size={13} />
                            <span>{c.title}</span>
                          </button>
                        ))}
                        {!histories.length && (
                          <p className="muted pad">No conversations yet.</p>
                        )}
                      </nav>
                    </aside>
                    <div id="panel-source" className="code-panel">
                      <CodeViewer selection={selection} onPage={setSelection} />
                    </div>
                    {conversation ? (
                      <Chat
                        key={conversation}
                        id={conversation}
                        onSource={openSource}
                        onNew={() => void newConversation()}
                      />
                    ) : (
                      <section
                        id="panel-chat"
                        className="chat-panel chat-start"
                      >
                        <span className="spark-tile">
                          <Sparkles size={26} />
                        </span>
                        <h2>Meet your codebase.</h2>
                        <p>
                          Start a conversation about this commit. Your answers
                          and sources will stay together.
                        </p>
                        <button
                          className="primary"
                          disabled={busy}
                          onClick={() => void newConversation()}
                        >
                          <Plus size={16} />
                          Start conversation
                        </button>
                      </section>
                    )}
                  </div>
                </>
              )}
              {!snapshot && !indexing && (
                <div className="waiting-empty">
                  <Layers size={32} />
                  <h2>Your workspace is taking shape.</h2>
                  <p>
                    {job?.status === "failed"
                      ? "Retry indexing to prepare a searchable snapshot."
                      : "No ready version is available yet. Check for updates to start indexing."}
                  </p>
                </div>
              )}
            </>
          )}
        </main>
        <footer className="workspace-footer">
          <span>
            <ShieldCheck size={12} />
            Read-only repository intelligence
          </span>
          <span>
            <CircleHelp size={12} />
            Answers are grounded in saved source. Check citations for support.
          </span>
        </footer>
      </div>
    </div>
  );
}
