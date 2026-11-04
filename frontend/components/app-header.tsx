"use client";

import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

/** An incident's own page, which on a phone has a small header of its own. */
const INCIDENT_PAGE = /^\/incidents\/[^/]+$/;

/**
 * The app's header. Below the desktop breakpoint an incident's page shows only its own small
 * header (back, subject, theme), so the plan and the decision bar pinned under it have the
 * screen; every other page, and every page on a desktop, keeps this one.
 */
export function AppHeader({ children }: { children: React.ReactNode }) {
  const ownHeader = INCIDENT_PAGE.test(usePathname());
  return <header className={cn("print:hidden", ownHeader && "max-lg:hidden")}>{children}</header>;
}
