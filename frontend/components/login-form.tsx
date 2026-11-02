"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, apiFetch } from "@/lib/api";

/** Only same-site paths: "//host" and "https://host" would make login an open redirect. */
function safeNext(next: string): string {
  return next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") ? next : "/";
}

function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "wrong password";
    if (error.status === 429) {
      return error.retryAfter
        ? `too many attempts, try again in ${error.retryAfter} s`
        : "too many attempts, try again later";
    }
  }
  return "could not reach the server, try again";
}

export function LoginForm({ next }: { next: string }) {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await apiFetch("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ password }),
        redirectOn401: false,
      });
      router.replace(safeNext(next));
    } catch (e) {
      setError(messageFor(e));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <label htmlFor="password" className="text-sm font-medium">
        Password
      </label>
      <Input
        id="password"
        type="password"
        autoComplete="current-password"
        autoFocus
        required
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        aria-invalid={error ? true : undefined}
      />
      {error && (
        <p role="alert" className="text-sm text-health-down">
          {error}
        </p>
      )}
      <Button type="submit" disabled={busy || password === ""}>
        {busy && <Loader2 aria-hidden className="animate-spin" />}
        Log in
      </Button>
    </form>
  );
}
