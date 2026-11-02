import pytest
from pydantic import SecretStr

from bleep.guard.grant import GrantInvalid, grant_token, verify_grant
from bleep.schemas.graph import ApprovalGrant

SECRET = "test-secret"


def grant(incident_id: str = "inc_1", plan: str = "h1", token: str | None = None) -> ApprovalGrant:
    token = token if token is not None else grant_token(SECRET, incident_id, plan)
    return ApprovalGrant(incident_id=incident_id, plan_hash=plan, token=token)


def test_a_grant_for_this_incident_and_plan_passes():
    assert verify_grant(grant(), "inc_1", "h1", SECRET) is None
    assert verify_grant(grant(), "inc_1", "h1", SecretStr(SECRET)) is None


@pytest.mark.parametrize(
    ("given", "incident_id", "plan", "why"),
    [
        (None, "inc_1", "h1", "no grant"),
        (grant("inc_2"), "inc_1", "h1", "another incident"),
        (grant(plan="h2"), "inc_1", "h1", "another plan"),
        (grant(token="0" * 64), "inc_1", "h1", "token does not match"),
        (grant(token="ünïcode"), "inc_1", "h1", "token does not match"),
        (grant(token=""), "inc_1", "h1", "token does not match"),
    ],
)
def test_anything_else_is_refused(given, incident_id, plan, why):
    with pytest.raises(GrantInvalid, match=why):
        verify_grant(given, incident_id, plan, SECRET)


def test_a_grant_whose_fields_were_changed_to_match_is_still_refused():
    # Relabelling a grant for inc_2 as inc_1 keeps inc_2's token, which inc_1 cannot use.
    forged = grant("inc_2").model_copy(update={"incident_id": "inc_1"})
    with pytest.raises(GrantInvalid, match="token does not match"):
        verify_grant(forged, "inc_1", "h1", SECRET)


def test_a_grant_keyed_with_another_secret_is_refused():
    with pytest.raises(GrantInvalid, match="token does not match"):
        verify_grant(grant(), "inc_1", "h1", "other-secret")
