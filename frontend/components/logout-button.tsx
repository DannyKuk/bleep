"use client";

import { LogOut } from "lucide-react";

import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";

export function LogoutButton() {
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
      <span className="hidden sm:inline">Log out</span>
      <span className="sr-only sm:hidden">Log out</span>
    </Button>
  );
}
