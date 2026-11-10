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
    action: TalkAction, snapshot: Snapshot, now: datetime, zone: tzinfo | None, spent: bool
) -> Offer | str:
    """The offer of a look at the service `action` names, or the fixed line that says why
    there is none. `spent`: the day's cost cap for incidents is spent."""
    found = find_subject(snapshot, action.subject)
    if found is None:
        return line("no_subject")
    if isinstance(found, str):
        return found
    if found.scope == "host":
        return line("look_host", host=found.name)
    service = next(s for s in snapshot.services if s.id == found.target_id)
    if service.open_incident:
        return line("look_open", incident=service.open_incident, subject=found.name)
    if found.quiet is not None:
        return _quiet(found.name, found.host, found.quiet, now, zone)
    host = next((h for h in snapshot.hosts if h.id == found.host), None)
    if host is not None and not host.reachable:
        return line("look_unreachable", subject=found.name, host=found.host)
    if spent:
        return line("look_spent")
    return Offer(kind="look_again", scope="service", target_id=found.target_id, subject=found.name)


def look_said(look: Look, now: datetime, zone: tzinfo | None) -> str:
    """What a press of a look's Yes is answered with."""
    subject = title_name(look.name)
    incident = (look.incident.short_name or look.incident.id) if look.incident is not None else ""
    match look.outcome:
        case "opened":
            return line("look_started", subject=subject, incident=incident)
        case "open":
            return line("look_open", incident=incident, subject=subject)
        case "quiet" if look.quiet is not None:
            if look.quiet.scope == "host":
                return line("look_quiet_host", subject=subject, host=look.host_id)
            return _quiet_line(subject, look.quiet.mode, look.quiet.until, now, zone)
        case "unreachable":
            return line("look_unreachable", subject=subject, host=look.host_id)
        case "spent":
            return line("look_spent")
    return line("look_unknown")


def _quiet(subject: str, host: str, quiet: QuietLine, now: datetime, zone: tzinfo | None) -> str:
    """A quiet as the snapshot lists it, its end a local time in `zone`."""
    if quiet.through_host:
        return line("look_quiet_host", subject=subject, host=host)
    until = None
    if quiet.until is not None:
        until = datetime.strptime(quiet.until, LOCAL_TIME).replace(tzinfo=zone or UTC)
    return _quiet_line(subject, quiet.mode, until, now, zone)


def _quiet_line(
    subject: str, mode: QuietMode, until: datetime | None, now: datetime, zone: tzinfo | None
) -> str:
    if mode == "snooze" and until is not None:
        return line("look_snoozed", subject=subject, until=when(until, now, zone))
    return line("look_left_alone", subject=subject)
