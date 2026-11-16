"use client";

import { useEffect } from "react";

/**
 * Turns smooth scrolling on once the page has loaded. Set in CSS from the start, it also animated
 * the browser's jump to the `#how` of a link from another page, from the top while the page was
 * still busy, and Safari painted the header and the section late for seconds. Links within the page
 * still scroll smoothly.
 */
export function SmoothScroll() {
  useEffect(() => {
    const frame = requestAnimationFrame(() => document.documentElement.classList.add("smooth"));
    return () => cancelAnimationFrame(frame);
  }, []);
  return null;
}
