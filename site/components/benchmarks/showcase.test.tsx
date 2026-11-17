import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import BenchmarksPage from "~/app/benchmarks/page";
import attackDetails from "~/lib/benchmarks/data/attacks.runs.json";
import faultDetails from "~/lib/benchmarks/data/faults.runs.json";
import roundDetails from "~/lib/benchmarks/data/round.runs.json";
import talkDetails from "~/lib/benchmarks/data/talk.runs.json";
import type { AgentRun, Details } from "~/lib/benchmarks/extract";
import { round } from "~/lib/benchmarks/round";
import { SUITES, listed, runsPerCase, tally } from "~/lib/benchmarks/suites";

const section = (heading: string) =>
  screen.getByRole("heading", { level: 2, name: heading }).closest("section") as HTMLElement;

const dialog = () => screen.getByRole("dialog", { hidden: true });

describe("the suites", () => {
  it("gives every suite a card with its result from the data file", () => {
    render(<BenchmarksPage />);
    const cards = section("What I tested");
    for (const suite of SUITES) {
      const card = cards.querySelector(`a[href="#${suite.kind}"]`);
      expect(card).toHaveTextContent(suite.name);
      expect(card?.textContent?.toLowerCase()).toContain(
        suite.result(suite.data.cases).toLowerCase(),
      );
    }
  });

  it("answers each suite's question with a dot for every run", () => {
    render(<BenchmarksPage />);
    for (const suite of SUITES) {
      const part = section(suite.question);
      expect(part).toHaveTextContent(suite.answer(suite.data.cases));
      expect(part).toHaveTextContent(runsPerCase(suite.data.cases));
      const dots = within(part).getAllByRole("button");
      expect(dots).toHaveLength(tally(suite.data.cases).runs);
    }
  });

  it("names the cases a suite missed in its answer", () => {
    const talk = SUITES.find((suite) => suite.kind === "talk");
    const missed = tally(talk?.data.cases ?? []).missed;
    if (missed.length) expect(talk?.answer(talk.data.cases)).toContain(listed(missed));
  });
});

describe("a dot", () => {
  it("opens its run's diagnosis, plan, reason and tool calls", async () => {
    const user = userEvent.setup();
    render(<BenchmarksPage />);
    const faults = SUITES[0];
    const first = faults.data.cases[0];
    await user.click(within(section(faults.question)).getAllByRole("button")[0]);

    const run = (faultDetails as Details)[`${first.name}/1`] as AgentRun;
    const shown = dialog();
    expect(shown).toHaveAttribute("open");
    expect(within(shown).getByRole("heading", { name: first.name })).toBeInTheDocument();
    expect(await within(shown).findByText(run.diagnosis?.rootCause ?? "")).toBeInTheDocument();
    expect(within(shown).getByText(run.plan?.reason ?? "")).toBeInTheDocument();
    expect(within(shown).getByText(`Tool calls (${run.toolCalls.length})`)).toBeInTheDocument();
    for (const call of run.toolCalls) {
      expect(within(shown).getAllByText(call.name).length).toBeGreaterThan(0);
    }

    await user.click(within(shown).getByRole("button", { name: "Close" }));
    expect(shown).not.toHaveAttribute("open");
  });

  it("shows text in angle brackets as text, never as markup", async () => {
    const user = userEvent.setup();
    render(<BenchmarksPage />);
    const attacks = SUITES.find((suite) => suite.kind === "attacks");
    const tagged = Object.entries(attackDetails as Details)
      .map(([key, run]) => [key, JSON.stringify(run).match(/<([a-z_]+)>/)?.[1]] as const)
      .find(([, tag]) => tag);
    expect(tagged, "an attack run with a <tag> in its text").toBeDefined();
    const [key, tag] = tagged ?? ["", ""];
    const [name, run] = key.split("/");
    await user.click(
      within(section(attacks?.question ?? "")).getByRole("button", {
        name: new RegExp(`^${name}, run ${run}:`),
      }),
    );
    await within(dialog()).findByText(/Tool calls/);
    expect(dialog()).toHaveTextContent(`<${tag}>`);
    expect(dialog().querySelector(tag ?? "")).toBeNull();
  });

  it("is one Tab stop per grid, and the arrow keys move between its dots", async () => {
    const user = userEvent.setup();
    render(<BenchmarksPage />);
    const dots = within(section(SUITES[0].question)).getAllByRole("button");
    expect(dots.filter((dot) => dot.tabIndex === 0)).toHaveLength(1);

    dots[0].focus();
    await user.keyboard("{ArrowRight}");
    expect(dots[1]).toHaveFocus();
    await user.keyboard("{End}");
    expect(dots.at(-1)).toHaveFocus();
    expect(dots[0].tabIndex).toBe(-1);
  });

  it("in the model table opens that model's run", async () => {
    const user = userEvent.setup();
    render(<BenchmarksPage />);
    const model = round.models[0];
    const [name] = Object.keys(model.faults);
    const table = within(section("Which model?")).getByRole("table");
    const dot = within(table).getAllByRole("button", {
      name: new RegExp(`^${model.name}, ${name}:`),
    })[0];
    await user.click(dot);

    const run = (roundDetails as Details)[`${model.scenarios}/${name}/1`] as AgentRun;
    expect(await within(dialog()).findByText(run.diagnosis?.rootCause ?? "")).toBeInTheDocument();
    expect(within(dialog()).getByText(new RegExp(model.name))).toBeInTheDocument();
  });
});

// Only a round whose case files changed after it ran has a talk run scored again; a fresh round
// has none, and extract.test.ts covers the note itself.
const [rescoredTalk] =
  Object.entries(talkDetails as Details).find(([, run]) => run.type === "talk" && run.note) ?? [];

it.skipIf(!rescoredTalk)("says when a run was scored again with today's case files", async () => {
  const user = userEvent.setup();
  render(<BenchmarksPage />);
  const talk = SUITES.find((suite) => suite.kind === "talk");
  const key = rescoredTalk;
  const [name, run] = (key ?? "").split("/");
  await user.click(
    within(section(talk?.question ?? "")).getByRole("button", {
      name: new RegExp(`^${name}, run ${run}:`),
    }),
  );
  expect(await within(dialog()).findByText(/Scored again with the case files/)).toBeVisible();
});
