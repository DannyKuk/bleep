import { Loader2, TriangleAlert } from "lucide-react";

/** A page section's failed read, as an alert. */
export function Failed({ children }: { children: React.ReactNode }) {
  return (
    <p role="alert" className="flex items-center gap-2 text-sm text-down">
      <TriangleAlert aria-hidden className="size-4" />
      {children}
    </p>
  );
}

/** A page section's read in flight, as a status. */
export function Loading({ children }: { children: React.ReactNode }) {
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-ink-2">
      <Loader2 aria-hidden className="size-4 animate-spin" />
      {children}
    </p>
  );
}
