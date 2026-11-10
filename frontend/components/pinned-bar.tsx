"use client";

import {
  createContext,
  useContext,
  useLayoutEffect,
  useState,
  type ReactNode,
  type RefObject,
} from "react";

type Report = (space: number) => void;

const SpaceContext = createContext(0);
const ReportContext = createContext<Report | null>(null);

/**
 * How much of the screen's bottom a bar pinned there (an incident's answer bar on a phone) takes,
 * so the chat button can sit above it instead of over it. It wraps the app layout.
 */
export function PinnedBarProvider({ children }: { children: ReactNode }) {
  const [space, setSpace] = useState(0);
  return (
    <ReportContext.Provider value={setSpace}>
      <SpaceContext.Provider value={space}>{children}</SpaceContext.Provider>
    </ReportContext.Provider>
  );
}

/** The px from the viewport's bottom to the pinned bar's top edge; 0 when no bar is pinned. */
export function usePinnedBarSpace(): number {
  return useContext(SpaceContext);
}

/**
 * Reports the space the element in `ref` takes from the viewport's bottom while `pinned`, again
 * whenever it changes size, the window resizes or `moved` changes (an on-screen keyboard's
 * inset). Without the provider, as in a page's own tests, it does nothing.
 */
export function useReportPinnedBar(
  ref: RefObject<HTMLElement | null>,
  pinned: boolean,
  moved: unknown,
) {
  const report = useContext(ReportContext);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!report || !pinned || !element) return;
    const measure = () =>
      report(Math.max(0, window.innerHeight - element.getBoundingClientRect().top));
    measure();
    const resized = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    resized?.observe(element);
    window.addEventListener("resize", measure);
    return () => {
      resized?.disconnect();
      window.removeEventListener("resize", measure);
      report(0);
    };
  }, [ref, report, pinned, moved]);
}
