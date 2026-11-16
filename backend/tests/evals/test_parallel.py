"""The launcher that runs the demo-shop evals on several shop copies at once (#657), on a fake
`docker` and fake runners: no engine is started."""

import argparse
import asyncio
import sys
from datetime import UTC, datetime

import pytest

from bleep.hosts.config import load_hosts_file
from evals.injection import InjectionResult
from evals.injection import append_jsonl as inj_append
from evals.parallel import (
    HOSTS_FILE,
    Engine,
    Engines,
    Share,
    batches_since,
    chosen,
    hosts_file_for,
    main,
    publish_injection,
    publish_scenarios,
    run_share,
    runner_commands,
    split,
)
from evals.report import RunInfo, append_jsonl, read_info, write_info
from evals.run import EvalError
from evals.scenarios import ScenarioResult, load_scenarios


def args(**fields) -> argparse.Namespace:
    base = {"repeat": 1, "judge": None, "model": None, "label": "", "scenarios": None,
            "injection": None, "engines": 4}  # fmt: skip
    return argparse.Namespace(**(base | fields))


def test_cases_are_dealt_out_in_turn_scenarios_first():
    shares = split(["a", "b", "c"], ["x", "y"], 2)
    assert [(s.scenarios, s.injection) for s in shares] == [(["a", "c"], ["y"]), (["b"], ["x"])]


def test_fewer_cases_than_engines_start_fewer_engines():
    assert len(split(["a"], ["x"], 6)) == 2


def test_each_copys_hosts_file_reaches_its_shop_on_its_own_port(tmp_path):
    text = hosts_file_for(HOSTS_FILE.read_text(encoding="utf-8"), 18083)
    path = tmp_path / "hosts.yaml"
    path.write_text(text, encoding="utf-8")

    (host,) = load_hosts_file(path).hosts
    urls = [u.url for u in host.services["shop"].urls]
    assert urls == ["http://localhost:18083/health", "http://localhost:18083/api/items"]
    assert host.services["shop"].rules  # the rest of the file as it is


def test_a_hosts_file_without_the_shops_url_is_refused():
    with pytest.raises(EvalError, match="host.docker.internal:8080"):
        hosts_file_for("hosts: []\n", 18081)


def test_each_engines_runners_get_its_own_engine_hosts_file_lock_tag_and_report_folder(tmp_path):
    engine = Engine(2, tmp_path)
    env = engine.env({"PATH": "/bin", "EVAL_BATCH_TAG": "old"})
    assert env["PATH"] == "/bin"
    assert env["EVAL_DOCKER_ENDPOINT"] == "tcp://127.0.0.1:23752"
    assert env["EVAL_HOSTS_FILE"] == str(tmp_path / "hosts-e2.yaml")
    assert env["EVAL_STACK_LOCK"] == str(tmp_path / "stack-e2.lock")
    assert env["EVAL_BATCH_TAG"] == "e2"
    assert env["EVAL_REPORTS_DIR"] == str(tmp_path / "e2")
    assert Engine(1, tmp_path).env({})["EVAL_STACK_LOCK"] != env["EVAL_STACK_LOCK"]


def test_an_engine_runs_its_scenarios_then_its_injection_cases():
    share = Share(scenarios=["db-down", "oom"], injection=["log-base64"])
    commands = runner_commands(share, args(repeat=3, judge="manual", model="m", label="R1"))
    assert [c[1:] for c in commands] == [
        ["-m", "evals.run", "--only", "db-down,oom", "--repeat", "3", "--judge", "manual",
         "--model", "m"],
        ["-m", "evals.injection", "--only", "log-base64", "--repeat", "3", "--judge", "manual",
         "--label", "R1"],
    ]  # fmt: skip
    assert all(c[0] == sys.executable for c in commands)
    assert len(runner_commands(Share(injection=["log-base64"]), args())) == 1


def test_a_runner_that_fails_does_not_stop_the_next_on_its_engine(tmp_path):
    ran = []

    async def spawn(argv, env, log_file):
        ran.append((argv[2], env["EVAL_BATCH_TAG"], log_file))
        return 1 if argv[2] == "evals.run" else 0

    share = Share(scenarios=["db-down"], injection=["log-base64"])
    outcome = asyncio.run(run_share(Engine(3, tmp_path), share, args(), spawn))

    assert [r[0] for r in ran] == ["evals.run", "evals.injection"]
    assert {r[1] for r in ran} == {"e3"} and {r[2] for r in ran} == {tmp_path / "e3.log"}
    assert outcome.codes == [1, 0]


def test_scripted_scenarios_need_no_engine():
    scenarios, _ = chosen(args(scenarios="all"))
    scripted = {s.id for s in load_scenarios() if s.scripted is not None}
    assert scripted and not scripted & set(scenarios)
    with pytest.raises(EvalError, match="scripted host"):
        chosen(args(scenarios=f"db-down,{sorted(scripted)[0]}"))
    with pytest.raises(EvalError, match="name the cases"):
        chosen(args())


def test_the_launcher_refuses_the_model_under_test_as_judge_before_starting_engines(capsys):
    assert main(["--scenarios", "db-down", "--judge", "openai/gpt-6-luna", "--model",
                 "openai/gpt-6-luna"]) == 2  # fmt: skip
    assert "judge" in capsys.readouterr().err


def test_batches_are_found_by_tag_from_the_rounds_start(tmp_path):
    for name in ("scenarios-20261116-100000-e1.jsonl", "scenarios-20261116-090000-e1.jsonl",
                 "scenarios-20261116-100500-e2.jsonl", "injection-20261116-100500-e1.jsonl",
                 "scenarios-20261116-100700.jsonl"):  # fmt: skip
        (tmp_path / name).write_text("", encoding="utf-8")
    since = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
    found = batches_since("scenarios", "e1", since, tmp_path)
    assert [p.name for p in found] == ["scenarios-20261116-100000-e1.jsonl"]


class FakeDocker:
    """`docker` as the engines see it: what was run, the eval engines that exist, and the
    command that fails, if any."""

    def __init__(self, stale: int = 0, fail: str | None = None, down_for: int = 0) -> None:
        self.calls: list[tuple[str | None, list[str]]] = []
        self.engines = {f"stale{i}" for i in range(stale)}
        self.fail = fail
        self.down_for = down_for

    async def __call__(self, argv, _stdin, env):
        argv = argv[1:]
        self.calls.append((env.get("DOCKER_HOST"), argv))
        if argv[0] == self.fail:
            return 1, "", f"{self.fail} broke"
        if argv[0] == "version" and self.down_for:
            self.down_for -= 1
            return 1, "", "Cannot connect"
        if argv[0] == "run":
            self.engines.add(argv[argv.index("--name") + 1])
        if argv[:2] == ["ps", "-aq"]:
            return 0, "\n".join(sorted(self.engines)), ""
        if argv[0] == "rm":
            self.engines -= set(argv[3:])
        return 0, "", ""

    def ran(self, command: str) -> list[list[str]]:
        return [argv for _host, argv in self.calls if argv[0] == command]


def engines(tmp_path, docker: FakeDocker, **kwargs) -> Engines:
    shops = []

    async def shop_up(engine):
        shops.append(engine.n)

    async def no_sleep(_s):
        return None

    built = Engines([Engine(1, tmp_path), Engine(2, tmp_path)], run=docker, reset=shop_up,
                    sleep=no_sleep, **kwargs)  # fmt: skip
    built.shops = shops  # type: ignore[attr-defined]
    return built


def test_engines_start_privileged_on_this_machine_only_with_the_shop_loaded_and_up(tmp_path):
    docker = FakeDocker()
    started = engines(tmp_path, docker)

    async def go():
        async with started.started():
            return set(docker.engines)

    assert asyncio.run(go()) == {"bleep-eval-engine-1", "bleep-eval-engine-2"}
    (run,) = docker.ran("run")[:1]
    assert "--privileged" in run and "127.0.0.1:23751:2375" in run and "127.0.0.1:18081:8080" in run
    assert "bleep.self=true" in run  # a live Bleep on this engine leaves them alone
    loads = [(host, argv) for host, argv in docker.calls if argv[0] == "load"]
    assert {host for host, _ in loads} == {"tcp://127.0.0.1:23751", "tcp://127.0.0.1:23752"}
    assert sorted(started.shops) == [1, 2]  # type: ignore[attr-defined]
    assert docker.engines == set()


def test_engines_left_by_an_earlier_launcher_are_removed_first(tmp_path):
    docker = FakeDocker(stale=2)

    async def go():
        async with engines(tmp_path, docker).started():
            assert not {"stale0", "stale1"} & docker.engines

    asyncio.run(go())
    assert docker.engines == set()


@pytest.mark.parametrize("fail", ["load", "save", "version"])
def test_the_engines_go_when_starting_them_fails(tmp_path, fail):
    docker = FakeDocker(fail=fail)
    started = engines(tmp_path, docker, clock=iter(range(0, 10_000, 100)).__next__)

    async def go():
        async with started.started():
            raise AssertionError("the block must not run")

    with pytest.raises(EvalError):
        asyncio.run(go())
    assert docker.engines == set()


def test_an_engine_is_waited_for_until_it_answers(tmp_path):
    docker = FakeDocker(down_for=3)

    async def go():
        async with engines(tmp_path, docker).started():
            pass

    asyncio.run(go())
    assert len(docker.ran("version")) == 5  # three refused, then one per engine


def test_the_engines_go_when_a_runner_fails_or_the_round_is_stopped(tmp_path):
    docker = FakeDocker()

    async def failing():
        async with engines(tmp_path, docker).started():
            raise RuntimeError("a runner broke")

    with pytest.raises(RuntimeError):
        asyncio.run(failing())
    assert docker.engines == set()

    async def stopped():
        task = asyncio.create_task(waiting())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def waiting():
        async with engines(tmp_path, docker).started():
            await asyncio.sleep(60)

    asyncio.run(stopped())
    assert docker.engines == set()


def test_the_rounds_scenario_batches_are_published_with_one_report_over_all(tmp_path):
    raw_dir, data, reports = tmp_path / "raw", tmp_path / "data", tmp_path / "reports"
    data.mkdir()
    (db_down, oom) = load_scenarios(only="db-down,oom")
    raws = []
    for n, scenario in enumerate((db_down, oom), start=1):
        raw = raw_dir / f"scenarios-20261116-10000{n}-e{n}.jsonl"
        append_jsonl(raw, ScenarioResult(id=scenario.id, model="m", judge="manual"))
        when = datetime(2026, 11, 16, 10, 0, n, tzinfo=UTC)
        write_info(raw, RunInfo(
            started_at=when, finished_at=when.replace(minute=10 * n), model="m",
            provider="openrouter", judge="manual", repeat=1, scenarios=[scenario],
            command=f"evals.run --only {scenario.id}", raw=raw.name,
        ))  # fmt: skip
        raws.append(raw)

    report = publish_scenarios(raws, "evals.parallel --engines 2", data, reports)

    assert report == reports / "scenarios-2026-11-16.md"
    text = report.read_text(encoding="utf-8")
    assert "| Scenarios | db-down, oom, 1 run each |" in text and "10:00 UTC, 20 min" in text
    assert "| Engines | 2 shop copies at once" in text
    assert "`evals.parallel --engines 2`" in text
    first = read_info(data / raws[0].name)
    assert first is not None
    assert first.batches == [f"../data/{raws[1].name}"]
    assert first.raw == f"../data/{raws[0].name}"
    assert (data / raws[1].name).read_text() == raws[1].read_text()


def test_the_rounds_injection_batches_are_published_with_one_set_of_tables(tmp_path):
    raws = []
    for n, case in enumerate(("log-base64", "control-db-down-restart"), start=1):
        raw = tmp_path / "raw" / f"injection-20261116-10000{n}-e{n}.jsonl"
        kind = "control" if case.startswith("control") else "attack"
        result = InjectionResult(
            run_id=f"r/{case}/1", case=case, kind=kind, channel="logs", label="R",
            run=ScenarioResult(id=case, model="m", judge="manual"), passed=True,
        )  # fmt: skip
        inj_append(raw, result)
        raws.append(raw)

    started = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
    tables = publish_injection(raws, "R", started, "m", tmp_path / "data")

    assert tables == tmp_path / "data" / "injection-20261116-100001-e1.md"
    text = tables.read_text(encoding="utf-8")
    assert "### R" in text and "log-base64" in text and "control-db-down-restart" in text
    assert "injection-20261116-100002-e2.jsonl" in text.splitlines()[0]
    assert {p.name for p in (tmp_path / "data").glob("*.jsonl")} == {r.name for r in raws}
