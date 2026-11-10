"""The dashboard's chat panel: its own conversation with Bleep (#391).

It reads a message with the same `Talk` as Telegram and runs a confirmed offer through the same
`Actions`, but keeps its own turns and its own offers: the two surfaces share the reading, the
prompts and the actions, not the transcript (docs/design/app.md). A message never changes
anything; an offer comes back with two buttons, and only a press of Yes (or Send) runs it, once,
within `talk.confirms.CONFIRM_TTL` and while its incident is open. A "yes" to a waiting plan
points to the incident's page, where the plan is shown in full with its own buttons.
"""

import asyncio
from collections.abc import Callable
from datetime import datetime

from bleep import prompts
from bleep.incidents.repository import utcnow
from bleep.schemas.talk import ChatAnswer, ChatConfirm, ConfirmResult, Offer, TalkReply
from bleep.talk.actions import Actions, said
from bleep.talk.confirms import NO_LABEL, Confirms, yes_label
from bleep.talk.conversation import Talk
from bleep.talk.history import Turns
from bleep.talk.links import links_in, offer_links

WAITING_MAX = 3  # messages being answered at once; one more is told Bleep is busy


class Panel:
    def __init__(self, talk: Talk, clock: Callable[[], datetime] = utcnow) -> None:
        self.talk = talk
        self.turns = Turns()
        self.confirms = Confirms(clock)
        self._turn = asyncio.Lock()  # one message at a time, in the order they came
        self._waiting = 0

    @property
    def waiting(self) -> int:
        """Messages being answered or waiting for their turn now."""
        return self._waiting

    async def say(self, text: str, incident_id: str | None, actions: Actions) -> ChatAnswer:
        """Answer `text`, written on the page of `incident_id` if any. Changes nothing."""
        if self._waiting >= WAITING_MAX:
            return ChatAnswer(answer=_fixed("busy"))
        self._waiting += 1
        try:
            async with self._turn:
                self.confirms.expired()  # forgets the offers nobody answered in time
                reply = await self.talk.reply(text, incident_id, self.turns.recent())
                answer = await self._answer(reply, actions)
                self.turns.add(text, answer.answer)
                return answer
        finally:
            self._waiting -= 1

    async def _answer(self, reply: TalkReply, actions: Actions) -> ChatAnswer:
        if reply.offer is None:
            return ChatAnswer(answer=reply.answer, links=reply.links)
        bound = await actions.bind(reply.offer)
        if isinstance(bound, str):
            return self._line(bound, reply.offer)
        if bound.kind == "show_plan":
            return self._line(await _plan_line(bound, actions), bound)
        confirm_id = self.confirms.add(bound)
        expires = self.confirms.expires(confirm_id)
        assert expires is not None
        confirm = ChatConfirm(
            id=confirm_id,
            yes_label=yes_label(bound),
            no_label=NO_LABEL,
            expires_at=expires,
            incident_id=bound.incident_id,
        )
        return ChatAnswer(answer=reply.answer, links=reply.links, confirm=confirm)

    def _line(self, line: str, offer: Offer) -> ChatAnswer:
        return ChatAnswer(answer=line, links=links_in(line, offer_links(offer)))

    async def press(self, confirm_id: str, yes: bool, actions: Actions) -> ConfirmResult:
        """Run the offer `confirm_id` on yes, once; no drops it. A second press, an expired and
        an unknown offer run nothing."""
        taken = self.confirms.take(confirm_id)
        if taken == "used":
            return ConfirmResult(line=said("answer_confirm_used"))
        if isinstance(taken, str):
            return ConfirmResult(line=said("answer_confirm_expired"))
        if not yes:
            return ConfirmResult(line=said("answer_confirm_no"))
        return ConfirmResult(line=await actions.run(taken))


async def _plan_line(offer: Offer, actions: Actions) -> str:
    """A yes to a waiting plan points to its page; a plan Bleep's own checks refused cannot be
    approved there either, and one that no longer waits is said so."""
    plan = await actions.plan_to_show(offer)
    if plan == "gone":
        return said("answer_not_waiting")
    name = "plan_refused" if plan == "refused" else "plan_on_page"
    return _fixed(name, incident=offer.incident or "")


def _fixed(name: str, **values: str) -> str:
    return prompts.load(f"talk/{name}", **values).strip()
