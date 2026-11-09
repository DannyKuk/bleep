from datetime import UTC, datetime
from importlib import resources

from bleep.incidents.repository import IncidentRepo
from bleep.notify.telegram import MessageLog
from bleep.schemas.incidents import Signal, Subject
from bleep.store.db import Database

T0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


async def test_older_incidents_keep_no_short_name_and_older_messages_keep_their_buttons(tmp_path):
    packaged = resources.files("bleep.store") / "migrations"
    old = tmp_path / "migrations"
    old.mkdir()
    for file in packaged.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) <= 26:
            (old / file.name).write_text(file.read_text())
    path = tmp_path / "bleep.sqlite"
    await Database(path, migrations=old).migrate()
    conn = await Database(path, migrations=old).connect()
    try:
        await conn.execute(
            "INSERT INTO incidents (id, kind, host_id, service_id, subject_key, status, title,"
            " opened_at) VALUES ('inc0', 'service', 'a', 'a/compose:shop',"
            " 'service:a:a/compose:shop', 'closed', 'container died', ?)",
            (T0.isoformat(),),
        )
        await conn.execute(
            "INSERT INTO telegram_messages (message_id, incident_id, approval_id, sent_at)"
            " VALUES (311, 'inc0', 'ap1', ?)",
            (T0.isoformat(),),
        )
        await conn.commit()
    finally:
        await conn.close()

    db = Database(path)
    await db.migrate()

    assert (await IncidentRepo(db).get("inc0")).short_name is None
    messages = MessageLog(db)
    assert await messages.ids_for_incident("inc0") == [311]  # sent with buttons, still cleared
    assert await messages.first_for("inc0") == 311
    subject = Subject(kind="service", host_id="a", service_id="a/compose:shop")
    signal = Signal(subject=subject, kind="container_died", at=T0)
    new, _ = await IncidentRepo(db).open_or_attach(subject, [signal])
    assert new.short_name == "shop #2"  # the older incident counts
