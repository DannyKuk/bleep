"use client";

import { LogOut } from "lucide-react";

import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

export function LogoutButton() {
  const label = useT().shell.logOut;
  async function logout() {
    try {
      await apiFetch("/api/auth/logout", { method: "POST", redirectOn401: false });
    } finally {
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full load drops client state
      window.location.assign("/login");
    }
  }
  return (
    <Button variant="ghost" size="sm" onClick={logout}>
      <LogOut aria-hidden />
      <span className="hidden sm:inline">{label}</span>
      <span className="sr-only sm:hidden">{label}</span>
    </Button>
  );
}
