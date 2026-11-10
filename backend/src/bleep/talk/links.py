"""The pages an answer names, found by code (#391).

The dashboard's panel links a service or an incident where its name stands in an answer. The
names and ids come from Bleep's own state, never from the model: the model may write any text,
and only a name Bleep knows becomes a link, to the page of the id Bleep has for it. A name is
found where it stands alone (not inside a longer word or name), the longest first, so "shop #14"
links the incident and not the service "shop" inside it. The panel splits the answer the same
way (`frontend/lib/talk-links.ts`).
"""

import re
from collections import Counter
from collections.abc import Iterable

from bleep.schemas.talk import IncidentFacts, Offer, Snapshot, TalkLink

CUT = "…"  # how the snapshot ends a name or id it cut: such an id names no page exactly


def known_links(snapshot: Snapshot, facts: IncidentFacts | None = None) -> list[TalkLink]:
    """Every name in the snapshot that can be a link: services whose name no other service has,
    and incidents by short name."""
    services = [s for s in snapshot.services if not (s.id.endswith(CUT) or s.name.endswith(CUT))]
    names = Counter(s.name for s in services)
    links = [
        TalkLink(kind="service", id=s.id, label=s.name) for s in services if names[s.name] == 1
    ]
    incidents = [*([facts] if facts else []), *snapshot.open_incidents, *snapshot.ended_last_24h]
    links += [
        TalkLink(kind="incident", id=i.id, label=i.name)
        for i in incidents
        if i.name and not i.name.endswith(CUT)
    ]
    return links


def offer_links(offer: Offer) -> list[TalkLink]:
    """The pages an offer names: its incident and, for a service, its subject."""
    links = []
    if offer.incident_id and offer.incident:
        links.append(TalkLink(kind="incident", id=offer.incident_id, label=offer.incident))
    if offer.scope == "service" and offer.target_id and offer.subject:
        links.append(TalkLink(kind="service", id=offer.target_id, label=offer.subject))
    return links


def links_in(text: str, candidates: Iterable[TalkLink]) -> list[TalkLink]:
    """The candidates whose label stands in `text`, once each, in the order they first stand."""
    by_label: dict[str, TalkLink] = {}
    for link in candidates:
        if link.label.strip():
            by_label.setdefault(link.label, link)
    if not by_label:
        return []
    labels = sorted(by_label, key=len, reverse=True)
    names = "|".join(map(re.escape, labels))
    pattern = re.compile(rf"(?<![\w-])(?:{names})(?![\w-])", re.ASCII)
    found: dict[str, TalkLink] = {}
    for match in pattern.finditer(text):
        found.setdefault(match.group(0), by_label[match.group(0)])
    return list(found.values())
