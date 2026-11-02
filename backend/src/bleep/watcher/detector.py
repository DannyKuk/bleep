"""Decides what raw signals mean: open an incident, log a self-recovery, or ignore.

Pure logic. The caller feeds it `Signal`s as they arrive and the current container states on a
timer; it never touches Docker, the database or the network (the only I/O is the model call in
`explain_recovery`). Time comes from an injected clock and from the `now` passed to `tick`.
"""

import re
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from bleep import prompts
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import Signal, Subject
from bleep.schemas.settings import Thresholds

LOG_TAIL_CHARS = 4_000
RECOVERY_LINE_CHARS = 200
NO_LOGS_LINE = "No logs were available to explain the restart."
SENTENCE_END = re.compile(r"(?<=[.!?])\s")
UNTRUSTED_TAG = re.compile(r"<(/?)untrusted", re.IGNORECASE)

# Signals that already mean "past a threshold" because the source applied one (Docker only reports
# unhealthy after the healthcheck retries ran out) or the detector produced them.
PASS_THROUGH = ("unhealthy", "restart_loop", "down_past_grace")


@dataclass(frozen=True)
class OpenIncident:
    subject: Subject
    signals: list[Signal]


@dataclass(frozen=True)
class AttachSignals:
    """More signals for a subject whose incident was just opened (inside the debounce window)."""

    subject: Subject
    signals: list[Signal]


@dataclass(frozen=True)
class Recovered:
    container: str
    signals: list[Signal]


@dataclass(frozen=True)
class Ignored:
    reason: str


Outcome = OpenIncident | AttachSignals | Recovered | Ignored


class Detector:
    def __init__(self, thresholds: Thresholds, clock: Callable[[], datetime]) -> None:
        self.t = thresholds
        self._clock = clock
        self._pending: dict[str, Signal] = {}  # container -> first death not yet resolved
        self._deaths: dict[str, deque[Signal]] = defaultdict(deque)
        self._streaks: dict[tuple[str, ...], list[Signal]] = {}
        self._down_since: dict[str, datetime] = {}  # host key -> first failed probe
        self._window_start: dict[str, datetime] = {}  # subject key -> when its incident opened

    def feed(self, signal: Signal) -> list[Outcome]:
        """Take one signal. Contract with the watcher (details in docs/design/watcher.md):

        - `signal.container` must equal `ContainerSummary.name`.
        - Detail keys: container_died {exit_code, restart_policy}; url_failed {ok, latency_ms,
          url}; disk {percent}; memory {available_percent}; load {load_per_cpu};
          host_unreachable {ok}.
        - URL and host probes are fed on every probe, good or bad, so streaks can reset.
        - The same subject may yield `OpenIncident` again once the debounce window has passed
          (a persistent breach, e.g. disk at 95 %). The manager dedupes against the subject's
          open incident (`IncidentRepo.open_or_attach`), so that is expected and safe. It must
          also handle `AttachSignals`.
        - The kind `url_failed` means "a URL check result"; `detail.ok` says whether it passed.
        - A second death while one is pending keeps the first death's grace window.
        """
        now = self._clock()
        match signal.kind:
            case "container_died" | "oom":
                return self._death(signal, now)
            case "url_failed":
                return self._url(signal, now)
            case "disk":
                if _num(signal, "percent") >= self.t.disk_percent:
                    return self._emit(signal.subject, [signal], now)
                return []
            case "memory":
                breach = (
                    _num(signal, "available_percent", 100) < self.t.memory_available_percent_min
                )
                return self._streak(signal, breach, self.t.memory_polls, now)
            case "load":
                breach = _num(signal, "load_per_cpu") > self.t.load_per_cpu
                return self._streak(signal, breach, self.t.load_polls, now)
            case "host_unreachable":
                return self._unreachable(signal, now)
            case _:
                signals = [signal]
                if (death := self._pending.pop(signal.container or "", None)) is not None:
                    signals.insert(0, death)  # this incident already covers that death
                return self._emit(signal.subject, signals, now)

    def tick(self, now: datetime, states: dict[str, list[ContainerSummary]]) -> list[Outcome]:
        """Resolve pending deaths against current container states (keyed by service id).

        A container running with health healthy or none whose `started_at` is within the grace
        window of the death is recovered whenever it is first seen, even on a late tick. One still
        `starting` cannot be judged that way, so the manager must tick about every 10 s.
        """
        by_name: dict[str, ContainerSummary] = {}
        for containers in states.values():
            for c in containers:
                by_name[c.name] = by_name[c.id] = c
        grace = timedelta(seconds=self.t.container_grace_s)
        out: list[Outcome] = []
        window = timedelta(seconds=self.t.repeated_window_s)
        for container in [c for c, d in self._deaths.items() if not d or now - d[-1].at > window]:
            del self._deaths[container]
        debounce = timedelta(seconds=self.t.debounce_s)
        for key in [k for k, at in self._window_start.items() if now - at >= debounce]:
            del self._window_start[key]
        for container, death in list(self._pending.items()):
            elapsed = now - death.at
            current = by_name.get(container)
            back = (
                current is not None
                and current.state == "running"
                and current.health
                in (
                    "healthy",
                    "none",
                )
            )
            started = current.started_at if current else None
            in_time = started <= death.at + grace if started else elapsed <= grace
            if back and in_time:
                del self._pending[container]
                out.append(Recovered(container, [death]))
            elif elapsed > grace:
                del self._pending[container]
                late = Signal(
                    subject=death.subject,
                    kind="down_past_grace",
                    at=now,
                    detail={
                        "grace_s": self.t.container_grace_s,
                        "state": getattr(current, "state", "missing"),
                    },
                    container=container,
                )
                out.extend(self._emit(death.subject, [death, late], now))
        return out

    # -- containers -------------------------------------------------------------------------

    def _death(self, signal: Signal, now: datetime) -> list[Outcome]:
        container = signal.container or ""
        if (
            signal.kind == "container_died"
            and signal.detail.get("exit_code") == 0
            and signal.detail.get("restart_policy", "") in ("", "no")
        ):
            return [Ignored("one-shot exit 0")]
        deaths = self._deaths[container]
        deaths.append(signal)
        cutoff = signal.at - timedelta(seconds=self.t.repeated_window_s)
        while deaths and deaths[0].at < cutoff:
            deaths.popleft()
        if len(deaths) >= self.t.repeated_deaths:
            loop = Signal(
                subject=signal.subject,
                kind="restart_loop",
                at=signal.at,
                detail={"deaths": len(deaths), "window_s": self.t.repeated_window_s},
                container=signal.container,
            )
            signals = [*deaths, loop]
            deaths.clear()
            self._pending.pop(container, None)
            return self._emit(signal.subject, signals, now)
        self._pending.setdefault(container, signal)
        return []

    # -- checks and host probes ---------------------------------------------------------------

    def _url(self, signal: Signal, now: datetime) -> list[Outcome]:
        failed = (
            not signal.detail.get("ok", False) or _num(signal, "latency_ms") > self.t.url_slow_ms
        )
        return self._streak(
            signal, failed, self.t.url_failures_to_open, now, signal.detail.get("url")
        )

    def _streak(
        self, signal: Signal, breach: bool, needed: int, now: datetime, extra: object = None
    ) -> list[Outcome]:
        key = (signal.subject.key, signal.kind, str(extra))
        if not breach:
            self._streaks.pop(key, None)
            return []
        run = self._streaks.setdefault(key, [])
        run.append(signal)
        if len(run) < needed:
            return []
        del self._streaks[key]
        return self._emit(signal.subject, run, now)

    def _unreachable(self, signal: Signal, now: datetime) -> list[Outcome]:
        key = signal.subject.key
        if signal.detail.get("ok", False):
            self._down_since.pop(key, None)
            self._streaks.pop((key, "host_unreachable", "None"), None)
            return []
        since = self._down_since.setdefault(key, now)
        run = self._streaks.setdefault((key, "host_unreachable", "None"), [])
        run.append(signal)
        if now - since < timedelta(seconds=self.t.host_unreachable_s):
            return []
        del self._down_since[key]
        del self._streaks[(key, "host_unreachable", "None")]
        return self._emit(signal.subject, run, now)

    # -- debounce -----------------------------------------------------------------------------

    def _emit(self, subject: Subject, signals: list[Signal], now: datetime) -> list[Outcome]:
        """The first qualifying signals open at once; later ones inside the window attach.

        Opening immediately keeps the latency of a real outage low and lets the manager treat
        `OpenIncident` as "create now". `AttachSignals` goes to the incident just opened.
        """
        started = self._window_start.get(subject.key)
        if started is not None and now - started < timedelta(seconds=self.t.debounce_s):
            return [AttachSignals(subject, signals)]
        self._window_start[subject.key] = now
        return [OpenIncident(subject, signals)]


def _num(signal: Signal, key: str, default: float = 0) -> float:
    value = signal.detail.get(key, default)
    return value if isinstance(value, int | float) else default


def _wrap_untrusted(source: str, text: str) -> str:  # replaced by guard.untrusted in Task 6
    safe = UNTRUSTED_TAG.sub(lambda m: f"&lt;{m.group(1)}untrusted", text)
    return f'<untrusted source="{source}">\n{safe}\n</untrusted>'


async def explain_recovery(model: BaseChatModel, logs_tail: str) -> str:
    """One plain sentence on why a self-recovered container stopped, from its last logs."""
    logs = logs_tail.strip()[-LOG_TAIL_CHARS:]
    if not logs:
        return NO_LOGS_LINE
    prompt = prompts.load("watcher/recovery_line", logs=_wrap_untrusted("logs", logs))
    reply = await model.ainvoke([HumanMessage(prompt)])
    sentence = SENTENCE_END.split(" ".join(reply.text.split()), maxsplit=1)[0]
    if len(sentence) > RECOVERY_LINE_CHARS:
        sentence = sentence[: RECOVERY_LINE_CHARS - 1].rstrip() + "…"
    return sentence
