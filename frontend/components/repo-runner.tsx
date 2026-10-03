"use client";
import { Github, Pause, Play, RotateCcw, Trophy } from "lucide-react";
import { useEffect, useRef, useState } from "react";

type Mode = "idle" | "running" | "paused" | "over";
const fresh = () => ({
  y: 0,
  velocity: 0,
  obstacle: 640,
  distance: 0,
  score: 0,
});
export function RepoRunner() {
  const [mode, setMode] = useState<Mode>("idle");
  const [frame, setFrame] = useState(fresh);
  const [best, setBest] = useState(0);
  const world = useRef(fresh());
  const arena = useRef<HTMLDivElement>(null);
  useEffect(() => {
    try {
      setBest(Number(localStorage.getItem("copilot-runner-best")) || 0);
    } catch {
      /* Storage is optional. */
    }
  }, []);
  useEffect(() => {
    if (mode !== "running") return;
    let request = 0;
    let previous = 0;
    const tick = (time: number) => {
      const dt = previous ? Math.min((time - previous) / 1000, 0.035) : 0;
      previous = time;
      const state = world.current;
      state.velocity -= 950 * dt;
      state.y = Math.max(0, state.y + state.velocity * dt);
      if (!state.y && state.velocity < 0) state.velocity = 0;
      state.obstacle -= (235 + Math.min(state.score * 0.7, 130)) * dt;
      state.distance += dt * 10;
      state.score = Math.floor(state.distance);
      if (state.obstacle < -25) state.obstacle = 640 + Math.random() * 150;
      if (state.obstacle < 79 && state.obstacle + 22 > 47 && state.y < 29) {
        setMode("over");
        setBest((old) => {
          const next = Math.max(old, state.score);
          try {
            localStorage.setItem("copilot-runner-best", String(next));
          } catch {
            /* Storage is optional. */
          }
          return next;
        });
        setFrame({ ...state });
        return;
      }
      setFrame({ ...state });
      request = requestAnimationFrame(tick);
    };
    request = requestAnimationFrame(tick);
    const hide = () => {
      if (document.hidden) setMode("paused");
    };
    document.addEventListener("visibilitychange", hide);
    return () => {
      cancelAnimationFrame(request);
      document.removeEventListener("visibilitychange", hide);
    };
  }, [mode]);
  function start() {
    world.current = fresh();
    setFrame(fresh());
    setMode("running");
    arena.current?.focus();
  }
  function jump() {
    if (mode === "running" && world.current.y === 0)
      world.current.velocity = 410;
  }
  function resume() {
    setMode("running");
    arena.current?.focus();
  }
  return (
    <section className="runner" aria-labelledby="runner-title">
      <div className="runner-heading">
        <div>
          <span className="eyebrow">A LITTLE SIDE QUEST</span>
          <h2 id="runner-title">Code loading. You jumping.</h2>
        </div>
        <div className="runner-scores">
          <span>
            SCORE <b>{String(frame.score).padStart(4, "0")}</b>
          </span>
          <span>
            <Trophy size={13} /> BEST <b>{String(best).padStart(4, "0")}</b>
          </span>
        </div>
      </div>
      <p className="runner-description">
        Meet Repo Runner. Dodge the bugs while we get to know your code.
      </p>
      <div
        className="runner-arena"
        ref={arena}
        tabIndex={0}
        role="group"
        aria-label="Repo Runner game"
        aria-describedby="runner-instructions"
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return;
          if ([" ", "ArrowUp"].includes(event.key)) {
            event.preventDefault();
            if (mode === "idle" || mode === "over") start();
            else if (mode === "paused") resume();
            else jump();
          }
          if (event.key === "Escape" && mode === "running") setMode("paused");
        }}
        onPointerDown={(event) => {
          if (!(event.target as HTMLElement).closest("button")) {
            arena.current?.focus();
            jump();
          }
        }}
      >
        <div className="runner-sky" aria-hidden="true">
          <span>{"{ }"}</span>
          <span>+</span>
          <span>{"</>"}</span>
          <span>*</span>
        </div>
        <div
          className="runner-character"
          style={{ bottom: `${29 + frame.y}px` }}
          aria-hidden="true"
        >
          <Github size={34} />
        </div>
        <div
          className="runner-obstacle"
          style={{ left: `${(frame.obstacle / 640) * 100}%` }}
          aria-hidden="true"
        >
          !
        </div>
        <div className="runner-ground" aria-hidden="true" />
        {mode !== "running" && (
          <div className="runner-overlay">
            <strong>
              {mode === "over"
                ? "A bug caught you. Classic."
                : mode === "paused"
                  ? "Taking a breather."
                  : "Your next high score is waiting."}
            </strong>
            <button
              className="primary"
              onClick={mode === "paused" ? resume : start}
            >
              {mode === "over" ? <RotateCcw size={15} /> : <Play size={15} />}
              {mode === "over"
                ? "Try again"
                : mode === "paused"
                  ? "Resume game"
                  : "Play Repo Runner"}
            </button>
            <span>
              {mode === "over"
                ? `Score: ${frame.score}. The repo is still doing its thing.`
                : "Optional fun. Indexing keeps going."}
            </span>
          </div>
        )}
        {mode === "running" && (
          <button
            className="runner-pause"
            aria-label="Pause game"
            onClick={() => setMode("paused")}
          >
            <Pause size={16} />
          </button>
        )}
      </div>
      <div className="runner-footer">
        <p id="runner-instructions">
          <kbd>Space</kbd> / <kbd>↑</kbd> or tap to jump · <kbd>Esc</kbd> to
          pause
        </p>
        <span role="status">
          {mode === "over"
            ? `Game over. Score ${frame.score}.`
            : mode === "paused"
              ? "Game paused."
              : "No bugs were harmed. Probably."}
        </span>
      </div>
    </section>
  );
}
