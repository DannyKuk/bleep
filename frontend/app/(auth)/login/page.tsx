import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { LoginForm } from "@/components/login-form";
import { loginIsOff } from "@/lib/login-off";

export const metadata: Metadata = { title: "Log in · Bleep" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  if (await loginIsOff()) redirect("/"); // a proxy in front asks for the login (#611)
  const { next } = await searchParams;
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col justify-center px-4 py-10">
      <LoginForm next={typeof next === "string" ? next : "/"} />
    </main>
  );
}
