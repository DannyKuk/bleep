import { Siren } from "lucide-react";
import type { Metadata } from "next";

import { LoginForm } from "@/components/login-form";

export const metadata: Metadata = { title: "Log in · Bleep" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const { next } = await searchParams;
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-sm flex-col justify-center gap-6 px-4">
      <div className="flex items-center gap-2">
        <Siren aria-hidden className="size-5" />
        <h1 className="text-lg font-semibold">Bleep</h1>
      </div>
      <LoginForm next={typeof next === "string" ? next : "/"} />
    </main>
  );
}
