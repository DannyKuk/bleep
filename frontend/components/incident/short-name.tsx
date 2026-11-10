import { cn } from "@/lib/utils";

/** An incident's short name as Telegram shows it ("shop #14"): quiet, in mono. */
export function ShortName({ name, className }: { name: string; className?: string }) {
  return <span className={cn("font-mono font-semibold text-ink-2", className)}>{name}</span>;
}

/** The short name and " · " in front of what names the incident; nothing when it has none. */
export function ShortNamePrefix({ name }: { name: string | null }) {
  if (!name) return null;
  return (
    <>
      <ShortName name={name} />
      {" · "}
    </>
  );
}
