"use client";

import { useRef, useSyncExternalStore, type KeyboardEvent, type ReactNode } from "react";

import { blob, REPO } from "~/components/chrome";

import { ChatDiagram } from "./chat-diagram";
import { FlowDiagram } from "./flow-diagram";

export type View = "incident" | "message";

const VIEWS: { view: View; label: string }[] = [
  { view: "incident", label: "An incident" },
  { view: "message", label: "A message" },
];
/** No element has this id, so the browser never scrolls to it by itself. */
const MESSAGE_HASH = "#message";
const HEADER = 64;
const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

const tabId = (view: View) => `flow-tab-${view}`;

const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("hashchange", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("hashchange", listener);
  };
}

const viewInUrl = (): View => (window.location.hash === MESSAGE_HASH ? "message" : "incident");
/** The static page is built with the incident; a `#message` link switches once it loads. */
const viewOnServer = (): View => "incident";

/** Puts the view in the URL without a jump or a new history entry; `replaceState` fires no
 * `hashchange`, so the switch tells its own listeners. */
function show(view: View) {
  const { pathname, search } = window.location;
  window.history.replaceState(null, "", view === "message" ? MESSAGE_HASH : pathname + search);
  listeners.forEach((listener) => listener());
}

/**
 * The switch at the top of `/flow` between the two diagrams, one at a time, the incident first.
 * It is a pair of tabs, and the choice lives in the URL hash, so `/flow#message` opens the chat.
 */
export function FlowSwitch() {
  const view = useSyncExternalStore(subscribe, viewInUrl, viewOnServer);
  const tabs = useRef<Partial<Record<View, HTMLButtonElement | null>>>({});

  const choose = (next: View) => {
    show(next);
    tabs.current[next]?.focus();
  };

  const onKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = VIEWS.findIndex((item) => item.view === view);
    const moves: Record<string, number> = {
      ArrowRight: index + 1,
      ArrowLeft: index - 1,
      Home: 0,
      End: VIEWS.length - 1,
    };
    const to = moves[event.key];
    if (to === undefined) return;
    event.preventDefault();
    choose(VIEWS[(to + VIEWS.length) % VIEWS.length]!.view);
  };

  /** The way into the incident loop: the switch moves to it, and the page goes back up to the
   * switch only if it is above the screen, since the visitor pressed this far down. */
  const intoIncident = () => {
    show("incident");
    const tab = tabs.current.incident;
    tab?.focus({ preventScroll: true });
    const top = tab?.getBoundingClientRect().top ?? 0;
    if (top >= HEADER) return;
    const reduced = window.matchMedia?.(REDUCED_MOTION).matches ?? false;
    window.scrollBy({ top: top - HEADER - 16, behavior: reduced ? "auto" : "smooth" });
  };

  return (
    <>
      <div
        role="tablist"
        aria-label="Which way to show"
        className="mt-8 inline-flex rounded-full border border-line bg-soft p-1"
      >
        {VIEWS.map((item) => {
          const selected = item.view === view;
          return (
            <button
              key={item.view}
              ref={(button) => {
                tabs.current[item.view] = button;
              }}
              type="button"
              role="tab"
              id={tabId(item.view)}
              aria-selected={selected}
              aria-controls="flow-view"
              tabIndex={selected ? 0 : -1}
              onClick={() => choose(item.view)}
              onKeyDown={onKey}
              className={`h-11 rounded-full px-5 font-bold transition-colors ${
                selected
                  ? "bg-surface text-ink shadow-sm ring-1 ring-line"
                  : "text-ink-2 hover:text-ink"
              }`}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      <div id="flow-view" role="tabpanel" aria-labelledby={tabId(view)}>
        {view === "incident" ? (
          <>
            <Intro>
              From a failure on your host to the report. The steps and every way between them are
              the ones Bleep&apos;s incident graph wires, and a test keeps this page and the graph
              the same.
            </Intro>
            <div className="mt-12 sm:mt-16">
              <FlowDiagram />
            </div>
            <Outro>
              Every step in more detail, with the code that runs it, is in the{" "}
              <TextLink href={blob("docs/architecture.md#one-incident-start-to-end")}>
                architecture
              </TextLink>
              , and the graph itself is{" "}
              <TextLink href={blob("backend/src/bleep/graph/build.py")}>one short file</TextLink>.
            </Outro>
          </>
        ) : (
          <>
            <Intro>
              From what you write in Telegram or the dashboard&apos;s chat to what it changes. A
              message alone never changes anything: code checks what you asked for, and only your
              press of Yes runs it. A test keeps this page and the chat&apos;s kinds and actions the
              same.
            </Intro>
            <div className="mt-12 sm:mt-16">
              <ChatDiagram onIncident={intoIncident} />
            </div>
            <Outro>
              Every rule of the chat, with the code behind it, is in the{" "}
              <TextLink href={blob("docs/design/notify.md#talking-to-bleep-390")}>
                design notes
              </TextLink>
              , and the code is{" "}
              <TextLink href={`${REPO}/tree/main/backend/src/bleep/talk`}>one folder</TextLink>.
            </Outro>
          </>
        )}
      </div>
    </>
  );
}

function Intro({ children }: { children: ReactNode }) {
  return <p className="mt-6 max-w-2xl text-lg leading-relaxed text-ink-2 sm:text-xl">{children}</p>;
}

function Outro({ children }: { children: ReactNode }) {
  return <p className="mt-16 max-w-2xl leading-relaxed text-ink-2">{children}</p>;
}

function TextLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} className="font-bold text-accent underline-offset-4 hover:underline">
      {children}
    </a>
  );
}
