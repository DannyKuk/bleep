"""The chat messages the demo video's phone shows (docs/demo.md "The README video").

The throwaway Bleep runs without a Telegram bot, so this sends the filmed incident's messages
through Bleep's own Notifier to a client that records them instead: the opener, the plan with its
buttons and the end message, in Bleep's own words from the run's own data. The incident, its
signals and its approval are read from a copy of the throwaway's database (its volume mounted
read-only into a one-off container of the backend image); the diagnosis and the report
from the incident's API data, which demo/video/capture/run.mjs saved.

    demo/video/stage.sh phone      # writes demo/video/.local/run/phone.json
"""

import asyncio
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.notify.notifier import Notifier
from bleep.notify.telegram import MessageLog
from bleep.schemas.graph import Diagnosis, Report
from bleep.store.db import Database

COPY = Path("/tmp/phone.db")


class RecordingTelegram:
    """Stands in for the Telegram client: keeps each message and its buttons."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(
        self, chat_id, text, buttons=None, reply_to=None, markdown=True, silent=False, more=None
    ) -> int:
        rows = [row for row in [buttons or [], *(more or [])] if row]
        self.sent.append({"text": text, "buttons": [[label for label, _ in row] for row in rows]})
        return len(self.sent)

    async def clear_buttons(self, chat_id, message_id) -> None:
        """Bleep takes a plan's buttons away once it is decided; the phone shows that too."""
        self.sent[message_id - 1]["cleared"] = True


def to_html(markdown_v2: str) -> str:
    """Telegram MarkdownV2 as the little HTML the phone draws: bold, italic, code, links."""
    out, i, open_tags = [], 0, []
    marks = {"*": "b", "_": "i", "`": "code"}
    while i < len(markdown_v2):
        ch = markdown_v2[i]
        if ch == "\\" and i + 1 < len(markdown_v2):
            out.append(escape_html(markdown_v2[i + 1]))
            i += 2
            continue
        if ch in marks:
            tag = marks[ch]
            if open_tags and open_tags[-1] == tag:
                out.append(f"</{tag}>")
                open_tags.pop()
            else:
                out.append(f"<{tag}>")
                open_tags.append(tag)
            i += 1
            continue
        out.append(escape_html(ch))
        i += 1
    return re.sub(r"\n", "<br>", "".join(out))


def escape_html(ch: str) -> str:
    return {"<": "&lt;", ">": "&gt;", "&": "&amp;"}.get(ch, ch)


def copy_without_chat(incident_id: str) -> None:
    """A copy of the throwaway's database (mounted read-only) with no chat log for the incident,
    so its first message is its opener however often this runs."""
    source = sqlite3.connect("file:/data/bleep.db?mode=ro&immutable=1", uri=True)
    copy = sqlite3.connect(COPY)
    source.backup(copy)
    copy.execute("DELETE FROM telegram_messages WHERE incident_id = ?", (incident_id,))
    copy.commit()


async def main(data: dict) -> list[dict]:
    incident_id = data["incident"]["id"]
    copy_without_chat(incident_id)
    db = Database(COPY)
    incidents = IncidentRepo(db)
    incident = await incidents.get(incident_id)
    approvals = ApprovalStore(db, "unused: reading an approval needs no grant")
    approval = await approvals.current(incident_id)
    assert incident is not None and approval is not None
    report = Report.model_validate(data["report"])
    finished = incident.closed_at or datetime.fromisoformat(data["incident"]["closed_at"])
    telegram = RecordingTelegram()
    notifier = Notifier(
        EventBus(db),
        telegram,  # type: ignore[arg-type]
        "video",
        MessageLog(db),
        incidents=incidents,
        clock=lambda: finished,
        on_it=object(),  # type: ignore[arg-type]
    )
    await notifier.incident_opened(incident)
    diagnosis = Diagnosis.model_validate(data["diagnosis"]) if data.get("diagnosis") else None
    await notifier.proposal(incident, diagnosis, approval.proposal, approval.request)
    await notifier.report(incident, report)
    return [{**m, "html": to_html(m["text"])} for m in telegram.sent]


print(json.dumps(asyncio.run(main(json.load(sys.stdin))), indent=1))
