"""A look on request in a chat (#403): the offer for "look at shop-web again", checked against
Bleep's state, and what a press of its Yes is answered with.

The offer is checked before anything is shown: the subject is a service Bleep knows (a host is
answered that a look is for a service), it has no open incident, it is not quiet, its host
answers and the day's cost cap is not spent. No model call is made for any of it; the run the
press starts is the cost, and the confirm line says so. The press runs `incidents.looks.Looks`,
which checks all of it again, since the state may have moved in the 10 minutes an offer lives.
"""

from datetime import UTC, datetime, tzinfo

from bleep.guard.titles import title_name
from bleep.incidents.looks import Look
from bleep.schemas.quiet import QuietMode
from bleep.schemas.talk import Offer, QuietLine, Snapshot, TalkAction
from bleep.talk.offers import LOCAL_TIME, find_subject, line
from bleep.talk.times import when


def look_offer(
    action: TalkAction,
    snapshot: Snapshot,
    now: datetime,
    zone: tzinfo | None,
    spent: bool,
    language: str = "en",
) -> Offer | str:
    """The offer of a look at the service `action` names, or the fixed line in `language` that
    says why there is none. `spent`: the day's cost cap for incidents is spent."""
    found = find_subject(snapshot, action.subject, language)
    if found is None:
        return line("no_subject", language)
    if isinstance(found, str):
        return found
    if found.scope == "host":
        return line("look_host", language, host=found.name)
    service = next(s for s in snapshot.services if s.id == found.target_id)
    if service.open_incident:
        return line("look_open", language, incident=service.open_incident, subject=found.name)
    if found.quiet is not None:
        return _quiet(found.name, found.host, found.quiet, now, zone, language)
    host = next((h for h in snapshot.hosts if h.id == found.host), None)
    if host is not None and not host.reachable:
        return line("look_unreachable", language, subject=found.name, host=found.host)
    if spent:
        return line("look_spent", language)
    return Offer(kind="look_again", scope="service", target_id=found.target_id, subject=found.name)


def look_said(
    look: Look, now: datetime, zone: tzinfo | None, language: str = "en", here: bool = False
) -> str:
    """What a press of a look's Yes is answered with, in `language`. `here`: the surface tells
    the result in the chat itself (the dashboard's panel, #458), not in the incident's thread."""
    subject = title_name(look.name)
    incident = (look.incident.short_name or look.incident.id) if look.incident is not None else ""
    match look.outcome:
        case "opened":
            started = "look_started_here" if here else "look_started"
            return line(started, language, subject=subject, incident=incident)
        case "open":
            return line("look_open", language, incident=incident, subject=subject)
        case "quiet" if look.quiet is not None:
            if look.quiet.scope == "host":
                return line("look_quiet_host", language, subject=subject, host=look.host_id)
            return _quiet_line(subject, look.quiet.mode, look.quiet.until, now, zone, language)
        case "unreachable":
            return line("look_unreachable", language, subject=subject, host=look.host_id)
        case "spent":
            return line("look_spent", language)
    return line("look_unknown", language)


def _quiet(
    subject: str,
    host: str,
    quiet: QuietLine,
    now: datetime,
    zone: tzinfo | None,
    language: str,
) -> str:
    """A quiet as the snapshot lists it, its end a local time in `zone`."""
    if quiet.through_host:
        return line("look_quiet_host", language, subject=subject, host=host)
    until = None
    if quiet.until is not None:
        until = datetime.strptime(quiet.until, LOCAL_TIME).replace(tzinfo=zone or UTC)
    return _quiet_line(subject, quiet.mode, until, now, zone, language)


def _quiet_line(
    subject: str,
    mode: QuietMode,
    until: datetime | None,
    now: datetime,
    zone: tzinfo | None,
    language: str,
) -> str:
    if mode == "snooze" and until is not None:
        said = when(until, now, zone, language)
        return line("look_snoozed", language, subject=subject, until=said)
    return line("look_left_alone", language, subject=subject)
