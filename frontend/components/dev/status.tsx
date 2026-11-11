import { Loader2, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import { FIXED_LOCALE, MESSAGES } from "@/lib/i18n";
import type { Language } from "@/lib/settings-types";

export const why = (error: unknown) => (error instanceof Error ? error.message : String(error));

export function Failed({ children }: { children: React.ReactNode }) {
  return (
    <p role="alert" className="flex items-center gap-2 text-xs text-health-down">
      <TriangleAlert aria-hidden className="size-3.5" />
      {children}
    </p>
  );
}

export function Loading({ children }: { children: React.ReactNode }) {
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 aria-hidden className="size-4 animate-spin" />
      {children}
    </p>
  );
}

export interface Loaded<T> {
  data?: T;
  error?: Error;
}

/** One GET of `path`, read again when `path` or `reload` changes. */
export function useGet<T>(path: string, reload = 0): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({});
  useEffect(() => {
    const controller = new AbortController();
    apiFetch<T>(path, { signal: controller.signal })
      .then((data) => setState({ data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ error: error instanceof Error ? error : new Error(String(error)) });
      });
    return () => controller.abort();
  }, [path, reload]);
  return state;
}

/** "$0.38", or "$0.0042" below a cent (German "$0,38"); "unknown" when no call had a price. */
export function formatUsd(usd: number | null, language: Language): string {
  if (usd === null) return MESSAGES[language].dev.costs.unknown;
  const digits = usd > 0 && usd < 0.01 ? 4 : 2;
  const amount = usd.toLocaleString(language === "en" ? "en-US" : FIXED_LOCALE[language], {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    useGrouping: false,
  });
  return `$${amount}`;
}
