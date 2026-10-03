"use client";
import { useEffect, useRef, useState } from "react";
import { ArrowUp, Github, X } from "lucide-react";

export function PlayfulEffects() {
  const cursor = useRef<HTMLDivElement>(null);
  const [bottom, setBottom] = useState(false);
  useEffect(() => {
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");
    const fine = matchMedia("(pointer: fine)");
    let lastShown = -Infinity;
    let timer: ReturnType<typeof setTimeout>;
    let touchY = 0;
    const move = (event: PointerEvent) => {
      if (
        !cursor.current ||
        !fine.matches ||
        reduced.matches ||
        event.pointerType === "touch"
      )
        return;
      cursor.current.style.transform = `translate3d(${event.clientX}px, ${event.clientY}px, 0)`;
      cursor.current.style.opacity = "1";
      cursor.current.dataset.active = String(
        !!(event.target as HTMLElement).closest("a, button, input, summary"),
      );
    };
    const leave = () => {
      if (cursor.current) cursor.current.style.opacity = "0";
    };
    const overscroll = (target: EventTarget | null) => {
      let element = target instanceof HTMLElement ? target : null;
      if (element?.closest("input, textarea, select")) return;
      while (element && element !== document.body) {
        if (
          element.scrollHeight > element.clientHeight + 2 &&
          /(auto|scroll)/.test(getComputedStyle(element).overflowY) &&
          element.scrollTop + element.clientHeight < element.scrollHeight - 2
        )
          return;
        element = element.parentElement;
      }
      if (
        window.scrollY + window.innerHeight >=
          document.documentElement.scrollHeight - 3 &&
        performance.now() - lastShown > 14000
      ) {
        lastShown = performance.now();
        setBottom(true);
        clearTimeout(timer);
        timer = setTimeout(() => setBottom(false), 5500);
      }
    };
    const wheel = (event: WheelEvent) => {
      if (event.deltaY > 12) overscroll(event.target);
    };
    const start = (event: TouchEvent) => {
      touchY = event.touches[0].clientY;
    };
    const touch = (event: TouchEvent) => {
      if (touchY - event.touches[0].clientY > 35) overscroll(event.target);
    };
    const key = (event: KeyboardEvent) => {
      if (["ArrowDown", "PageDown", "End"].includes(event.key))
        overscroll(event.target);
    };
    document.addEventListener("pointermove", move, { passive: true });
    document.addEventListener("pointerleave", leave);
    window.addEventListener("wheel", wheel, { passive: true });
    window.addEventListener("touchstart", start, { passive: true });
    window.addEventListener("touchmove", touch, { passive: true });
    window.addEventListener("keydown", key);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerleave", leave);
      window.removeEventListener("wheel", wheel);
      window.removeEventListener("touchstart", start);
      window.removeEventListener("touchmove", touch);
      window.removeEventListener("keydown", key);
    };
  }, []);
  return (
    <>
      <div className="cursor-halo" ref={cursor} aria-hidden="true">
        <span />
      </div>
      {bottom && (
        <div className="bottom-toast" role="status">
          <Github size={26} />
          <div>
            <strong>You’ve reached the bottom.</strong>
            <p>Of the page, of course. Your potential? Limitless.</p>
            <button
              onClick={() => {
                window.scrollTo({ top: 0, behavior: "smooth" });
                setBottom(false);
              }}
            >
              Back to the good stuff <ArrowUp size={13} />
            </button>
          </div>
          <button
            className="toast-close"
            aria-label="Dismiss bottom message"
            onClick={() => setBottom(false)}
          >
            <X size={16} />
          </button>
        </div>
      )}
    </>
  );
}
