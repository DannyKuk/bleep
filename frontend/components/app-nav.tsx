"use client";

import { AlertOctagon, FlaskConical, LayoutDashboard, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

const ITEMS = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/incidents", label: "Incidents", icon: AlertOctagon },
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/dev", label: "Dev", icon: FlaskConical, dev: true },
] as const;

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

export function AppNav() {
  const pathname = usePathname();
  const devPanel = useDevPanel();
  const items = ITEMS.filter((item) => !("dev" in item) || devPanel);
  return (
    <nav aria-label="Main" className="flex gap-1 overflow-x-auto">
      {items.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm font-medium whitespace-nowrap focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
              active
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon aria-hidden className="size-4" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
