import type { Metadata } from "next";

import { SiteFooter, SiteHeader } from "~/components/chrome";
import { FlowSwitch } from "~/components/flow/flow-switch";

export const metadata: Metadata = {
  title: "How an incident runs and a message is read, Bleep",
  description:
    "One incident's way through Bleep, from a failure on your host to the report, and one chat message's way from what you write to what it changes, with every way out and the steps that wait for you.",
};

export default function FlowPage() {
  return (
    <>
      <SiteHeader page="flow" />
      <main className="mx-auto w-full max-w-6xl px-4 pt-10 pb-20 sm:px-6 lg:pt-16">
        <h1 className="max-w-3xl text-[2.5rem] leading-[1.05] font-black tracking-[-0.03em] text-balance sm:text-6xl">
          How Bleep runs an incident and reads a message
        </h1>
        <FlowSwitch />
      </main>
      <SiteFooter />
    </>
  );
}
