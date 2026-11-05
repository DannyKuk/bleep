from datetime import UTC, datetime
from importlib import resources

from bleep.incidents.approvals import ApprovalStore
from bleep.schemas.graph import PlanStep, Proposal
from bleep.store.db import Database

T0 = datetime(2026, 11, 5, 7, 45, tzinfo=UTC)
PLAN = Proposal(
    kind="act",
    steps=[PlanStep(action="docker.start", host_id="a", target="molecula-web-1")],
    reason="it stopped",
)


async def test_a_pending_approval_left_on_a_finished_incident_is_closed_by_the_migration(
    tmp_path,
):
    packaged = resources.files("bleep.store") / "migrations"
    old = tmp_path / "migrations"
    old.mkdir()
    for file in packaged.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) <= 25:
            (old / file.name).write_text(file.read_text())
    path = tmp_path / "bleep.sqlite"
    await Database(path, migrations=old).migrate()
    conn = await Database(path, migrations=old).connect()
    try:
        for n, status in enumerate(["closed", "awaiting_approval"]):
            await conn.execute(
                "INSERT INTO incidents (id, kind, host_id, service_id, subject_key, status,"
                " title, opened_at, fold_key) VALUES (?, 'service', 'a', ?, ?, ?, 't', ?, 'fk')",
                (f"inc{n}", f"s{n}", f"service:a:s{n}", status, T0.isoformat()),
            )
            await conn.execute(
                "INSERT INTO approvals (id, incident_id, plan_hash, proposal, snapshots, status,"
                " requested_at) VALUES (?, ?, 'h', ?, '[]', 'pending', ?)",
                (f"ap{n}", f"inc{n}", PLAN.model_dump_json(), T0.isoformat()),
            )
        await conn.commit()
    finally:
        await conn.close()

    await Database(path).migrate()

    store = ApprovalStore(Database(path), "secret")
    left_behind, waiting = await store.current("inc0"), await store.current("inc1")
    assert left_behind is not None and left_behind.closed and left_behind.decision is None
    assert waiting is not None and not waiting.closed
    assert [record.id for record in await store.pending()] == ["ap1"]
