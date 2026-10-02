"use client";
import { ChevronDown, Terminal } from "lucide-react";
import { useEffect, useState } from "react";

// Fetch the persisted public activity only when a reader expands a completed answer.
export function SavedActivity({ run }: { run: string }) {
  const [open, setOpen] = useState(false);
  const [finished, setFinished] = useState(false);
  const [events, setEvents] = useState<
    { sequence: number; tool: string; status: string }[]
  >([]);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open || finished) return;
    let last = 0;
    setEvents([]);
    const stream = new EventSource(`/api/runs/${run}/events`);
    const record = (raw: Event) => {
      if (!(raw instanceof MessageEvent)) return;
      const sequence = Number(raw.lastEventId);
      if (!Number.isSafeInteger(sequence) || sequence <= last) return;
      try {
        const value = JSON.parse(raw.data);
        last = sequence;
        setEvents((old) => [
          ...old,
          { sequence, tool: value.tool, status: value.status },
        ]);
        setError("");
      } catch {
        setError("Could not read an activity event.");
      }
    };
    stream.addEventListener("tool_finished", record);
    stream.addEventListener("done", () => {
      stream.close();
      setFinished(true);
      setError("");
    });
    stream.onerror = () => setError("Reconnecting to saved activity…");
    return () => stream.close();
  }, [run, open, finished]);
  return (
    <details
      className="activity saved-activity"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <Terminal size={14} />
        Repository activity <ChevronDown size={13} />
      </summary>
      <ul>
        {events.map((item) => (
          <li key={item.sequence}>
            <span
              className={`dot ${item.status === "completed" ? "complete" : ""}`}
            />
            <code>{item.tool.replaceAll("_", " ")}</code>
            <span>{item.status}</span>
          </li>
        ))}
      </ul>
      {error && <p role="status">{error}</p>}
      {!error && !events.length && (
        <p className="muted">
          {finished ? "No tool activity recorded." : "Loading saved activity…"}
        </p>
      )}
    </details>
  );
}
