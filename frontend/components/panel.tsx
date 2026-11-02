import { cn } from "@/lib/utils";

/** A titled card section of a page (an incident's, a service's). */
export function Panel({
  title,
  children,
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  const id = `panel-${title.toLowerCase().replaceAll(" ", "-")}`;
  return (
    <section
      aria-labelledby={id}
      className={cn("min-w-0 rounded-lg border border-border bg-card", className)}
    >
      <h2 id={id} className="px-4 py-3 text-sm font-semibold">
        {title}
      </h2>
      <div className="border-t border-border px-4 py-3">{children}</div>
    </section>
  );
}
