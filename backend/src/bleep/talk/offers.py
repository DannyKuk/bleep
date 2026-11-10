"""Turn the action the model read into an offer to confirm with one button, or into a fixed line
that says why Bleep cannot offer it (docs/design/notify.md, #402).

Code checks every field against Bleep's own state: the subject is a service or a host Bleep
knows, by its id or its name; a snooze ends after now and within `MAX_SNOOZE`; the incident is
open; for an instruction or a yes, a plan waits. The confirm line is built from the checked
fields with the fixed templates `prompts/talk/confirm_*.md`, never from the model's words, so
what the person confirms is exactly what runs.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import cast

from bleep import prompts
from bleep.guard.titles import title_name
from bleep.incidents.quiet import MAX_SNOOZE, morning_of
from bleep.schemas.incidents import TERMINAL_STATUSES
from bleep.schemas.quiet import QuietScope
from bleep.schemas.talk import (
    IncidentFacts,
    IncidentLine,
    Offer,
    OfferKind,
    QuietLine,
    ServiceLine,
    Snapshot,
    TalkAction,
)
from bleep.talk.times import when

LOCAL_TIME = "%Y-%m-%d %H:%M"
LOCAL_DAY = "%Y-%m-%d"
CUT = "…"  # how the snapshot ends a name it cut: such an id names no service exactly
QUIET_KINDS = ("snooze", "leave_alone", "watch_again")
SUBJECT_KINDS = (*QUIET_KINDS, "look_again")  # the offers about a service or host
NEVER = datetime.max.replace(tzinfo=UTC)  # an end beyond any limit
PLAN_KINDS = ("instruct", "show_plan")


def line(name: str, language: str, /, **values: str) -> str:
    """The fixed line `talk/<name>` in `language`."""
    return prompts.load_in(language, f"talk/{name}", **values).strip()


@dataclass(frozen=True)
class Subject:
    """A service or a host as Bleep's state lists it: what a quiet is set on, and its name."""

    scope: QuietScope
    target_id: str
    name: str
    quiet: QuietLine | None
    host: str


def _same(a: str, b: str) -> bool:
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


def find_subject(
    snapshot: Snapshot, said: str | None, language: str = "en"
) -> Subject | str | None:
    """The service or host `said` names: by a service's id, else by a service's name or a
    host's id or name; only when none fits, the one service that has a container of that name
    (#429). None when Bleep knows none; the `ambiguous_subject` line when the name fits more
    than one: two services, a service and a host, or a container name of two services. A service
    whose name another service or a host also has is named with its host ("web on laptop"), so
    the line the person confirms can only mean that one. The lines are in `language`."""
    if not said:
        return None
    services = [s for s in snapshot.services if not s.id.endswith(CUT)]
    by_id = [s for s in services if _same(said, s.id)] or _on_host(snapshot, services, said)
    named = by_id or [s for s in services if _same(said, s.name)]
    hosts = [] if by_id else [h for h in snapshot.hosts if _same(said, h.id) or _same(said, h.name)]
    if not named and not hosts:
        in_container = _with_container(services, said)
        if len(in_container) > 1:
            return line("ambiguous_subject", language, subject=_container_name(in_container, said))
        named = in_container
    if len(named) + len(hosts) > 1:
        known = title_name(named[0].name) if named else hosts[0].id  # never the model's words
        return line("ambiguous_subject", language, subject=known)
    if hosts:
        host = hosts[0]
        return Subject("host", host.id, host.id, host.quiet, host.id)
    if not named:
        return None
    service = named[0]
    name = title_name(service.name)
    shared = [s for s in snapshot.services if s.id != service.id and _same(s.name, service.name)]
    shared += [
        h for h in snapshot.hosts if _same(service.name, h.id) or _same(service.name, h.name)
    ]
    if shared:
        name = line("subject_on_host", language, subject=name, host=service.host)
    return Subject("service", service.id, name, service.quiet, service.host)


def _with_container(services: list[ServiceLine], said: str) -> list[ServiceLine]:
    """The services with a container called `said`, the name people read in `docker ps` (#429)."""
    return [s for s in services if any(_same(said, c.name) for c in s.containers)]


def _container_name(services: list[ServiceLine], said: str) -> str:
    """The container name as Bleep's state writes it, never the model's words."""
    return next(c.name for c in services[0].containers if _same(said, c.name))


def _on_host(snapshot: Snapshot, services: list[ServiceLine], said: str) -> list[ServiceLine]:
    """The service `said` names as "<service name> on <host id or name>", as the confirm lines
    write a shared name (#410), in any language: the model may take the name from a line it was
    shown in another one. None when it is not written so or names no such service."""
    for connector in _ON_HOST:
        name, on, host = said.rpartition(connector)
        hosts = {h.id for h in snapshot.hosts if _same(host, h.id) or _same(host, h.name)}
        found = [s for s in services if s.host in hosts and _same(name, s.name)] if on else []
        if found:
            return found
    return []


# What stands between a service and its host in `subject_on_host`, in each language (" on ").
_ON_HOST = tuple(
    line("subject_on_host", language, subject="\x00", host="\x00").split("\x00")[1]
    for language in prompts.LANGUAGES
)


def find_incident(
    snapshot: Snapshot, facts: IncidentFacts | None, said: str | None
) -> IncidentLine | None:
    """The incident `said` names (a short name or an id), else the one the message is about."""
    if not said:
        return facts
    known = [*([facts] if facts is not None else []), *snapshot.open_incidents]
    for incident in [*known, *snapshot.ended_last_24h]:
        if _same(said, incident.id) or (incident.name and _same(said, incident.name)):
            return incident
    return None


def snooze_end(action: TalkAction, now: datetime, zone: tzinfo | None) -> datetime | None:
    """When the snooze asked for ends, in UTC: `minutes` from now, else `until` read as a local
    time in `zone`, a day alone as its 08:00. None when the action gives no end Bleep can read,
    or a day alone whose 08:00 has passed (which "until today" may mean); `NEVER` for an end too
    far off to reckon, so it fails the limit as any end beyond it does."""
    try:
        return _snooze_end(action, now, zone or UTC)
    except OverflowError:
        return NEVER


def _snooze_end(action: TalkAction, now: datetime, zone: tzinfo) -> datetime | None:
    if action.minutes is not None:
        return now + timedelta(minutes=action.minutes) if action.minutes > 0 else None
    said = (action.until or "").strip()
    try:
        return datetime.strptime(said, LOCAL_TIME).replace(tzinfo=zone).astimezone(UTC)
    except ValueError:
        pass
    try:
        morning = morning_of(datetime.strptime(said, LOCAL_DAY).date(), zone)
    except ValueError:
        return None
    return morning if morning > now else None


def check(
    action: TalkAction,
    snapshot: Snapshot,
    facts: IncidentFacts | None,
    now: datetime,
    zone: tzinfo | None,
    text: str = "",
    language: str = "en",
) -> Offer | str:
    """The offer for `action`, or the fixed line in `language` that says why there is none.
    `text` is the person's message, the words an instruction sends to a plan."""
    if action.kind in QUIET_KINDS:
        return _quiet(action, snapshot, now, zone, language)
    incident = find_incident(snapshot, facts, action.incident)
    if incident is None:
        return line("no_incident" if action.incident else "which_incident", language)
    name = incident.name or incident.id
    if incident.status in TERMINAL_STATUSES:
        return line("incident_ended", language, incident=name)
    if action.kind in PLAN_KINDS and not incident.plan_waiting:
        return line("no_plan", language, incident=name)
    return Offer(
        kind=cast(OfferKind, action.kind),
        incident_id=incident.id,
        incident=name,
        text=text if action.kind == "instruct" else None,
    )


def _quiet(
    action: TalkAction, snapshot: Snapshot, now: datetime, zone: tzinfo | None, language: str
) -> Offer | str:
    found = find_subject(snapshot, action.subject, language)
    if found is None:
        return line("no_subject", language)
    if isinstance(found, str):
        return found
    offer = Offer(
        kind=cast(OfferKind, action.kind),
        scope=found.scope,
        target_id=found.target_id,
        subject=found.name,
    )
    if action.kind == "watch_again":
        if found.quiet is None:
            return line("not_quiet", language, subject=found.name)
        if found.quiet.through_host:
            return line("quiet_through_host", language, subject=found.name, host=found.host)
        return offer
    if action.kind == "leave_alone":
        return offer
    until = snooze_end(action, now, zone)
    if until is None:
        return line("snooze_how_long", language, subject=found.name)
    if until <= now or until > now + MAX_SNOOZE:
        days = str(MAX_SNOOZE.days)
        return line("snooze_limit", language, subject=found.name, days=days)
    return offer.model_copy(update={"until": until})


def instruction(incident_id: str, incident: str, text: str) -> Offer:
    """The person's words for the plan that waits on an incident."""
    return Offer(kind="instruct", incident_id=incident_id, incident=incident, text=text)


def confirm_line(offer: Offer, now: datetime, zone: tzinfo | None, language: str = "en") -> str:
    """What the person confirms, in `language`, from the offer's checked fields alone."""
    if offer.kind == "snooze" and offer.until is not None:
        until = when(offer.until, now, zone, language)
        return line("confirm_snooze", language, subject=offer.subject or "", until=until)
    if offer.kind in SUBJECT_KINDS:
        return line(f"confirm_{offer.kind}", language, subject=offer.subject or "")
    return line(f"confirm_{offer.kind}", language, incident=offer.incident or "")
