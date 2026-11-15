"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Bleep, type Mood } from "@/components/bleep/bleep";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, apiFetch } from "@/lib/api";
import { type Messages, useT } from "@/lib/i18n";
import { safeNext } from "@/lib/safe-next";

/** How long Bleep looks unsure after a failed login before it rests again. */
export const DOUBT_MS = 2500;

function messageFor(error: unknown, t: Messages["login"]): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return t.wrongPassword;
    if (error.status === 429) {
      return error.retryAfter ? t.tooManySoon(error.retryAfter) : t.tooManyLater;
    }
  }
  return t.unreachable;
}

/** The login card: Bleep above it, whose mood follows the form, then the title and the form. */
export function LoginForm({ next }: { next: string }) {
  const router = useRouter();
  const t = useT().login;
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [doubt, setDoubt] = useState(false);
  const mood: Mood = busy ? "working" : doubt ? "not_sure" : "resting";

  // The metadata title is fixed at build time, so the title in the person's language is set here.
  useEffect(() => {
    document.title = t.title;
  }, [t.title]);

  useEffect(() => {
    if (!doubt) return;
    const timer = setTimeout(() => setDoubt(false), DOUBT_MS);
    return () => clearTimeout(timer);
  }, [doubt]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setDoubt(false);
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
      setDoubt(true);
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-center gap-2">
      <Bleep mood={mood} size={112} className="size-[96px] sm:size-[112px]" />
      <section
        aria-labelledby="login-title"
        className="flex w-full flex-col gap-6 rounded-card-sm border border-line bg-surface p-5 sm:rounded-card sm:p-8"
      >
        <div className="flex flex-col gap-2 text-center">
          <h1
            id="login-title"
            className="text-[26px] leading-tight font-extrabold tracking-tight sm:text-[30px]"
          >
            Bleep
          </h1>
          <p className="text-[15px] leading-normal hyphens-auto text-ink-2">{t.tagline}</p>
        </div>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <label htmlFor="password" className="text-sm font-bold">
            {t.password}
          </label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            autoFocus
            required
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              setDoubt(false);
            }}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "login-error" : undefined}
          />
          {error && (
            <p id="login-error" role="alert" className="text-sm font-semibold text-health-down">
              {error}
            </p>
          )}
          <Button type="submit" className="mt-2 w-full" disabled={busy || password === ""}>
            {busy && <Loader2 aria-hidden className="animate-spin" />}
            {t.submit}
          </Button>
        </form>
      </section>
    </div>
  );
}
