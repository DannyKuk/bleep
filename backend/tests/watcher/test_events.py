import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bleep.schemas.docker import ContainerSummary, DockerEvent
from bleep.schemas.incidents import Subject
from bleep.watcher.events import diff, event_key, translate
from fakes import compose_labels

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent.parent / "fixtures" / "labels"
SVC = Subject(kind="service", host_id="h", service_id="h/web")


def subject_of(name, container_id, labels):
    return SVC


def box(cid="c1", name="web-1", state="running", **fields) -> ContainerSummary:
    return ContainerSummary(id=cid, name=name, image="img", state=state, **fields)


def event(action, cid="c1", name="web-1", at=0, **attributes) -> DockerEvent:
    return DockerEvent(
        time=T0 + timedelta(seconds=at),
        type="container",
        action=action,
        container_id=cid,
        container_name=name,
        attributes={"name": name, **attributes},
    )


def run(events, known=(), covered=None):
    return translate(events, {c.id: c for c in known}, subject_of, covered or {})


def test_die_carries_exit_code_and_the_restart_policy():
    t = run([event("die", exitCode="1")], [box(restart_policy="always")])
    [signal] = t.signals
    assert signal.kind == "container_died"
    assert signal.container == "web-1"
    assert signal.subject == SVC
    assert signal.at == T0
    assert signal.detail == {"exit_code": 1, "restart_policy": "always"}


def test_oom_and_unhealthy_become_their_own_signals():
    t = run([event("oom"), event("health_status: unhealthy", at=1)])
    assert [s.kind for s in t.signals] == ["oom", "unhealthy"]


def test_health_status_healthy_and_start_make_nothing():
    assert run([event("health_status: healthy"), event("start"), event("kill")]).signals == []


def test_bleep_self_emits_nothing_by_event_label_or_by_snapshot_label():
    by_event = event("die", exitCode="1", **{"bleep.self": "true"})
    by_snapshot = event("oom", cid="c2", name="bleep")
    t = run([by_event, by_snapshot], [box("c2", "bleep", labels={"bleep.self": "true"})])
    assert t.signals == []


def test_a_stopped_container_is_not_a_death():
    t = run([event("kill"), event("die", exitCode="143", at=1), event("stop", at=2)])
    assert t.signals == []


def test_a_restart_event_counts_unless_its_die_is_in_the_batch():
    assert [s.kind for s in run([event("restart")]).signals] == ["container_died"]
    both = run([event("die", exitCode="1"), event("restart", at=1)])
    assert len(both.signals) == 1
    assert both.died_ids == {"c1"}


def test_a_manual_restart_is_not_a_death():
    t = run([event("kill"), event("die", exitCode="0"), event("stop"), event("restart", at=3)])
    assert t.signals == []


def test_events_for_a_death_the_diff_already_reported_are_skipped():
    covered = {"c1": T0 + timedelta(seconds=5)}
    assert run([event("die", exitCode="1", at=4)], covered=covered).signals == []
    assert len(run([event("die", exitCode="1", at=6)], covered=covered).signals) == 1


def test_event_key_tells_apart_by_container_action_and_time():
    assert event_key(event("die")) == event_key(event("die"))
    assert event_key(event("die")) != event_key(event("die", at=1))
    assert event_key(event("die")) != event_key(event("oom"))


def test_diff_reports_a_container_gone_once_and_ignores_clean_exits():
    before = [box("a", "gone", restart_policy="always"), box("b", "done"), box("c", "up")]
    after = [box("b", "done", state="exited", exit_code=0), box("c", "up")]
    [signal] = diff(before, after, T0, subject_of, set(), set())
    assert signal.container == "gone"
    assert signal.detail == {"exit_code": None, "restart_policy": "always"}


def test_diff_reports_a_crash_and_a_restart_count_increase():
    before = [box("a", "crash"), box("b", "flap", restart_count=1)]
    after = [
        box("a", "crash", state="exited", exit_code=1),
        box("b", "flap", restart_count=2),
    ]
    out = diff(before, after, T0, subject_of, set(), set())
    assert [(s.container, s.detail["exit_code"]) for s in out] == [("crash", 1), ("flap", None)]


def test_diff_skips_handled_deliberate_self_and_not_running_before():
    before = [
        box("a", "handled"),
        box("b", "deliberate"),
        box("c", "self", labels={"bleep.self": "true"}),
        box("d", "was-down", state="exited"),
    ]
    assert diff(before, [], T0, subject_of, {"a"}, {"b"}) == []


def expects_fetch(subject, name, labels):
    return labels.get("com.docker.compose.service") == "fetch" or name == "fetch-1"


def test_a_failed_run_rm_job_is_a_death_although_it_is_removed_and_marked_expected():
    job = compose_labels("shop", "fetch")
    failed = [event("die", exitCode="3", **job), event("destroy", at=1, **job)]
    t = translate(failed, {}, subject_of, {}, expects_fetch)
    [signal] = t.signals
    assert signal.detail["exit_code"] == 3
    assert signal.detail["expected_exit"] is True
    assert signal.detail["compose_service"] == "fetch"


def test_a_stop_still_silences_a_job_and_a_removal_still_silences_any_other_container():
    job = {"com.docker.compose.service": "fetch"}
    stopped = [event("die", exitCode="143", **job), event("stop", at=1, **job)]
    assert translate(stopped, {}, subject_of, {}, expects_fetch).signals == []
    removed = [event("die", exitCode="3"), event("destroy", at=1)]
    assert translate(removed, {}, subject_of, {}, expects_fetch).signals == []


def test_diff_takes_a_vanished_job_for_its_rm_end_and_still_reports_its_crash():
    before = [box("a", "fetch-1"), box("b", "other")]
    assert [s.container for s in diff(before, [], T0, subject_of, set(), set(), expects_fetch)] == [
        "other"
    ]
    after = [box("a", "fetch-1", state="exited", exit_code=1)]
    [signal] = diff(before[:1], after, T0, subject_of, set(), set(), expects_fetch)
    assert signal.detail == {"exit_code": 1, "restart_policy": "", "expected_exit": True}


def test_a_death_of_a_container_compose_did_not_create_names_no_compose_service():
    canary = json.loads((FIXTURES / "image-only-shop-canary.json").read_text())["labels"]
    [signal] = translate([event("die", exitCode="1", **canary)], {}, subject_of, {}).signals
    assert "compose_service" not in signal.detail
    created = json.loads((FIXTURES / "compose-shop-proxy.json").read_text())["labels"]
    [signal] = translate([event("die", exitCode="1", **created)], {}, subject_of, {}).signals
    assert signal.detail["compose_service"] == "shop-proxy"


def test_diff_names_no_compose_service_for_an_image_only_container():
    canary = json.loads((FIXTURES / "image-only-shop-canary.json").read_text())["labels"]
    before = [box("a", "shop-canary", labels=canary)]
    after = [box("a", "shop-canary", state="exited", exit_code=1, labels=canary)]
    [signal] = diff(before, after, T0, subject_of, set(), set())
    assert "compose_service" not in signal.detail
