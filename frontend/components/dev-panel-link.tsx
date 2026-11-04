"use client";

import { FlaskConical } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";

/** Whether the backend has the dev panel on: `/api/dev` answers only then (404 otherwise). */
function useDevPanel(): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    apiFetch("/api/dev", { signal: controller.signal, redirectOn401: false })
      .then(() => setOn(true))
      .catch(() => setOn(false));
    return () => controller.abort();
  }, []);
  return on;
}

/** The way to the developer panel, shown on Settings only while the panel is on. */
export function DevPanelLink() {
  if (!useDevPanel()) return null;
  return (
    <Link
      href="/dev"
      className="inline-flex min-h-11 items-center gap-2 self-start rounded-full border border-line bg-surface px-4 text-[15px] font-bold text-ink-2 hover:bg-soft hover:text-ink focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <FlaskConical aria-hidden className="size-4" />
      Developer panel
    </Link>
  );
}
