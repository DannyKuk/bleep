import pytest

from bleep.hosts.readings import past_threshold, readings
from bleep.schemas.docker import DiskUsage, HostBasics
from bleep.schemas.settings import Thresholds

BASICS = HostBasics(
    os="Linux",
    disks=[DiskUsage(mount="/", used_percent=49), DiskUsage(mount="/data", used_percent=93)],
    mem_total=4_000,
    mem_available=200,
    load1=1.0,
    load15=6.0,
    cpus=2,
)


def test_a_reading_has_the_fullest_disk_memory_left_and_load_per_cpu():
    assert readings(BASICS) == {
        "disk": {"percent": 93, "mount": "/data"},
        "memory": {"available_percent": 5.0},
        "load": {"load_per_cpu": 3.0},
    }


def test_load_can_be_read_over_the_last_minute():
    assert readings(BASICS, load_minutes=1)["load"] == {"load_per_cpu": 0.5}


def test_a_field_the_host_did_not_report_is_left_out():
    assert readings(HostBasics(os="Linux", mem_total=4_000, load15=1.0)) == {}


@pytest.mark.parametrize(
    ("kind", "detail", "past"),
    [
        ("disk", {"percent": 90}, True),  # at the threshold is past it
        ("disk", {"percent": 89.9}, False),
        ("memory", {"available_percent": 4.9}, True),
        ("memory", {"available_percent": 5}, False),
        ("memory", {}, False),  # an unknown reading is not a breach
        ("load", {"load_per_cpu": 2.01}, True),
        ("load", {"load_per_cpu": 2}, False),
        ("load", {"load_per_cpu": "high"}, False),
    ],
)
def test_past_threshold_is_the_detectors_rule(kind, detail, past):
    assert past_threshold(kind, detail, Thresholds()) is past


def test_a_kind_that_is_no_host_reading_is_refused():
    with pytest.raises(ValueError):
        past_threshold("oom", {}, Thresholds())
