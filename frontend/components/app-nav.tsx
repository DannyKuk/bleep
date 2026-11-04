"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const ITEMS = [
  { href: "/", label: "Home" },
  { href: "/incidents", label: "Incidents" },
  { href: "/settings", label: "Settings" },
] as const;

/** The main nav as one pill group; the developer panel is reached from Settings, not from here. */
export function AppNav() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Main"
      className="inline-flex max-w-full gap-1 overflow-x-auto rounded-full border border-line bg-surface p-1"
    >
      {ITEMS.map(({ href, label }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "inline-flex h-11 items-center rounded-full px-4 text-[15px] font-bold whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none pointer-fine:h-9",
              active ? "bg-ink text-surface" : "text-ink-2 hover:bg-soft hover:text-ink",
            )}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
