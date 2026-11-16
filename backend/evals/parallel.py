"""Run the demo-shop evals on several copies of the demo shop at once, one per Docker engine.

Each copy is a `docker:dind` container on this machine's engine with the shop's images loaded and
the shop up, under the same container names and Compose project as the real one, so the
scenarios' expected plans and the shop's docs still fit. The cases are split across the copies;
each copy runs `evals.run` and then `evals.injection` on its share, one run at a time, with its
own engine, hosts file, stack lock and batch tag. The engines are removed afterwards, also when a
runner fails or the launcher is stopped (#657).

    cd backend && uv run --group evals python -m evals.parallel --engines 4 \\
        --scenarios db-down,oom --injection log-base64,control-db-down-restart --judge manual
"""

import argparse
import asyncio
import contextlib
import logging
import os
import shlex
import shutil
import signal
import sys
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from bleep.incidents.repository import utcnow
from evals.injection import InjectionError, load_cases, render_round
from evals.injection import read_jsonl as read_injection_jsonl
from evals.judge import JudgeError, check_distinct
from evals.judge import model_under_test as tested_model
from evals.report import read_info, render, report_path, round_info, write_info
from evals.report import read_jsonl as read_scenario_jsonl
from evals.run import (
    DEMO_COMPOSE,
    RAW_DIR,
    REPO,
    EvalError,
    EvalSettings,
    Runner,
    ShopReset,
    _exec,
)
from evals.scenarios import ScenarioError, load_scenarios
from evals.stack_lock import StackBusy, eval_command, holding_demo_stack

log = logging.getLogger("evals.parallel")

BACKEND = REPO / "backend"
DATA_DIR = REPO / "docs" / "evals" / "data"
REPORTS_DIR = REPO / "docs" / "evals"
LAUNCHER_LOCK = Path.home() / ".cache" / "bleep" / "parallel-evals.lock"
DIND_IMAGE = "docker:29.9.3-dind"
# `bleep.self` keeps a live Bleep watching this machine's engine from opening incidents on them.
ENGINE_LABELS = {"bleep.self": "true", "bleep.eval-engine": "true"}
ENGINE_NAME = "bleep-eval-engine-{n}"
ENGINE_PORT = 23750  # engine n's Docker API on 127.0.0.1:ENGINE_PORT + n
SHOP_PORT = 18080  # and its shop-proxy's 8080 on 127.0.0.1:SHOP_PORT + n
SHOP_IMAGES = ("shop-shop-web", "shop-shop-proxy", "shop-shop-worker", "postgres:17-alpine")
SHOP_URL = "host.docker.internal:8080"  # how config/hosts.demo.yaml reaches the shop
HOSTS_FILE = REPO / "config" / "hosts.demo.yaml"
ENGINE_READY_S = 120
STOP_GRACE_S = 15
DEFAULT_ENGINES = 4


@dataclass(frozen=True)
class Engine:
    """One copy of the shop: engine n's ports, and the files its runners keep apart."""

    n: int
    folder: Path

    @property
    def name(self) -> str:
        return ENGINE_NAME.format(n=self.n)

    @property
    def endpoint(self) -> str:
        return f"tcp://127.0.0.1:{ENGINE_PORT + self.n}"

    @property
    def shop_port(self) -> int:
        return SHOP_PORT + self.n

    @property
    def tag(self) -> str:
        return f"e{self.n}"

    @property
    def hosts_file(self) -> Path:
        return self.folder / f"hosts-{self.tag}.yaml"

    @property
    def log_file(self) -> Path:
        return self.folder / f"{self.tag}.log"

    def env(self, base: Mapping[str, str]) -> dict[str, str]:
        """The runners' environment: this copy's engine, hosts file, lock and batch tag."""
        return dict(base) | {
            "EVAL_DOCKER_ENDPOINT": self.endpoint,
            "EVAL_HOSTS_FILE": str(self.hosts_file),
            "EVAL_STACK_LOCK": str(self.folder / f"stack-{self.tag}.lock"),
            "EVAL_BATCH_TAG": self.tag,
            "EVAL_REPORTS_DIR": str(self.folder / self.tag),
        }


@dataclass
class Share:
    """The cases one engine runs, in file order."""

    scenarios: list[str] = field(default_factory=list)
    injection: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.scenarios) + len(self.injection)


def split(scenarios: Sequence[str], cases: Sequence[str], engines: int) -> list[Share]:
    """The cases dealt out in turn, scenarios first, so every engine gets about as many runs; a
    case's repeats stay on one engine, so its runs are numbered as in a single batch. No empty
    shares: fewer cases than engines start fewer engines."""
    shares = [Share() for _ in range(engines)]
    units = [("run", s) for s in scenarios] + [("injection", c) for c in cases]
    for i, (suite, case) in enumerate(units):
        share = shares[i % engines]
        (share.scenarios if suite == "run" else share.injection).append(case)
    return [s for s in shares if len(s)]


def hosts_file_for(template: str, shop_port: int) -> str:
    """The demo hosts file with the shop's URLs on this copy's published port."""
    if SHOP_URL not in template:
        raise EvalError(f"the demo hosts file reaches the shop at no {SHOP_URL} URL")
    return template.replace(SHOP_URL, f"localhost:{shop_port}")


def runner_commands(share: Share, args: argparse.Namespace) -> list[list[str]]:
    """The runners an engine runs one after the other: evals.run on its scenarios, then
    evals.injection on its cases."""
    common = ["--repeat", str(args.repeat)]
    common += ["--judge", args.judge] if args.judge else []
    python = [sys.executable, "-m"]
    commands = []
    if share.scenarios:
        commands.append([*python, "evals.run", "--only", ",".join(share.scenarios), *common])
    if share.injection:
        label = ["--label", args.label] if args.label else []
        commands.append(
            [*python, "evals.injection", "--only", ",".join(share.injection), *common, *label]
        )
    return commands


# Runs a command with this environment, its output appended to a log file; its exit code.
Spawn = Callable[[list[str], dict[str, str], Path], Awaitable[int]]


async def _spawn(argv: list[str], env: dict[str, str], log_file: Path) -> int:
    with log_file.open("a", encoding="utf-8") as out:
        out.write(f"$ {shlex.join(argv)}\n")
        out.flush()
        process = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=out, stderr=out, cwd=BACKEND, env=env
        )
        try:
            return await process.wait()
        finally:
            if process.returncode is None:  # cancelled: stop the runner before its engine goes
                process.send_signal(signal.SIGINT)
                try:
                    await asyncio.wait_for(process.wait(), STOP_GRACE_S)
                except TimeoutError:
                    process.kill()
                    await process.wait()


class Engines:
    """Starts the engines with the shop up in each, and removes them when the block ends,
    whatever ends it. Engines a launcher killed earlier left behind are removed first."""

    def __init__(
        self,
        engines: Sequence[Engine],
        *,
        run: Runner = _exec,
        reset: Callable[[Engine], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.engines = list(engines)
        self._run = run
        self._reset = reset or _shop_up
        self._clock = clock
        self._sleep = sleep

    @contextlib.asynccontextmanager
    async def started(self) -> AsyncIterator[None]:
        try:
            await self.remove()
            with tempfile.TemporaryDirectory(prefix="bleep-shop-images-") as folder:
                images = Path(folder) / "shop.tar"
                await self._docker("save", "-o", str(images), *SHOP_IMAGES)
                await asyncio.gather(*(self._start(e, images) for e in self.engines))
            yield
        finally:
            await asyncio.shield(self.remove())

    async def remove(self) -> None:
        listed = ["ps", "-aq", "--filter", "label=bleep.eval-engine=true"]
        ids = (await self._docker(*listed)).split()
        if ids:
            log.info("removing %d eval engines", len(ids))
            await self._docker("rm", "-f", "-v", *ids)

    async def _start(self, engine: Engine, images: Path) -> None:
        labels = [arg for k, v in ENGINE_LABELS.items() for arg in ("--label", f"{k}={v}")]
        await self._docker(
            "run", "-d", "--privileged", "--name", engine.name, *labels,
            "-e", "DOCKER_TLS_CERTDIR=",
            "-p", f"127.0.0.1:{ENGINE_PORT + engine.n}:2375",
            "-p", f"127.0.0.1:{engine.shop_port}:8080",
            DIND_IMAGE,
        )  # fmt: skip
        await self._until_up(engine)
        await self._docker("load", "-i", str(images), host=engine.endpoint)
        log.info("%s: images loaded, starting the shop", engine.name)
        await self._reset(engine)
        log.info("%s: shop up on port %d", engine.name, engine.shop_port)

    async def _until_up(self, engine: Engine) -> None:
        clock = self._clock or asyncio.get_running_loop().time
        deadline, why = clock() + ENGINE_READY_S, ""
        while clock() <= deadline:
            code, _out, err = await self._run(["docker", "version"], "", _env(engine.endpoint))
            if code == 0:
                return
            why = err.strip()[-200:]
            await self._sleep(1)
        raise EvalError(f"{engine.name} did not answer within {ENGINE_READY_S} s: {why}")

    async def _docker(self, *argv: str, host: str | None = None) -> str:
        code, out, err = await self._run(["docker", *argv], "", _env(host))
        if code != 0:
            raise EvalError(f"docker {argv[0]} failed: {err.strip()[-500:]}")
        return out


def _env(docker_host: str | None) -> dict[str, str]:
    env = dict(os.environ)
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    return env


async def _shop_up(engine: Engine) -> None:
    """The shop up and healthy on a fresh engine: the same recreate and wait as before a run."""
    items = f"http://localhost:{engine.shop_port}/api/items"
    await ShopReset(DEMO_COMPOSE, items, docker_host=engine.endpoint)()


@dataclass
class Outcome:
    engine: Engine
    share: Share
    codes: list[int]
    seconds: float


def model_env(model: str | None, settings: EvalSettings) -> dict[str, str]:
    """`--model` as the environment both runners read it from (`evals.injection` has no
    `--model`): the model setting of the provider in use."""
    if not model:
        return {}
    return {"LOCAL_MODEL" if settings.models.provider == "local" else "LLM_MODEL": model}


async def run_share(
    engine: Engine,
    share: Share,
    args: argparse.Namespace,
    base: Mapping[str, str],
    spawn: Spawn = _spawn,
) -> Outcome:
    """An engine's runners, one after the other, in `base`'s environment and its own; a runner
    that fails does not stop the next."""
    started = asyncio.get_running_loop().time()
    codes = []
    for argv in runner_commands(share, args):
        codes.append(await spawn(argv, engine.env(base), engine.log_file))
    seconds = asyncio.get_running_loop().time() - started
    return Outcome(engine, share, codes, seconds)


def batches_since(kind: str, tag: str, since: datetime, folder: Path) -> list[Path]:
    """The raw files of `kind` with this batch tag started at `since` or later, by name, so a
    runner that failed halfway still leaves its runs."""
    stamp = f"{since:%Y%m%d-%H%M%S}"
    found = folder.glob(f"{kind}-*-{tag}.jsonl")
    return sorted(p for p in found if p.stem.removeprefix(f"{kind}-")[:15] >= stamp)


def publish_scenarios(
    raws: Sequence[Path], command: str, data_dir: Path, reports_dir: Path
) -> Path:
    """The round's scenario batches copied to `data_dir`, each naming the others and one report,
    and that report written over all of them; its path."""
    data_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    infos = []
    for raw in raws:
        info = read_info(raw)
        if info is None:
            raise EvalError(f"{raw} has no batch setup next to it")
        infos.append(info)
    report = report_path(min(i.started_at for i in infos).date(), reports_dir)
    copies = [data_dir / raw.name for raw in raws]
    published = []
    for raw, copy, info in zip(raws, copies, infos, strict=True):
        shutil.copyfile(raw, copy)
        info = info.model_copy(
            update={
                "raw": os.path.relpath(copy, report.parent),
                "batches": [os.path.relpath(c, report.parent) for c in copies if c != copy],
                "report": os.path.relpath(report, REPO),
                "command": command,
            }
        )
        write_info(copy, info)
        published.append(info)
    results = [r for copy in copies for r in read_scenario_jsonl(copy)]
    report.write_text(render(round_info(published[0], published[1:]), results), encoding="utf-8")
    return report


def publish_injection(
    raws: Sequence[Path], label: str, started: datetime, model: str, data_dir: Path
) -> Path:
    """The round's injection batches copied to `data_dir`, with one set of tables over all of
    them beside the first; its path."""
    data_dir.mkdir(parents=True, exist_ok=True)
    copies = [data_dir / raw.name for raw in raws]
    for raw, copy in zip(raws, copies, strict=True):
        shutil.copyfile(raw, copy)
    results = [r for copy in copies for r in read_injection_jsonl(copy)]
    cases = load_cases(only=",".join(sorted({r.case for r in results})))
    tables = copies[0].with_suffix(".md")
    batches = ", ".join(c.name for c in copies)
    tables.write_text(
        f"<!-- {model}, {started:%Y-%m-%d %H:%M} to {utcnow():%H:%M} UTC, batches {batches} -->"
        f"\n\n{render_round(label, cases, results)}",
        encoding="utf-8",
    )
    return tables


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.parallel", description=__doc__.split("\n")[0]
    )
    parser.add_argument(
        "--engines", type=int, default=DEFAULT_ENGINES,
        help=f"shop copies to run at once (default {DEFAULT_ENGINES})",
    )  # fmt: skip
    parser.add_argument(
        "--scenarios", help="evals.run scenarios on the demo shop: ids, comma-separated, or all"
    )
    parser.add_argument("--injection", help="evals.injection cases: ids, comma-separated, or all")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case (default 1)")
    parser.add_argument(
        "--model", help="the model under test for both runners, as evals.run's --model"
    )
    parser.add_argument("--judge", help="the judge, as the runners' --judge")
    parser.add_argument("--label", default="", help="the injection round's heading")
    return parser


def chosen(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    """The demo-shop scenarios and the live injection cases the arguments name, in file order.
    A scenario on a scripted host needs no engine, so `all` leaves those out and naming one is
    refused."""
    scenarios: list[str] = []
    if args.scenarios:
        only = None if args.scenarios == "all" else args.scenarios
        loaded = load_scenarios(only=only)
        scripted = [s.id for s in loaded if s.scripted is not None]
        if scripted and only is not None:
            raise EvalError(
                f"{', '.join(scripted)} run on a scripted host, not the shop; run them with "
                "evals.run"
            )
        scenarios = [s.id for s in loaded if s.scripted is None]
    cases: list[str] = []
    if args.injection:
        only = None if args.injection == "all" else args.injection
        cases = [c.id for c in load_cases(only=only) if c.live]
    if not scenarios and not cases:
        raise EvalError("name the cases to run: --scenarios and/or --injection")
    return scenarios, cases


async def run_round(
    args: argparse.Namespace,
    scenarios: Sequence[str],
    cases: Sequence[str],
    command: str,
    settings: EvalSettings,
) -> int:
    started = utcnow()
    folder = RAW_DIR / f"parallel-{started:%Y%m%d-%H%M%S}"
    folder.mkdir(parents=True)
    shares = split(scenarios, cases, args.engines)
    engines = [Engine(n, folder) for n in range(1, len(shares) + 1)]
    template = HOSTS_FILE.read_text(encoding="utf-8")
    for engine in engines:
        engine.hosts_file.write_text(hosts_file_for(template, engine.shop_port), encoding="utf-8")
    log.info("building the shop's images on this machine's engine")
    await _check(["docker", "compose", "-p", "shop", "-f", str(DEMO_COMPOSE), "build"])
    base = dict(os.environ) | model_env(args.model, settings)
    async with Engines(engines).started():
        log.info("%d engines up; logs in %s", len(engines), folder.relative_to(REPO))
        ran = asyncio.get_running_loop().time()
        outcomes = await asyncio.gather(
            *(run_share(e, s, args, base) for e, s in zip(engines, shares, strict=True))
        )
        wall = asyncio.get_running_loop().time() - ran
    model = tested_model(settings.models)
    lines = [f"{len(engines)} engines, {wall / 60:.1f} min from the first run to the last"]
    for o in outcomes:
        failed = [c for c in o.codes if c != 0]
        lines.append(
            f"  {o.engine.tag}: {len(o.share)} cases, {o.seconds / 60:.1f} min"
            + (
                f", a runner exited {failed} (see {o.engine.log_file.relative_to(REPO)})"
                if failed
                else ""
            )
        )
    tags = [e.tag for e in engines]
    scenario_raws = [p for t in tags for p in batches_since("scenarios", t, started, RAW_DIR)]
    with_info = [p for p in scenario_raws if read_info(p) is not None]
    if with_info:
        report = publish_scenarios(with_info, command, DATA_DIR, REPORTS_DIR)
        lines.append(f"scenarios: {report.relative_to(REPO)}, {len(with_info)} batches")
    for raw in sorted(set(scenario_raws) - set(with_info)):
        lines.append(f"scenarios: {raw.relative_to(REPO)} ended without its setup; not published")
    injection_raws = [p for t in tags for p in batches_since("injection", t, started, RAW_DIR)]
    if injection_raws:
        tables = publish_injection(injection_raws, args.label, started, model, DATA_DIR)
        lines.append(f"injection: {tables.relative_to(REPO)}, {len(injection_raws)} batches")
    print("\n".join(lines))
    return 0 if all(c == 0 for o in outcomes for c in o.codes) else 1


async def _check(argv: list[str]) -> None:
    code, _out, err = await _exec(argv, "", dict(os.environ))
    if code != 0:
        raise EvalError(f"{shlex.join(argv)} failed: {err.strip()[-500:]}")


async def _cancel_on_term(round_: Awaitable[int]) -> int:
    """`round_`, cancelled on SIGTERM as on Ctrl-C, so the engines go either way."""
    task = asyncio.ensure_future(round_)
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    return await task


def main(argv: Sequence[str] | None = None, settings: EvalSettings | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    settings = settings or EvalSettings()
    if args.model:
        settings = settings.with_model(args.model)
    try:
        if args.engines < 1 or args.repeat < 1:
            raise EvalError("--engines and --repeat must be 1 or more")
        check_distinct(args.judge or settings.judge_model, tested_model(settings.models))
        scenarios, cases = chosen(args)
        command = "uv run --group evals " + eval_command(
            "evals.parallel", sys.argv[1:] if argv is None else argv
        )
        with holding_demo_stack(LAUNCHER_LOCK, command):
            return asyncio.run(
                _cancel_on_term(run_round(args, scenarios, cases, command, settings))
            )
    except (EvalError, JudgeError, ScenarioError, InjectionError, StackBusy) as error:
        print(f"evals: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt, asyncio.CancelledError:
        print("evals: stopped; the engines are removed", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
