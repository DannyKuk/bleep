import Link from "next/link";

import { Bleep } from "@/components/bleep/bleep";

export const REPO = "https://github.com/DannyKuk/bleep";
export const blob = (path: string) => `${REPO}/blob/main/${path}`;

/** The sticky header of every page. `home` says whether this is the landing page, where the
 * section links stay on the page instead of leading back to it. */
export function SiteHeader({ home = false }: { home?: boolean }) {
  const section = (id: string) => (home ? `#${id}` : `/#${id}`);
  return (
    <header className="site-header sticky top-0 z-40 border-b border-line bg-bg">
      <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <Link href={home ? "#" : "/"} className="flex items-center gap-2 text-lg font-extrabold">
          <Bleep mood="all_good" size={30} />
          Bleep
        </Link>
        <nav aria-label="Page" className="flex items-center gap-1 text-sm font-bold text-ink-2">
          <Link
            href={section("how")}
            className="hidden rounded-full px-3 py-2 hover:bg-soft sm:block"
          >
            How it works
          </Link>
          <Link
            href={section("safety")}
            className="hidden rounded-full px-3 py-2 hover:bg-soft sm:block"
          >
            Safety
          </Link>
          <Link
            href="/flow"
            aria-current={home ? undefined : "page"}
            className="rounded-full px-3 py-2 hover:bg-soft aria-[current=page]:text-ink"
          >
            Flow
          </Link>
          <a
            href={REPO}
            className="rounded-full bg-ink px-4 py-2 text-bg transition-opacity hover:opacity-85"
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
