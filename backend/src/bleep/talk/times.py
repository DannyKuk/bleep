"""Times as a person reads them in a chat, the same on Telegram and in the dashboard's panel."""

from datetime import UTC, datetime, timedelta, tzinfo

from bleep import prompts


def _phrase(name: str, language: str, **values: str) -> str:
    return prompts.load_in(language, f"talk/{name}", **values).strip()


def when(at: datetime, now: datetime, zone: tzinfo | None, language: str = "en") -> str:
    """A time as a person says it in a chat, in `zone` and `language`: "14:30" today,
    "tomorrow 08:00", else the weekday ("Thu 08:00"), as the dashboard's quiet pill says it.
    Without a zone (none was configured) it is UTC and says so: "14:30 UTC"."""
    if zone is None:
        return _phrase("when_utc", language, when=when(at, now, UTC, language))
    local, today = at.astimezone(zone), now.astimezone(zone).date()
    time = local.strftime("%H:%M")
    if local.date() == today:
        return time
    if local.date() == today + timedelta(days=1):
        return _phrase("when_tomorrow", language, time=time)
    day = _phrase("weekdays", language).split()[local.weekday()]
    return _phrase("when_day", language, day=day, time=time)


def duration(span: timedelta, language: str = "en") -> str:
    """A span as a person says it in a chat: "4 min", "2 h", "3 days"; Telegram's words
    (`prompts/notify/duration_*`), shared with the panel."""
    minutes = max(int(span.total_seconds() // 60), 1)
    if minutes < 60:
        return _said("duration_min", language, n=str(minutes))
    if minutes < 48 * 60:
        return _said("duration_h", language, n=str(minutes // 60))
    return _said("duration_days", language, n=str(minutes // (24 * 60)))


def _said(name: str, language: str, **values: str) -> str:
    return prompts.load_in(language, f"notify/{name}", **values).strip()
