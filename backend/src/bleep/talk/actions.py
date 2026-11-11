"""Run what the person confirmed in a chat, the same for Telegram and the dashboard's panel (#391).

A surface offers an action with a button (`talk.confirms`); the press comes here. Every action
goes through the code the dashboard's own buttons and Telegram's use (`QuietStore.set` and
`clear`, `Expectations.expected`, `approvals.resolve` + `runner.resume`), so first-wins, a
replaced plan and a closed incident are answered as they are there. A look on request
(#403) starts through `incidents.looks.Looks`. The answers are the lines a
Telegram button gets (`prompts/notify/answer_*.md`), shared now by both surfaces, in the
language the person chose (#375).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any, Literal, Protocol

from bleep import prompts
from bleep.incidents.looks import Look
from bleep.incidents.quiet import QuietStore
from bleep.incidents.repository import IncidentRepo
from bleep.schemas.graph import ApprovalRecord, Decision
from bleep.schemas.incidents import TERMINAL_STATUSES
from bleep.schemas.talk import Offer
from bleep.talk.looks import look_said
from bleep.talk.times import when

Channel = Literal["telegram", "dashboard"]
_PAST = {"approve": "approved", "reject": "rejected", "instruct": "instructed"}


def said(name: str, language: str, /, **values: str) -> str:
    """A short plain-text answer to a press, `prompts/notify/<name>.md` in `language`."""
    return prompts.load_in(language, f"notify/{name}", **values).strip()


def _english() -> str:
    return "en"


class Approvals(Protocol):
    """The part of `incidents.approvals.ApprovalStore` a confirmed action uses."""

    async def resolve(
        self, incident_id: str, decision: Decision, approval_id: str | None = None
    ) -> Decision | None: ...

    async def current(self, incident_id: str) -> ApprovalRecord | None: ...


class Resumer(Protocol):
    """The part of `IncidentRunner` that carries a decided plan on."""

    async def resume(self, incident_id: str, decision: Decision, approval_id: str) -> Any: ...


class ExpectAnswer(Protocol):
    done: bool
    text: str


class Expecting(Protocol):
    """The part of `incidents.expectations.Expectations` a chat uses."""

    async def offered(self, incident_id: str) -> Any: ...

    async def expected(self, incident_id: str, via: Channel) -> ExpectAnswer: ...


class Looking(Protocol):
    """The part of `incidents.looks.Looks` a chat uses (#403)."""

    async def look(self, service_id: str, via: Channel) -> Look: ...


def _no_zone() -> tzinfo | None:
    return None


@dataclass
class Actions:
    """What a surface `via` runs an offer with. `incidents` and `quiet` are None only where a
    surface has no quiet feature (a bot built without it): a quiet offer is then unknown, and an
    incident's end is left to the action's own answer. `zone` gives Bleep's time zone at each
    answer, and `language` the language it is said in. `looked` hears the incident a look the
    person confirmed opened or found open, so the surface's conversation goes on about it
    (#422)."""

    approvals: Approvals
    runner: Resumer
    via: Channel
    clock: Callable[[], datetime]
    incidents: IncidentRepo | None = None
    quiet: QuietStore | None = None
    expectations: Expecting | None = None
    looks: Looking | None = None
    zone: Callable[[], tzinfo | None] = _no_zone
    language: Callable[[], str] = _english
    looked: Callable[[str], None] | None = None

    async def bind(self, offer: Offer, shown_plan: str = "") -> Offer | str:
        """The offer ready to show, or why it cannot be offered now. An instruction and a yes
        are bound to the plan `shown_plan` (the one a replied-to message showed) while it still
        waits, else to the one that waits now; "It's expected" only where it is offered."""
        if offer.kind in ("instruct", "show_plan"):
            incident_id = offer.incident_id or ""
            if shown_plan:
                if await self.plan_open(incident_id, shown_plan):
                    return offer.model_copy(update={"plan_id": shown_plan})
                return await self.why_lost(incident_id, shown_plan)
            waiting = await self.waiting(incident_id)
            if not waiting:
                return said("answer_not_waiting", self.language())
            return offer.model_copy(update={"plan_id": waiting})
        if offer.kind == "expected" and not await self._expectable(offer.incident_id or ""):
            return said("answer_cannot_expect", self.language(), incident=offer.incident or "")
        return offer

    async def plan_to_show(self, offer: Offer) -> ApprovalRecord | Literal["refused", "gone"]:
        """The plan a yes (`show_plan`) points to, or why there is none to approve: Bleep's own
        checks refused it, or it no longer waits."""
        current = await self.approvals.current(offer.incident_id or "")
        if current is not None and current.proposal.refused:
            return "refused"
        if current is None or current.id != offer.plan_id:
            return "gone"
        return current

    async def run(self, offer: Offer) -> str:
        """Run a confirmed offer; what the person is told."""
        now, language = self.clock(), self.language()
        if offer.incident_id and await self._ended(offer.incident_id):
            return said("answer_confirm_ended", language)
        if offer.kind == "expected" and self.expectations is not None:
            return (await self.expectations.expected(offer.incident_id or "", self.via)).text
        if offer.kind == "instruct" and offer.plan_id:
            decision = Decision(kind="instruct", text=offer.text, via=self.via, at=now)
            return await self.resolve(offer.incident_id or "", offer.plan_id, decision)
        if offer.kind == "look_again":
            return (await self.look(offer))[0]
        if self.quiet is None or offer.scope is None or offer.target_id is None:
            return said("answer_unknown", language)
        subject = offer.subject or offer.target_id
        if offer.kind == "watch_again":
            cleared = await self.quiet.clear(offer.scope, offer.target_id)
            answer = "answer_watching" if cleared else "answer_not_quiet"
            return said(answer, language, subject=subject)
        if offer.kind == "leave_alone":
            await self.quiet.set(offer.scope, offer.target_id, "leave_alone", None)
            return said("answer_left_alone", language, subject=subject)
        if offer.until is None or offer.until <= now:
            return said("answer_confirm_expired", language)
        await self.quiet.set(offer.scope, offer.target_id, "snooze", offer.until)
        return said("answer_on_it", language, until=when(offer.until, now, self.zone(), language))

    async def look(self, offer: Offer) -> tuple[str, str | None]:
        """Start the look a confirmed `look_again` asks for: what the person is told, and the
        incident it opened or found open. The dashboard's panel tells the result itself (#458),
        so its line says it comes in the chat, Telegram's that it comes in the thread."""
        language = self.language()
        if self.looks is None or offer.target_id is None:
            return said("answer_unknown", language), None
        look = await self.looks.look(offer.target_id, self.via)
        incident_id = look.incident.id if look.incident is not None else None
        if incident_id is not None and self.looked is not None:
            self.looked(incident_id)
        here = self.via == "dashboard"
        return look_said(look, self.clock(), self.zone(), language, here=here), incident_id

    async def resolve(self, incident_id: str, approval_id: str, decision: Decision) -> str:
        """Try to win the approval; the text to tell the person either way."""
        before = await self.approvals.current(incident_id)
        won = await self.approvals.resolve(incident_id, decision, approval_id)
        if won is not None:
            await self.runner.resume(incident_id, won, approval_id)
            refused = before is not None and before.id == approval_id and before.proposal.refused
            if refused and decision.kind == "reject":
                return said("answer_let_go", self.language())
            return said(f"answer_{_PAST[decision.kind]}", self.language())
        return await self.why_lost(incident_id, approval_id)

    async def why_lost(self, incident_id: str, approval_id: str) -> str:
        """Why the plan `approval_id` takes no decision now, as the person is told."""
        current, language = await self.approvals.current(incident_id), self.language()
        if current is not None and current.id == approval_id and current.decision is not None:
            channel = said(f"channel_{current.decision.via}", language)
            return said("answer_already_decided", language, channel=channel)
        if current is not None and current.id == approval_id and current.closed:
            return said("answer_closed", language)
        if current is not None and current.id != approval_id:
            return said("answer_stale", language)
        return said("answer_not_waiting", language)

    async def plan_open(self, incident_id: str, approval_id: str) -> bool:
        """Whether the plan `approval_id` still waits; no plan asks nothing that closes before
        its incident ends."""
        if not approval_id:
            return True
        current = await self.approvals.current(incident_id)
        return (
            current is not None
            and current.id == approval_id
            and current.decision is None
            and not current.closed
        )

    async def waiting(self, incident_id: str) -> str:
        """The id of the incident's plan that waits for a decision now; empty when none does."""
        current = await self.approvals.current(incident_id)
        if current is None or current.decision is not None or current.closed:
            return ""
        return current.id

    async def _expectable(self, incident_id: str) -> bool:
        if self.expectations is None:
            return False
        return await self.expectations.offered(incident_id) is not None

    async def _ended(self, incident_id: str) -> bool:
        if self.incidents is None:
            return False  # an instruction or "It's expected" answers a closed incident itself
        incident = await self.incidents.get(incident_id)
        return incident is None or incident.status in TERMINAL_STATUSES
