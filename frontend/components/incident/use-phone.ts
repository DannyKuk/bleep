"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

/** Below Tailwind's `lg` breakpoint the incident page is the one-column phone layout. */
export const PHONE_QUERY = "(max-width: 1023.98px)";

function subscribe(onChange: () => void): () => void {
  if (typeof window.matchMedia !== "function") return () => {};
  const query = window.matchMedia(PHONE_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function isPhone(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(PHONE_QUERY).matches;
}

/** Whether the screen is narrower than the desktop layout; false on the server. */
export function usePhone(): boolean {
  return useSyncExternalStore(subscribe, isPhone, () => false);
}

/**
 * How far the on-screen keyboard covers the bottom of the layout viewport, in px. A bar pinned
 * with `position: fixed; bottom: 0` stays behind the keyboard on iOS and on Chrome for Android,
 * where the keyboard shrinks only the visual viewport, so the bar is lifted by this much.
 */
export function useKeyboardInset(enabled: boolean): number {
  const [inset, setInset] = useState(0);
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!enabled || !viewport) return;
    const update = () =>
      setInset(Math.max(0, Math.round(window.innerHeight - viewport.height - viewport.offsetTop)));
    update();
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    return () => {
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
      setInset(0);
    };
  }, [enabled]);
  return inset;
}
