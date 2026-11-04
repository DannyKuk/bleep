"use client";

import Link from "next/link";

import { Bleep } from "@/components/bleep/bleep";
import { useOverall } from "@/components/overall-state";
import { stateMood, type OverallState } from "@/lib/mood";

const STATE_WORDS: Record<OverallState, string> = {
  red: "something needs you",
  amber: "Bleep is working on something",
  green: "all good",
};

/** The header's home link: the head-only Bleep, its eyes in the overall state's colour. Until the
 * first read answers, the eyes are the grey of "not sure", which is no state at all. */
export function HomeLogo() {
  const { state } = useOverall();
  const words = state ? STATE_WORDS[state] : "checking";
  return (
    <Link
      href="/"
      title={`Bleep: ${words}`}
      data-state={state ?? "unknown"}
      className="-ml-1 inline-flex min-h-11 items-center gap-2 rounded-full pr-2 pl-1 text-lg font-extrabold focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <Bleep size={30} mood={state ? stateMood(state) : "not_sure"} />
      Bleep
      <span className="sr-only">, home ({words})</span>
    </Link>
  );
}
