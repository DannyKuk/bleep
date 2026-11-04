"""The hosts added on the dashboard, below the API (#194): what is written where and in which
order, what the listeners hear, and what a restart reads back."""

import logging
from datetime import UTC, datetime, timedelta

import asyncssh
import pytest
from pydantic import SecretStr

from bleep.hosts import dashboard as dashboard_module
from bleep.hosts.dashboard import (
    HOST_KEYS,
    SSH_KEYS,
    DashboardHosts,
    HostChange,
    HostConflict,
    HostStale,
)
from bleep.schemas.dashboard_hosts import DashboardHostEdit, HostKeyConfirmation, NewDashboardHost
from bleep.store.db import Database
from bleep.store.secrets import SecretNotSaved, SecretStore

T0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
SERVER_KEY = asyncssh.generate_private_key("ssh-ed25519")
PEM = asyncssh.generate_private_key("ssh-ed25519").export_private_key().decode()


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


async def scan(host, port, **kwargs):
    return SERVER_KEY


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(tmp_path / "bleep.db")
    await database.migrate()
    return database


def secret_store(tmp_path) -> SecretStore:
    return SecretStore(tmp_path / "secrets.json", (), families=(SSH_KEYS, HOST_KEYS), env={})


def hosts(db, tmp_path, file_ids=("a",), clock=None) -> DashboardHosts:
    return DashboardHosts(db, secret_store(tmp_path), file_ids, scan, clock or Clock())


async def new(store: DashboardHosts, host_id: str = "c", **fields) -> NewDashboardHost:
    offer = await store.offer_host_key(fields.get("address", "box.lan"), fields.get("port", 22))
    confirmation = HostKeyConfirmation(offer=offer.offer, fingerprint=offer.fingerprint)
    body = {"id": host_id, "name": "Box", "address": "box.lan", "user": "deploy", "ssh_key": PEM}
    return NewDashboardHost(**(body | fields), host_key=confirmation)


def edit(**fields) -> DashboardHostEdit:
    return DashboardHostEdit(**({"name": "Box", "address": "box.lan", "user": "deploy"} | fields))


async def test_listeners_hear_each_change_with_the_host_as_it_is_now(db, tmp_path):
    store = hosts(db, tmp_path)
    heard: list[HostChange] = []

    async def listener(change: HostChange) -> None:
        heard.append(change)

    store.subscribe(listener)
    await store.add(await new(store))
    await store.change("c", edit(name="Box 2"), version=store.version("c"))
    await store.change("c", edit(name="Box 2", user="root"), version=store.version("c"))
    await store.change(
        "c",
        edit(name="Box 2", user="root", docker_socket="/run/docker.sock"),
        version=store.version("c"),
    )
    await store.remove("c", version=store.version("c"))

    assert [(c.kind, c.config.name if c.config else None) for c in heard] == [
        ("added", "Box"),
        ("renamed", "Box 2"),
        ("connection", "Box 2"),
        ("connection", "Box 2"),
        ("removed", None),
    ]
    assert heard[0].config.source == "dashboard" and heard[0].config.key_file is None


async def test_a_listener_that_fails_is_logged_and_the_change_stays_saved(db, tmp_path, caplog):
    store = hosts(db, tmp_path)

    async def broken(change: HostChange) -> None:
        raise RuntimeError("the watcher is down")

    store.subscribe(broken)
    await store.add(await new(store))
    assert "putting the change of c in force failed" in caplog.text
    assert [c.id for c in await hosts(db, tmp_path).load()] == ["c"]


async def test_a_restart_reads_the_hosts_back_in_the_order_they_were_added(db, tmp_path):
    clock = Clock()
    store = hosts(db, tmp_path, clock=clock)
    await store.add(await new(store, "zed"))
    clock.now += timedelta(seconds=1)
    await store.add(await new(store, "abe"))

    again = hosts(db, tmp_path)
    assert [c.id for c in await again.load()] == ["zed", "abe"]
    assert again.version("zed") == store.version("zed")
    assert again.connection("abe").ssh_key.set


async def test_a_host_the_hosts_file_names_now_wins_and_its_entry_can_only_be_removed(
    db, tmp_path, caplog
):
    store = hosts(db, tmp_path, file_ids=())
    await store.add(await new(store, "a"))

    shadowed = hosts(db, tmp_path, file_ids=("a",))
    assert await shadowed.load() == []
    assert "a is in the hosts file, which wins" in caplog.text
    with pytest.raises(HostConflict):
        await shadowed.change("a", edit(), version=shadowed.version("a"))
    heard: list[HostChange] = []

    async def listener(change: HostChange) -> None:
        heard.append(change)

    shadowed.subscribe(listener)
    await shadowed.remove("a", version=shadowed.version("a"))
    assert heard == []  # the file's host "a" is not touched
    assert shadowed._secrets.stored(SSH_KEYS) == []


async def test_keys_left_for_a_host_that_no_longer_exists_are_removed_at_load(db, tmp_path):
    secrets = secret_store(tmp_path)
    await secrets.replace({"SSH_KEY:gone": PEM, "KNOWN_HOSTS:gone": "gone ssh-ed25519 AAAA"})
    store = DashboardHosts(db, secrets, (), scan, Clock())
    await store.load()
    assert secrets.stored(SSH_KEYS) == [] and secrets.stored(HOST_KEYS) == []


async def test_a_secret_write_that_fails_on_add_leaves_no_host_behind(db, tmp_path):
    store = hosts(db, tmp_path)

    async def refuse(changed):
        raise RuntimeError("disk full")

    store._secrets.subscribe(refuse, before_write=True)
    with pytest.raises(SecretNotSaved):
        await store.add(await new(store))
    assert await hosts(db, tmp_path).load() == []
    with pytest.raises(KeyError):
        store.version("c")


async def test_a_secret_write_that_fails_on_a_move_leaves_the_old_connection_and_keys(db, tmp_path):
    store = hosts(db, tmp_path)
    await store.add(await new(store))
    version = store.version("c")

    async def refuse(changed):
        raise RuntimeError("disk full")

    store._secrets.subscribe(refuse, before_write=True)
    with pytest.raises(SecretNotSaved):
        await store.change("c", edit(address="other.lan"), version=version)
    assert store.version("c") == version
    assert store.connection("c").address == "box.lan" and store.connection("c").ssh_key.set
    (config,) = await hosts(db, tmp_path).load()
    assert config.address == "box.lan"


async def test_every_change_makes_a_new_version_and_an_old_one_is_stale(db, tmp_path):
    store = hosts(db, tmp_path)
    await store.add(await new(store))
    first = store.version("c")
    await store.change("c", edit(ssh_key=SecretStr(PEM)), version=first)  # the same key again
    assert store.version("c") != first
    with pytest.raises(HostStale) as stale:
        await store.change("c", edit(name="Late"), version=first)
    assert stale.value.current == store.version("c")
    with pytest.raises(HostStale):
        await store.remove("c", version=first)


async def test_an_offer_expires_after_ten_minutes(db, tmp_path):
    clock = Clock()
    store = hosts(db, tmp_path, clock=clock)
    body = await new(store)
    clock.now += timedelta(minutes=10)
    with pytest.raises(HostConflict, match="expired"):
        await store.add(body)


async def test_at_most_sixteen_offers_are_kept_and_the_oldest_goes(db, tmp_path, monkeypatch):
    store = hosts(db, tmp_path)
    first = await new(store)
    for _ in range(dashboard_module.MAX_OFFERS):
        await store.offer_host_key("box.lan", 22)
    assert len(store._offers) == dashboard_module.MAX_OFFERS
    with pytest.raises(HostConflict):
        await store.add(first)


async def test_nothing_about_a_key_is_logged(db, tmp_path, caplog):
    store = hosts(db, tmp_path)
    with caplog.at_level(logging.DEBUG):
        await store.add(await new(store))
        await store.change("c", edit(address="other.lan"), version=store.version("c"))
    body = PEM.splitlines()[2]
    assert body not in caplog.text
    assert SERVER_KEY.export_public_key().decode().split()[1] not in caplog.text
    assert "connects elsewhere now" in caplog.text
