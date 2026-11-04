import { cn } from "@/lib/utils";

/** A titled card section of a page (an incident's, a service's). */
export function Panel({
  title,
  children,
  className,
  awaitingYou = false,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
  /** The card that waits for the human's decision gets a 2 px accent border. */
  awaitingYou?: boolean;
}) {
  const id = `panel-${title.toLowerCase().replaceAll(" ", "-")}`;
  return (
    <section
      aria-labelledby={id}
      className={cn(
        "min-w-0 rounded-card-sm bg-surface p-4 md:rounded-card md:p-6",
        awaitingYou ? "border-2 border-accent" : "border border-line",
        className,
      )}
    >
      <h2 id={id} className="mb-3 text-xl font-extrabold">
        {title}
      </h2>
      {children}
    </section>
  );
}
