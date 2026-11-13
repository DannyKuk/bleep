"""Linear-time checks that hold on a busy machine.

The hostile input is timed against a benign input of the same size, by turns, so load slows both
sides alike. A quadratic path is hundreds of times slower than the benign run on these sizes; the
worst linear one measured 3.7 times (2026-11-13), which leaves room for noise under 10.
"""

import time
from collections.abc import Awaitable, Callable

FACTOR = 10
RUNS = 5
GIVE_UP_S = 1.0
# Lines of client commands, so the redaction reads them on the same path as the hostile ones
FILLER = "curl -s http://shop/ok "


def benign_like(text: str, filler: str = FILLER) -> str:
    """Harmless words in the shape of `text`: the same lines, each exactly as long."""
    lines = text.split("\n")
    return "\n".join((filler * (len(line) // len(filler) + 1))[: len(line)] for line in lines)


class _Best:
    def __init__(self, factor: float) -> None:
        self.factor, self.benign, self.hostile = factor, float("inf"), float("inf")

    def holds(self, benign: float, hostile: float) -> bool:
        """Whether the best runs so far hold the bar. Fails at once on a hostile run over a
        second and ten times the bar, which no load explains, so a quadratic path that
        takes minutes is not run again."""
        self.benign, self.hostile = min(self.benign, benign), min(self.hostile, hostile)
        if hostile > max(GIVE_UP_S, 10 * self.factor * self.benign):
            self.fail()
        return self.hostile < self.factor * self.benign

    def fail(self) -> None:
        raise AssertionError(
            f"the hostile input took {self.hostile:.4f} s, more than {self.factor} times "
            f"the {self.benign:.4f} s of a benign one of the same size"
        )


def _timed(check: Callable[[str], object], text: str) -> float:
    started = time.perf_counter()
    check(text)
    return time.perf_counter() - started


def assert_linear(
    check: Callable[[str], object],
    hostile: str,
    benign: str | None = None,
    factor: float = FACTOR,
) -> None:
    """`check(hostile)` takes under `factor` times `check(benign)`, best run of each, and the
    benign input is `benign_like(hostile)` unless given. Stops at the first run that holds."""
    benign = benign_like(hostile) if benign is None else benign
    best = _Best(factor)
    for _ in range(RUNS):
        if best.holds(_timed(check, benign), _timed(check, hostile)):
            return
    best.fail()


async def assert_linear_async(
    check: Callable[[str], Awaitable[object]],
    hostile: str,
    benign: str,
    factor: float = FACTOR,
) -> None:
    """`assert_linear` for a check that is awaited."""

    async def timed(text: str) -> float:
        started = time.perf_counter()
        await check(text)
        return time.perf_counter() - started

    best = _Best(factor)
    for _ in range(RUNS):
        if best.holds(await timed(benign), await timed(hostile)):
            return
    best.fail()
