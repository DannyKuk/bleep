import { describe, expect, it } from "vitest";

import { type Read, extract, talkMessage } from "./extract";

const DATA = "docs/evals/data";
const jsonl = (...rows: object[]) => rows.map((row) => JSON.stringify(row)).join("\n");
const files =
  (contents: Record<string, string>): Read =>
  (file) =>
    contents[file] ?? null;

const scenario = (id: string, more: object = {}) => ({
  id,
  run: 1,
  diagnosis: { root_cause: "x", category: "config", confidence: 0.9, unsure: false },
  proposal: { kind: "no_action", steps: [], reason: "r", human_instructions: null },
  root_cause_score: null,
  root_cause_pass: false,
  action_correct: true,
  unnecessary_action: false,
  tool_calls: [{ name: "inspect_container", args: '{"container": "shop-web"}' }],
  failure: null,
  error: null,
  ...more,
});

const info = (ids: string[]) =>
  JSON.stringify({
    model: "m",
    commit: "c0ffee",
    judge: "manual",
    started_at: "2026-11-09T07:58:10Z",
    scenarios: ids.map((id) => ({ id, expected: { root_cause: `${id} broke` } })),
  });

const outcomes = (data: { cases: { name: string; runs: string[] }[] }) =>
  Object.fromEntries(data.cases.map((c) => [c.name, c.runs]));

describe("faults", () => {
  const batch = "scenarios-20261109-075810";

  it("takes the root cause from the person's grade over the stored verdict", () => {
    const read = files({
      [`${DATA}/${batch}.info.json`]: info(["a", "b"]),
      [`${DATA}/${batch}.jsonl`]: jsonl(scenario("a"), scenario("b", { action_correct: false })),
      [`${DATA}/${batch}.grades.jsonl`]: jsonl(
        { run_id: "a/1", root_cause_pass: true, note: "names it" },
        { run_id: "b/1", root_cause_pass: true },
      ),
    });
    const { data, details } = extract("faults", read, [batch]);
    expect(outcomes(data)).toEqual({ a: ["pass"], b: ["fail"] });
    expect(details["a/1"]).toMatchObject({ note: "names it", expected: "a broke" });
    expect(details["a/1"].type === "agent" && details["a/1"].toolCalls).toEqual([
      { name: "inspect_container", args: "container: shop-web" },
    ]);
  });

  it("refuses a run whose root cause no one judged", () => {
    const read = files({
      [`${DATA}/${batch}.info.json`]: info(["a"]),
      [`${DATA}/${batch}.jsonl`]: jsonl(scenario("a")),
    });
    expect(() => extract("faults", read, [batch])).toThrow(/never judged/);
  });

  it("counts a run that ended early as a miss, with nothing to judge", () => {
    const read = files({
      [`${DATA}/${batch}.info.json`]: info(["a"]),
      [`${DATA}/${batch}.jsonl`]: jsonl(
        scenario("a", { failure: "timeout", diagnosis: null, proposal: null }),
      ),
    });
    const { data, details } = extract("faults", read, [batch]);
    expect(outcomes(data)).toEqual({ a: ["fail"] });
    expect(details["a/1"]).toMatchObject({ failure: "timeout", diagnosis: null, plan: null });
  });

  it("refuses a run that two batches both hold", () => {
    const other = "scenarios-20261109-080000";
    const read = files({
      [`${DATA}/${batch}.info.json`]: info(["a"]),
      [`${DATA}/${batch}.jsonl`]: jsonl(
        scenario("a", { root_cause_score: 1, root_cause_pass: true }),
      ),
      [`${DATA}/${other}.info.json`]: info(["a"]),
      [`${DATA}/${other}.jsonl`]: jsonl(
        scenario("a", { root_cause_score: 1, root_cause_pass: true }),
      ),
    });
    expect(() => extract("faults", read, [batch, other])).toThrow(/more than one batch/);
  });
});

describe("attacks", () => {
  const batch = "injection-20261116-120949";
  const attack = (name: string, passed: boolean, reached: unknown, run = 1) => ({
    case: name,
    kind: "attack",
    channel: "logs",
    passed,
    payload_reached: reached,
    run: { run, model: "m", diagnosis: null, proposal: null, tool_calls: [], failure: null },
  });
  const control = (passed: boolean) => ({
    ...attack("control-db-down", passed, true),
    kind: "control",
    channel: "logs",
  });

  it("counts an attack whose payload never reached the model apart, never as resisted", () => {
    const read = files({
      [`${DATA}/${batch}.jsonl`]: jsonl(
        attack("seen", true, true),
        attack("unseen", true, false),
        control(true),
      ),
    });
    const { data } = extract("attacks", read, [batch]);
    expect(outcomes(data)).toEqual({
      "control-db-down": ["pass"],
      seen: ["pass"],
      unseen: ["unmet"],
    });
    expect(data.cases.map((c) => c.group)).toEqual(["control", "logs", "logs"]);
  });

  it("refuses an attack run that does not say whether its payload reached the model", () => {
    const read = files({ [`${DATA}/${batch}.jsonl`]: jsonl(attack("old", true, null)) });
    expect(() => extract("attacks", read, [batch])).toThrow(/reached the model/);
  });

  it("takes the verdict of the rendered table, which scores with today's rules", () => {
    const read = files({
      [`${DATA}/${batch}.jsonl`]: jsonl(attack("rescored", false, true)),
      [`${DATA}/${batch}.md`]:
        "<!-- m, commit b7364fa2, 2026-11-16 -->\n| `rescored` | logs | 1 of 1 | 1 of 1 |",
    });
    const { data } = extract("attacks", read, [batch]);
    expect(outcomes(data)).toEqual({ rescored: ["pass"] });
    expect(data.commit).toBe("b7364fa2");
  });

  it("refuses a mixed case whose raw runs disagree with the table's count", () => {
    const read = files({
      [`${DATA}/${batch}.jsonl`]: jsonl(
        attack("mixed", false, true, 1),
        attack("mixed", false, true, 2),
      ),
      [`${DATA}/${batch}.md`]: "| `mixed` | logs | 1 of 2 | 2 of 2 |",
    });
    expect(() => extract("attacks", read, [batch])).toThrow(/disagree/);
  });
});

describe("loop back", () => {
  const batch = "loopback-20261109-082230";
  const row = (passed: boolean | null) => ({
    run_id: "verify-failed-oom/1",
    case: "verify-failed-oom",
    kind: "verify_failed",
    passed,
    repeated_fix: [],
    tool_calls: [{ name: "list_containers", args: {} }],
    run: { model: "m", diagnosis: null, proposal: null, tool_calls: [], failure: null },
  });

  it("takes a waiting verdict from the grade, and the tool calls from the round it measures", () => {
    const read = files({
      [`${DATA}/${batch}.jsonl`]: jsonl(row(null)),
      [`${DATA}/${batch}.grades.jsonl`]: jsonl({
        run_id: "verify-failed-oom/1",
        root_cause_pass: false,
      }),
    });
    const { data, details } = extract("loopback", read, [batch]);
    expect(outcomes(data)).toEqual({ "verify-failed-oom": ["fail"] });
    expect(details["verify-failed-oom/1"]).toMatchObject({
      toolCalls: [{ name: "list_containers", args: "" }],
    });
  });

  it("refuses a waiting verdict with no grade", () => {
    const read = files({ [`${DATA}/${batch}.jsonl`]: jsonl(row(null)) });
    expect(() => extract("loopback", read, [batch])).toThrow(/never graded/);
  });
});

it("counts a scripted plan that never wrote the pushed step as not exercised", () => {
  const batch = "scripted-propose-20261116-102720";
  const run = (name: string, passed: boolean | null) => ({
    run_id: `${name}/1`,
    case: name,
    model: "m",
    passed,
    tool_calls: [],
  });
  const read = files({
    [`${DATA}/${batch}.jsonl`]: jsonl(run("held", true), run("broke", false), run("idle", null)),
  });
  expect(outcomes(extract("scripted", read, [batch]).data)).toEqual({
    broke: ["fail"],
    held: ["pass"],
    idle: ["unmet"],
  });
});

it("counts a docs search as a hit only in the top five, and names a repeated page apart", () => {
  const batch = "knowledge-20261103-203626";
  const question = (page: string, rank: number | null) => ({
    question: "q",
    page,
    rank,
    found: [],
  });
  const read = files({
    [`${DATA}/${batch}.jsonl`]: jsonl(
      { totals: {} },
      question("restart", 1),
      question("restart", 5),
      question("volumes", 6),
      question("logs", null),
    ),
  });
  const { data } = extract("docs", read, [batch]);
  expect(outcomes(data)).toEqual({
    logs: ["fail"],
    restart: ["pass"],
    "restart (2)": ["pass"],
    volumes: ["fail"],
  });
  expect(data.model).toBeNull();
});

it("reads a talk case's message as plain or quoted YAML", () => {
  expect(talkMessage("id: a\ntext: wie geht's shop?\n")).toBe("wie geht's shop?");
  expect(talkMessage("text: 'it''s down'")).toBe("it's down");
  expect(talkMessage('text: "say \\"hi\\""')).toBe('say "hi"');
});

describe("talk", () => {
  const batch = "talk-20261113-121701";
  const row = (name: string, passed: boolean) => ({
    run_id: `${name}/1`,
    case: name,
    lang: "de",
    model: "m",
    kind: "question",
    answer: "Alles läuft.",
    kind_ok: true,
    action_ok: null,
    words_missing: passed ? [] : [["laufen"]],
    language_ok: true,
    passed,
    error: null,
  });
  const cases = {
    "backend/evals/talk/cases/fixed.yaml": "text: wie geht's?",
    "backend/evals/talk/cases/kept.yaml": "text: und M1?",
  };

  it("takes the verdict of the rendered run table, and drops the checks it overturned", () => {
    const read = files({
      ...cases,
      [`${DATA}/${batch}.jsonl`]: jsonl(row("fixed", false), row("kept", false)),
      [`${DATA}/${batch}.md`]:
        "| Run | Kind | Action | Answer | Passed |\n|---|---|---|---|---|\n" +
        "| `fixed/1` | question |  | Alles läuft. | yes |\n" +
        "| `kept/1` | question |  | Alles läuft. | no |\n",
    });
    const { data, details } = extract("talk", read, [batch]);
    expect(outcomes(data)).toEqual({ fixed: ["pass"], kept: ["fail"] });
    expect(details["fixed/1"]).toMatchObject({ checks: [], note: expect.stringMatching(/passes/) });
    expect(details["kept/1"]).toMatchObject({ note: null });
    expect(details["kept/1"].type === "talk" && details["kept/1"].checks.length).toBe(3);
  });

  it("refuses a rendered table that lacks one of the batch's runs", () => {
    const read = files({
      ...cases,
      [`${DATA}/${batch}.jsonl`]: jsonl(row("fixed", true), row("kept", true)),
      [`${DATA}/${batch}.md`]: "| `fixed/1` | question |  | Alles läuft. | yes |\n",
    });
    expect(() => extract("talk", read, [batch])).toThrow(/no row for kept\/1/);
  });
});
