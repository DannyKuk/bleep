"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, apiFetch } from "@/lib/api";
import { type Messages, useT } from "@/lib/i18n";
import { safeNext } from "@/lib/safe-next";

function messageFor(error: unknown, t: Messages["login"]): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return t.wrongPassword;
    if (error.status === 429) {
      return error.retryAfter ? t.tooManySoon(error.retryAfter) : t.tooManyLater;
    }
  }
  return t.unreachable;
}

export function LoginForm({ next }: { next: string }) {
  const router = useRouter();
  const t = useT().login;
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  // The metadata title is fixed at build time, so the title in the person's language is set here.
  useEffect(() => {
    document.title = t.title;
  }, [t.title]);

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
      setError(messageFor(e, t));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <label htmlFor="password" className="text-sm font-medium">
        {t.password}
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
        aria-describedby={error ? "login-error" : undefined}
      />
      {error && (
        <p id="login-error" role="alert" className="text-sm text-health-down">
          {error}
        </p>
      )}
      <Button type="submit" disabled={busy || password === ""}>
        {busy && <Loader2 aria-hidden className="animate-spin" />}
        {t.submit}
      </Button>
    </form>
  );
}
