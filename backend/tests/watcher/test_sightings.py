from datetime import UTC, datetime, timedelta

from bleep.schemas.docker import ContainerSummary, DockerEvent
from bleep.watcher.sightings import Sightings

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def box(name: str, state: str = "running", exit_code: int | None = None) -> ContainerSummary:
    return ContainerSummary(
        id=f"id-{name}", name=name, image="img", state=state, exit_code=exit_code
    )


def ev(action: str, name: str, seconds: float) -> DockerEvent:
    return DockerEvent(
        time=T0 + timedelta(seconds=seconds),
        type="container",
        action=action,
        container_id=f"id-{name}",
        container_name=name,
    )


def test_a_stopped_container_is_remembered_until_it_starts_again():
    seen = Sightings()
    seen.saw("h", T0, [], [box("web")], set())
    stop = [ev("kill", "web", 1), ev("die", "web", 11), ev("stop", "web", 11)]
    seen.saw("h", T0, stop, [box("web", "exited", 137)], set())
    assert seen.of("h").stopped_on_purpose == {"id-web"}
    seen.saw("h", T0, [], [box("web", "exited", 137)], set())  # kept while it stays down
    assert seen.of("h").stopped_on_purpose == {"id-web"}
    seen.saw("h", T0, [ev("start", "web", 30)], [box("web")], set())
    assert seen.of("h").stopped_on_purpose == frozenset()


def test_a_stop_then_a_start_then_a_crash_in_one_tick_is_not_on_purpose():
    seen = Sightings()
    seen.saw("h", T0, [], [box("web")], set())
    events = [ev("stop", "web", 1), ev("start", "web", 2), ev("die", "web", 5)]
    seen.saw("h", T0, list(reversed(events)), [box("web", "exited", 137)], {"id-web"})
    assert seen.of("h").stopped_on_purpose == frozenset()


def test_a_container_running_again_or_gone_is_forgotten_even_without_its_start_event():
    seen = Sightings()
    seen.saw("h", T0, [ev("stop", "a", 1), ev("stop", "b", 1)], [box("a", "exited", 137)], set())
    assert seen.of("h").stopped_on_purpose == {"id-a"}  # b is not listed
    seen.saw("h", T0, [], [box("a")], set())  # the start event was missed
    assert seen.of("h").stopped_on_purpose == frozenset()


def test_what_is_down_at_the_first_listing_is_down_at_start_unless_a_death_explains_it():
    seen = Sightings()
    listing = [box("web", "exited", 1), box("job", "exited", 2), box("db"), box("p", "paused")]
    seen.saw("h", T0, [], listing, died={"id-job"})
    assert seen.of("h").first_listing == T0
    assert seen.of("h").down_at_start == {"id-web", "id-p"}


def test_only_the_first_listing_is_the_baseline():
    seen = Sightings()
    seen.saw("h", T0, [], [box("web")], set())
    later = T0 + timedelta(seconds=15)
    seen.saw("h", later, [], [box("web", "exited", 1)], set())
    assert seen.of("h").first_listing == T0
    assert seen.of("h").down_at_start == frozenset()


def test_a_container_down_at_start_is_forgotten_once_it_starts():
    seen = Sightings()
    seen.saw("h", T0, [], [box("web", "exited", 1)], set())
    seen.saw("h", T0, [ev("start", "web", 3)], [box("web", "exited", 1)], set())
    assert seen.of("h").down_at_start == frozenset()  # it started and died again: seen anew


def test_hosts_are_kept_apart_and_a_forgotten_host_starts_over():
    seen = Sightings()
    seen.saw("a", T0, [], [box("web", "exited", 1)], set())
    seen.saw("b", T0, [], [box("web")], set())
    assert seen.of("b").down_at_start == frozenset()
    seen.forget("a")
    assert seen.of("a").first_listing is None
    assert seen.of("a").down_at_start == frozenset()
