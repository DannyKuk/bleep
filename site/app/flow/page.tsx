import type { Metadata } from "next";

import { SiteFooter, SiteHeader, blob } from "~/components/chrome";
import { FlowDiagram } from "~/components/flow/flow-diagram";

export const metadata: Metadata = {
  title: "How one incident runs, Bleep",
  description:
    "One incident's way through Bleep, from a failure on your host to the report, with every loop back and the one step that waits for you.",
};

export default function FlowPage() {
  return (
    <>
      <SiteHeader page="flow" />
      <main className="mx-auto w-full max-w-6xl px-4 pt-10 pb-20 sm:px-6 lg:pt-16">
        <h1 className="max-w-3xl text-[2.5rem] leading-[1.05] font-black tracking-[-0.03em] text-balance sm:text-6xl">
          How one incident runs
        </h1>
        <p className="mt-6 max-w-2xl text-lg leading-relaxed text-ink-2 sm:text-xl">
          From a failure on your host to the report. The steps and every way between them are the
          ones Bleep&apos;s incident graph wires, and a test keeps this page and the graph the same.
        </p>
        <div className="mt-12 sm:mt-16">
          <FlowDiagram />
        </div>
        <p className="mt-16 max-w-2xl leading-relaxed text-ink-2">
          Every step in more detail, with the code that runs it, is in the{" "}
          <a
            href={blob("docs/architecture.md#one-incident-start-to-end")}
            className="font-bold text-accent underline-offset-4 hover:underline"
          >
            architecture
          </a>
          , and the graph itself is{" "}
          <a
            href={blob("backend/src/bleep/graph/build.py")}
            className="font-bold text-accent underline-offset-4 hover:underline"
          >
            one short file
          </a>
          .
        </p>
      </main>
      <SiteFooter />
    </>
  );
}
