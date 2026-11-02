import logging
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage

from bleep.graph.state import checkpoint_serializer, checkpoint_types
from bleep.schemas.docker import ContainerSummary, DiskUsage, HostBasics
from bleep.schemas.graph import (
    ApprovalGrant,
    ApprovalRequest,
    ContainerMemory,
    Decision,
    Diagnosis,
    Evidence,
    GatherContext,
    PastIncident,
    PlanStep,
    Proposal,
    Report,
    StepResult,
    TimelineEntry,
    VerificationPlan,
    VerificationResult,
)
from bleep.schemas.incidents import Signal, Subject

AT = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def full_state() -> dict:
    subject = Subject(kind="service", host_id="a", service_id="a/compose:shop")
    step = PlanStep(action="docker.restart", host_id="a", target="shop-web-1", params={"x": "1"})
    return {
        "incident_id": "inc_1",
        "subject": subject,
        "signals": [Signal(subject=subject, kind="oom", at=AT, container="shop-web-1")],
        "context": GatherContext(
            at=AT,
            service_name="shop",
            containers=[ContainerSummary(id="1", name="shop-web-1", image="i", state="exited")],
            logs={"shop-web-1": "<untrusted ...>"},
            basics=HostBasics(os="Linux", disks=[DiskUsage(mount="/", used_percent=50)]),
            top_memory=[ContainerMemory(name="db", mem_usage=1, mem_limit=2, mem_percent=50)],
            host_rules=["r"],
            service_rules=["s"],
            similar=[
                PastIncident(
                    incident_id="inc_0",
                    date=AT,
                    title="t",
                    cause="c",
                    action="a",
                    outcome="no_action",
                    human_decision="approve",
                    feedback=None,
                    score=0.7,
                )
            ],
            gaps=["g"],
        ),
        "messages": [HumanMessage("hi"), AIMessage("hello")],
        "investigate_rounds": 1,
        "tool_calls_used": 3,
        "diagnosis": Diagnosis(
            root_cause="bad env",
            category="config",
            evidence=[Evidence(source="logs", detail="KeyError: DATABASE_URL")],
            confidence=0.8,
            affected_services=["shop"],
        ),
        "proposal": Proposal(
            kind="act",
            steps=[step],
            reason="r",
            verification=VerificationPlan(containers=["shop-web-1"], urls=["http://x"]),
        ),
        "instructions": ["redeploy instead"],
        "approval": ApprovalRequest(id="ap_1", plan_hash="h"),
        "decision": Decision(kind="approve", via="dashboard", at=AT),
        "grant": ApprovalGrant(incident_id="inc_1", plan_hash="h", token="t"),
        "step_results": [
            StepResult(
                step_index=0,
                action="docker.restart",
                target="shop-web-1",
                ok=True,
                detail="restarted shop-web-1",
                started_at=AT,
                finished_at=AT,
            )
        ],
        "verify_loops": 0,
        "verification": VerificationResult(ok=True),
        "outcome": "resolved",
        "report": Report(
            title="t",
            summary="s",
            cause="c",
            outcome="Fixed and verified",
            timeline=[TimelineEntry(at=AT, text="Approved on the dashboard")],
        ),
    }


def test_every_model_in_the_state_round_trips_through_the_checkpoint_serializer(caplog):
    state = full_state()
    serde = checkpoint_serializer()
    with caplog.at_level(logging.WARNING):
        back = serde.loads_typed(serde.dumps_typed(state))

    assert back == state
    assert [r.getMessage() for r in caplog.records] == []  # no "unregistered type" warning


def test_the_serializer_refuses_a_class_outside_the_state():
    from bleep.schemas.incidents import IncidentEvent

    serde = checkpoint_serializer()
    event = IncidentEvent(incident_id="i", seq=1, kind="notice", at=AT)
    assert serde.loads_typed(serde.dumps_typed({"x": event}))["x"] != event


def test_checkpoint_types_cover_nested_models():
    names = {cls.__name__ for cls in checkpoint_types()}
    assert {"Subject", "Signal", "GatherContext", "ContainerSummary", "DiskUsage"} <= names
    assert {"PastIncident", "Evidence", "PlanStep", "VerificationPlan", "ApprovalGrant"} <= names
