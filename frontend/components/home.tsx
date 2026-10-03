"use client";
import {
  ArrowDown,
  ArrowRight,
  Braces,
  Check,
  ChevronRight,
  Code2,
  FileCode2,
  GitBranch,
  Github,
  LoaderCircle,
  MessageSquare,
  Pause,
  Play,
  ShieldCheck,
  Sparkles,
  Terminal,
} from "lucide-react";
import { useEffect, useState } from "react";
import { PlayfulEffects } from "./playful-effects";
import { RepositoryLibrary, type SavedRepository } from "./repository-library";

const steps = [
  {
    title: "Bring a repository",
    description:
      "One public GitHub link. We map the files and save the exact commit, so every exploration has a solid starting point.",
    icon: Github,
  },
  {
    title: "Ask what matters",
    description:
      "Follow your curiosity. Untangle a feature, trace a function, or find the best place to make your first contribution.",
    icon: MessageSquare,
  },
  {
    title: "Follow the evidence",
    description:
      "Go beyond the answer. Jump straight to the source lines and see how all the pieces fit together.",
    icon: FileCode2,
  },
];
export function Home({
  url,
  setUrl,
  branch,
  setBranch,
  busy,
  onSubmit,
  libraryVersion,
  onOpenRepository,
  onDeleteRepository,
}: {
  libraryVersion: number;
  onOpenRepository: (id: string) => void;
  onDeleteRepository: (repository: SavedRepository) => void;
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
    const timer = setInterval(() => setStep((value) => (value + 1) % 3), 4500);
    return () => clearInterval(timer);
  }, [playing]);
  return (
    <div className="home" data-playing={playing}>
      <PlayfulEffects />
      <nav className="landing-nav" aria-label="Main navigation">
        <a className="landing-brand" href="#">
          <Github size={30} />
          <span>
            GitHub <b>Copilot</b>
          </span>
        </a>
        <div className="landing-links">
          <a href="#how-it-works">How it works</a>
          <a href="#under-the-hood">Under the hood</a>
        </div>
        <div className="landing-nav-actions">
          <a className="my-repos-link" href="#my-repositories">
            My repositories
          </a>
          <a className="nav-cta" href="#repository-url">
            Explore a repo <ArrowRight size={15} />
          </a>
        </div>
      </nav>
      <div className="home-grid">
        <section className="home-story" aria-labelledby="home-title">
          <div className="hero-eyebrow">
            <span className="signal-dot" /> FOR THE CURIOUS MINDS BEHIND THE
            CODE
          </div>
          <h1 id="home-title">
            Don’t judge a repo
            <br />
            by its <span>cover.</span>
            <br />
            <em>Look inside.</em>
          </h1>
          <p className="home-description">
            Great code has a story. Get past the README and into the good stuff
            — with answers that lead you straight to the source.
          </p>
          <div className="hero-actions">
            <a className="primary" href="#repository-url">
              Find your way in <ArrowRight size={18} />
            </a>
            <a className="quiet-link" href="#how-it-works">
              See how it works <ArrowDown size={16} />
            </a>
          </div>
          <div className="hero-proof">
            <span>
              <ShieldCheck size={15} /> Read-only by design
            </span>
            <span>
              <GitBranch size={15} /> Grounded in your code
            </span>
          </div>
          <div className="hero-note">
            <span>01 —</span> LESS GUESSWORK. MORE “AHA.”
          </div>
        </section>
        <section className="home-start" aria-labelledby="start-title">
          <div
            className="code-universe"
            aria-label="Animated illustration of connected repository files"
          >
            <div className="orbit orbit-one" />
            <div className="orbit orbit-two" />
            <div className="orbit orbit-three" />
            <div className="universe-cross cross-one">+</div>
            <div className="universe-cross cross-two">+</div>
            <span className="floating-code code-left">
              <Braces size={16} /> src / curiosity.ts
            </span>
            <span className="floating-code code-right">
              <GitBranch size={16} /> follow the source
            </span>
            <span className="floating-node node-one">
              <Code2 size={22} />
            </span>
            <span className="floating-node node-two">
              <FileCode2 size={20} />
            </span>
            <div className="universe-core">
              <Github size={64} strokeWidth={1.3} />
              <span className="core-spark">
                <Sparkles size={16} />
              </span>
            </div>
            <span className="universe-caption">
              <span className="signal-dot" /> A WHOLE WORLD INSIDE YOUR REPO
            </span>
            <button
              className="motion-toggle"
              onClick={() => setPlaying(!playing)}
              aria-label={playing ? "Pause illustration" : "Play illustration"}
            >
              {playing ? <Pause size={13} /> : <Play size={13} />}
            </button>
          </div>
          <form className="start-card" onSubmit={onSubmit}>
            <div className="start-card-heading">
              <span className="start-icon">
                <Terminal size={20} />
              </span>
              <div>
                <span className="eyebrow">YOUR NEXT DEEP DIVE</span>
                <h2 id="start-title">Big discoveries. One little link.</h2>
              </div>
              <span className="form-number">↗</span>
            </div>
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
                  Open workspace <ArrowRight size={17} />
                </>
              )}
            </button>
            <p id="link-help" className="link-help">
              <ShieldCheck size={13} /> We read the code. Your source stays
              untouched.
            </p>
          </form>
        </section>
      </div>
      <div className="tech-strip">
        <span>FROM “WHAT IS THIS?” TO “I GET IT.”</span>
        <div>
          <Braces size={17} /> Understand the architecture
        </div>
        <div>
          <GitBranch size={17} /> Trace the logic
        </div>
        <div>
          <Sparkles size={17} /> Find your next idea
        </div>
      </div>
      <RepositoryLibrary
        version={libraryVersion}
        onOpen={onOpenRepository}
        onDelete={onDeleteRepository}
      />
      <section
        className="how-it-works"
        id="how-it-works"
        aria-labelledby="how-title"
      >
        <div className="section-kicker">THE WAY IN / 01</div>
        <div className="how-heading">
          <h2 id="how-title">
            Go from a link
            <br />
            to a <span>lightbulb moment.</span>
          </h2>
          <p>
            No more opening fifty tabs.
            <br />
            Just you, your questions, and the code.
          </p>
        </div>
        <ol>
          {steps.map((item, index) => (
            <li key={item.title}>
              <div className="feature-top">
                <item.icon size={24} />
                <span>0{index + 1}</span>
              </div>
              <h3>{item.title}</h3>
              <p>{item.description}</p>
              <span className="feature-arrow">
                <ArrowRight size={18} />
              </span>
            </li>
          ))}
        </ol>
      </section>
      <section
        className="inside-section"
        id="under-the-hood"
        aria-labelledby="inside-title"
      >
        <div>
          <div className="section-kicker">REAL CODE. REAL CONTEXT. / 02</div>
          <h2 id="inside-title">
            The answer is good.
            <br />
            <span>The evidence is better.</span>
          </h2>
          <p>
            Every explanation comes with a way to check it. Explore saved
            source, inspect citations, and keep the whole conversation in
            context.
          </p>
          <div className="inside-points">
            <span>
              <Check size={16} /> Exact source lines
            </span>
            <span>
              <Check size={16} /> Saved commit history
            </span>
            <span>
              <Check size={16} /> Conversations that stay with you
            </span>
          </div>
        </div>
        <div
          className="preview"
          aria-label="An illustrative preview of the workflow"
        >
          <div className="preview-title">
            <span>
              <i />
              <i />
              <i />
            </span>
            <span>THE AHA. IN ACTION.</span>
            <Code2 size={14} />
          </div>
          <div className="preview-body" key={step}>
            <div className="demo-label">
              ILLUSTRATIVE EXAMPLE · {steps[step].title}
            </div>
            {step === 0 ? (
              <>
                <div className="demo-repo">
                  <Github size={25} />
                  <strong>your-next-project</strong>
                  <span>main</span>
                </div>
                <div className="demo-files">
                  <span>
                    <FileCode2 size={14} /> README.md{" "}
                    <small>the introduction</small>
                  </span>
                  <span>
                    <FileCode2 size={14} /> src / auth.py{" "}
                    <small>the good stuff</small>
                  </span>
                  <span>
                    <FileCode2 size={14} /> tests /{" "}
                    <small>the expectations</small>
                  </span>
                </div>
              </>
            ) : step === 1 ? (
              <>
                <div className="demo-question">
                  How does authentication work?
                </div>
                <div className="demo-answer">
                  <Sparkles size={19} />
                  <p>
                    Following the source…
                    <br />
                    <span>Finding the files that explain the behavior.</span>
                  </p>
                </div>
                <div className="demo-search">
                  <span className="signal-dot" /> Search → Read → Connect the
                  dots
                </div>
              </>
            ) : (
              <>
                <div className="demo-answer">
                  <Sparkles size={19} />
                  <p>
                    Authentication checks the token.
                    <br />
                    <span>Here’s exactly where it happens.</span>
                  </p>
                </div>
                <div className="demo-code">
                  <span>12</span> <b>def</b> authenticate(token):
                  <br />
                  <span>13</span> &nbsp; <b>return</b> verify(token)
                </div>
                <div className="demo-citation">
                  <FileCode2 size={13} /> auth.py <span>L12–13</span>
                  <ArrowRight size={13} />
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
            <span>0{step + 1} / 03</span>
          </div>
        </div>
      </section>
      <div className="home-bottom">
        <a className="landing-brand" href="#">
          <Github size={22} />
          <span>
            GitHub <b>Copilot</b>
          </span>
        </a>
        <span>A little curiosity goes a long way.</span>
        <a href="#repository-url">
          Let’s look inside <ArrowRight size={15} />
        </a>
      </div>
    </div>
  );
}
