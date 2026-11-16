import { Bleep } from "@/components/bleep/bleep";

export const REPO = "https://github.com/DannyKuk/bleep";
export const blob = (path: string) => `${REPO}/blob/main/${path}`;

type Page = "home" | "flow" | "benchmarks";

const PAGES: { page: Exclude<Page, "home">; href: string; label: string }[] = [
  { page: "flow", href: "/flow", label: "Flow" },
  { page: "benchmarks", href: "/benchmarks", label: "Benchmarks" },
];

/** The sticky header of every page. On the landing page the section links stay on the page
 * instead of leading back to it, and every other page marks its own link as the current one.
 *
 * The links are plain anchors, not `next/link`: on a client-side move Next scrolls to the first
 * element below a sticky header, so a new page opened 65 px down, under the header. Static pages
 * load fast enough whole, and the browser then starts each at the top. */
export function SiteHeader({ page }: { page: Page }) {
  const home = page === "home";
  const section = (id: string) => (home ? `#${id}` : `/#${id}`);
  return (
    <header className="site-header sticky top-0 z-40 border-b border-line bg-bg">
      <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between gap-3 px-4 sm:px-6">
        <a
          href={home ? "#" : "/"}
          aria-label="Bleep, home"
          className="flex items-center gap-2 text-lg font-extrabold"
        >
          <Bleep mood="all_good" size={30} />
          <span className="hidden min-[400px]:inline">Bleep</span>
        </a>
        <nav aria-label="Page" className="flex items-center gap-0.5 text-sm font-bold text-ink-2">
          <a href={section("how")} className="hidden rounded-full px-3 py-2 hover:bg-soft sm:block">
            How it works
          </a>
          <a
            href={section("safety")}
            className="hidden rounded-full px-3 py-2 hover:bg-soft sm:block"
          >
            Safety
          </a>
          {PAGES.map((link) => (
            <a
              key={link.page}
              href={link.href}
              aria-current={page === link.page ? "page" : undefined}
              className="rounded-full px-2.5 py-2 hover:bg-soft aria-[current=page]:text-ink sm:px-3"
            >
              {link.label}
            </a>
          ))}
          <a
            href={REPO}
            className="ml-1 rounded-full bg-ink px-3.5 py-2 text-bg transition-opacity hover:opacity-85 sm:px-4"
          >
            GitHub
          </a>
        </nav>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="border-t border-line">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-10 text-sm text-ink-2 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="flex items-center gap-3">
          <Bleep mood="resting" size={44} />
          <p>
            Built by{" "}
            <a href="https://dkwebsite.de" className="font-bold text-ink hover:underline">
              Danny Kukic
            </a>
            . Open source under the MIT licence.
          </p>
        </div>
        <nav aria-label="Footer" className="flex flex-wrap gap-x-5 gap-y-2 font-semibold">
          <a href={REPO} className="hover:text-ink">
            GitHub
          </a>
          <a href={blob("docs/architecture.md")} className="hover:text-ink">
            Architecture
          </a>
          <a href="https://dkwebsite.de/imprint" className="hover:text-ink">
            Legal notice
          </a>
          <a href="https://dkwebsite.de/privacy" className="hover:text-ink">
            Privacy
          </a>
        </nav>
      </div>
    </footer>
  );
}
