"use client";

import Link from "next/link";

import { Bleep } from "@/components/bleep/bleep";
import { useOverall } from "@/components/overall-state";
import { useT } from "@/lib/i18n";
import { faceMood } from "@/lib/mood";

/** The header's home link: the head-only Bleep, its eyes in the overall state's colour (and its
 * pointing face while Bleep points). Until the first read answers, the eyes are the grey of "not
 * sure", which is no state at all. */
export function HomeLogo() {
  const { state, mood } = useOverall();
  const t = useT().shell;
  const words = t.state[state ?? "checking"];
  return (
    <Link
      href="/"
      title={t.homeTitle(words)}
      data-state={state ?? "unknown"}
      className="-ml-1 inline-flex min-h-11 items-center gap-2 rounded-full pr-2 pl-1 text-lg font-extrabold focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <Bleep size={30} mood={state && mood ? faceMood(state, mood) : "not_sure"} />
      Bleep
      <span className="sr-only">{t.homeName(words)}</span>
    </Link>
  );
}
