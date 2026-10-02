"use client";
import {
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  Code2,
  FileCode2,
  GitBranch,
  Github,
  LoaderCircle,
  Pause,
  Play,
  Search,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useEffect, useState } from "react";

const steps = [
  {
    title: "Bring a repository",
    description:
      "Paste a public GitHub link. We save a version of the code and prepare it for questions.",
  },
  {
    title: "Ask what matters",
    description:
      "Start a conversation. Ask where to begin, how a feature works, or where a function is used.",
  },
  {
    title: "Follow the evidence",
    description:
      "Read the answer, then click a citation to open the exact lines that support it.",
  },
];
export function Home({
  url,
  setUrl,
  branch,
  setBranch,
  busy,
  onSubmit,
}: {
  url: string;
  setUrl: (value: string) => void;
  branch: string;
  setBranch: (value: string) => void;
  busy: boolean;
  onSubmit: (event: React.FormEvent) => void;
}) {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(true);
  useEffect(() => {
    const preference = matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => {
      if (preference.matches) setPlaying(false);
    };
    update();
    preference.addEventListener("change", update);
    return () => preference.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(
      () => setStep((value) => (value + 1) % steps.length),
      4500,
    );
    return () => clearInterval(timer);
  }, [playing]);
  return (
    <div className="home">
      <div className="home-topline">
        <span>
          <span className="dot complete" />A LITTLE CURIOSITY. A LOT OF CONTEXT.
        </span>
        <span className="home-tag">
          <Code2 size={12} />
          Built for developers
        </span>
      </div>
      <div className="home-grid">
        <section className="home-story" aria-labelledby="home-title">
          <h1 id="home-title">
            Every repository
            <br />
            has a story.
            <br />
            <span>Find your way in.</span>
          </h1>
          <p className="home-description">
            Turn an unfamiliar codebase into a conversation.
            <br className="desktop-break" /> Clear answers, with source code you
            can inspect.
          </p>
          <div
            className="preview"
            data-playing={playing}
            aria-label="An illustrative preview of the workflow"
          >
            <div className="preview-title">
              <span>
                <span className="preview-dot" />
                <span className="preview-dot" />
                <span className="preview-dot" />
              </span>
              <span>A QUICK PREVIEW</span>
              <GitBranch size={13} />
            </div>
            <div className="preview-body" key={step} aria-hidden="true">
              {step === 0 && (
                <>
                  <div className="preview-repo">
                    <span className="preview-github">
                      <Github size={23} />
                    </span>
                    <div>
                      <small>PUBLIC REPOSITORY</small>
                      <strong>your-next-project</strong>
                    </div>
                    <span className="preview-saved">
                      <Check size={12} />
                      Saved
                    </span>
                  </div>
                  <div className="preview-files">
                    <div>
                      <FileCode2 size={13} />
                      README.md<span>the starting point</span>
                    </div>
                    <div>
                      <FileCode2 size={13} />
                      src / auth.py<span>the details</span>
                    </div>
                    <div>
                      <FileCode2 size={13} />
                      tests /<span>the expectations</span>
                    </div>
                  </div>
                </>
              )}
              {step === 1 && (
                <>
                  <div className="preview-question">
                    <span>YOU</span>
                    <p>How does authentication work?</p>
                  </div>
                  <div className="preview-search">
                    <span className="preview-spark">
                      <Sparkles size={17} />
                    </span>
                    <div>
                      <strong>Looking through the source</strong>
                      <small>
                        Finding the files that explain the behavior…
                      </small>
                    </div>
                    <span className="thinking-dots">
                      <i />
                      <i />
                      <i />
                    </span>
                  </div>
                  <div className="preview-path">
                    <Search size={12} />
                    Search files <ChevronRight size={12} />
                    Read source <ChevronRight size={12} />
                    Check citations
                  </div>
                </>
              )}
              {step === 2 && (
                <>
                  <div className="preview-answer">
                    <span className="preview-spark">
                      <Sparkles size={17} />
                    </span>
                    <p>
                      An explanation you can trace
                      <br />
                      <strong>all the way back to the code.</strong>
                    </p>
                  </div>
                  <div className="preview-code">
                    <div>
                      <span>12</span>
                      <code>
                        <b>def</b> authenticate(token):
                      </code>
                    </div>
                    <div className="preview-highlight">
                      <span>13</span>
                      <code>
                        {" "}
                        <b>return</b> verify(token)
                      </code>
                    </div>
                  </div>
                  <div className="preview-citation">
                    <FileCode2 size={12} />
                    auth.py <span>L12–13</span>
                    <ArrowRight size={12} />
                  </div>
                </>
              )}
            </div>
            <div className="preview-controls">
              <div>
                {steps.map((item, index) => (
                  <button
                    key={item.title}
                    aria-label={`Preview step ${index + 1}: ${item.title}`}
                    aria-pressed={step === index}
                    onClick={() => {
                      setStep(index);
                      setPlaying(false);
                    }}
                  >
                    <span />
                  </button>
                ))}
              </div>
              <span>
                {String(step + 1).padStart(2, "0")} / 03 · {steps[step].title}
              </span>
              <button
                className="preview-pause"
                onClick={() => setPlaying(!playing)}
                aria-label={
                  playing ? "Pause illustration" : "Play illustration"
                }
              >
                {playing ? <Pause size={12} /> : <Play size={12} />}
              </button>
            </div>
          </div>
        </section>
        <section className="home-start" aria-labelledby="start-title">
          <form className="start-card" onSubmit={onSubmit}>
            <span className="start-icon">
              <Github size={24} />
            </span>
            <span className="eyebrow">YOUR NEXT DEEP DIVE</span>
            <h2 id="start-title">It starts with a link.</h2>
            <p>
              Bring a public repository. We’ll make a space to explore it, one
              question at a time.
            </p>
            <label htmlFor="repository-url">
              Start with a public GitHub repository
            </label>
            <div className="home-url">
              <Github size={17} />
              <input
                id="repository-url"
                type="url"
                placeholder="https://github.com/owner/repository"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                required
                disabled={busy}
                aria-describedby="link-help"
              />
            </div>
            <p id="link-help" className="link-help">
              Copy the repository URL from your browser.
            </p>
            <details className="ref-option">
              <summary>
                Choose a branch or commit <ChevronRight size={12} />
              </summary>
              <label htmlFor="repository-ref">Branch, tag, or commit</label>
              <input
                id="repository-ref"
                value={branch}
                onChange={(event) => setBranch(event.target.value)}
                placeholder="HEAD (default branch)"
                maxLength={255}
                disabled={busy}
              />
            </details>
            <button className="primary home-submit" disabled={busy}>
              {busy ? (
                <>
                  <LoaderCircle size={17} className="spin" />
                  Preparing your workspace…
                </>
              ) : (
                <>
                  Open workspace
                  <ArrowRight size={17} />
                </>
              )}
            </button>
            <div className="start-next">
              <span className="step-pin">↳</span>
              <p>
                <strong>What happens next?</strong> We’ll index the code. Then
                choose <b>Start conversation</b> and ask your first question.
              </p>
            </div>
            <div className="start-trust">
              <ShieldCheck size={14} />
              <span>Read-only. Your code stays untouched.</span>
            </div>
          </form>
          <div className="question-inspiration">
            <BookOpen size={17} />
            <div>
              <span>NOT SURE WHAT TO ASK?</span>
              <p>“Where should I start reading this project?”</p>
            </div>
          </div>
        </section>
      </div>
      <section className="how-it-works" aria-labelledby="how-title">
        <div className="how-heading">
          <h2 id="how-title">From link to understanding.</h2>
          <span>Three small steps. A clearer picture.</span>
        </div>
        <ol>
          {steps.map((item, index) => (
            <li key={item.title}>
              <span className="step-number">0{index + 1}</span>
              <div>
                <h3>{item.title}</h3>
                <p>{item.description}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>
      <div className="home-bottom">
        <span>Made for your next “how does this work?”</span>
        <span>
          <GitBranch size={12} />
          Every answer stays with its saved commit.
        </span>
      </div>
    </div>
  );
}
