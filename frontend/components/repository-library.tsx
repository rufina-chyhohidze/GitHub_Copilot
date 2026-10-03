"use client";
import {
  ArrowRight,
  FolderGit2,
  Github,
  LoaderCircle,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { allPages, errorText } from "@/lib/api";

export type SavedRepository = { id: string; name: string };
export function RepositoryLibrary({
  version,
  onOpen,
  onDelete,
}: {
  version: number;
  onOpen: (id: string) => void;
  onDelete: (repository: SavedRepository) => void;
}) {
  const [items, setItems] = useState<SavedRepository[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    allPages<{ id: string; canonical_url: string }>("/repositories")
      .then((rows) => {
        if (alive)
          setItems(
            rows.map((row) => ({
              id: row.id,
              name: row.canonical_url.replace("https://github.com/", ""),
            })),
          );
      })
      .catch((reason) => {
        if (alive) setError(errorText(reason));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [version, retry]);
  return (
    <section
      id="my-repositories"
      className="repository-library"
      aria-labelledby="library-title"
    >
      <div className="library-heading">
        <div>
          <div className="section-kicker">PICK UP WHERE YOU LEFT OFF</div>
          <h2 id="library-title">My repositories</h2>
        </div>
        <FolderGit2 size={25} />
      </div>
      <p>Your saved repositories, ready for another look inside.</p>
      {loading ? (
        <div className="library-empty" role="status">
          <LoaderCircle size={18} className="spin" /> Loading repositories…
        </div>
      ) : error ? (
        <div className="library-empty" role="alert">
          <p>{error}</p>
          <button
            className="secondary"
            onClick={() => setRetry((value) => value + 1)}
          >
            Retry repositories
          </button>
        </div>
      ) : items.length ? (
        <ul className="library-grid">
          {items.map((item) => (
            <li key={item.id}>
              <button
                className="library-open"
                onClick={() => onOpen(item.id)}
                aria-label={`Open ${item.name}`}
              >
                <Github size={22} />
                <span>
                  <strong>{item.name.split("/").at(-1)}</strong>
                  <small>{item.name.split("/")[0]}</small>
                </span>
                <ArrowRight size={17} />
              </button>
              <button
                className="library-delete"
                aria-label={`Delete ${item.name}`}
                title="Delete saved repository"
                onClick={() => onDelete(item)}
              >
                <Trash2 size={16} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="library-empty">
          <FolderGit2 size={22} />
          <p>
            No saved repositories yet. Add a GitHub link above to get started.
          </p>
        </div>
      )}
    </section>
  );
}

export function DeleteRepositoryDialog({
  repository,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  repository: SavedRepository;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    cancelButton.current?.focus();
    return () => element?.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="delete-dialog"
      aria-labelledby="delete-title"
      aria-describedby="delete-description"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
    >
      <button
        className="dialog-close"
        aria-label="Close deletion dialog"
        disabled={busy}
        onClick={onCancel}
      >
        <X size={18} />
      </button>
      <span className="delete-icon">
        <Trash2 size={25} />
      </span>
      <h2 id="delete-title">Delete saved repository?</h2>
      <strong className="delete-repo-name">{repository.name}</strong>
      <p id="delete-description">
        This permanently deletes its saved snapshots, files, and conversations
        from this app. The repository on GitHub stays untouched.
      </p>
      {error && (
        <p className="error-box" role="alert">
          {error}
        </p>
      )}
      <div className="dialog-actions">
        <button
          className="secondary"
          ref={cancelButton}
          disabled={busy}
          onClick={onCancel}
        >
          Keep repository
        </button>
        <button className="delete-confirm" disabled={busy} onClick={onConfirm}>
          {busy ? (
            <LoaderCircle size={16} className="spin" />
          ) : (
            <Trash2 size={16} />
          )}
          {busy ? "Deleting…" : "Delete repository"}
        </button>
      </div>
    </dialog>
  );
}
