"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const ITEMS = [
  { href: "/", key: "home" },
  { href: "/incidents", key: "incidents" },
  { href: "/usage", key: "usage" },
  { href: "/settings", key: "settings" },
] as const;

/** The main nav as one pill group; the developer panel is reached from Settings, not from here. */
export function AppNav() {
  const pathname = usePathname();
  const t = useT().nav;
  return (
    <nav
      aria-label={t.label}
      className="flex w-full max-w-full gap-0.5 overflow-x-auto rounded-full border border-line bg-surface p-1 sm:inline-flex sm:w-auto sm:gap-1"
    >
      {ITEMS.map(({ href, key }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "inline-flex h-11 flex-1 items-center justify-center rounded-full px-1.5 text-sm font-bold whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none sm:flex-none sm:px-4 sm:text-[15px] pointer-fine:h-9",
              active ? "bg-ink text-surface" : "text-ink-2 hover:bg-soft hover:text-ink",
            )}
          >
            {t[key]}
          </Link>
        );
      })}
    </nav>
  );
}
