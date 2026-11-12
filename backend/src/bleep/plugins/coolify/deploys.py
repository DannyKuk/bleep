"""Which commit a Coolify application runs, for `deployed_commits` (`tools/github.py`).

The application is the one the service's containers name in `coolify.applicationUuid`. Only a
finished deployment ever ran; a failed, queued or cancelled one did not, and a preview's
(`pull_request_id`) runs beside the application, not as it. The running commit is that of the
newest finished deployment, live since the oldest finished deployment of that same commit in a
row: a redeploy of the same commit, as `coolify.redeploy` makes, changes no code. The commit
before it is that of the next finished deployment of another commit. Coolify keeps the ref it
deploys (`HEAD`) in the commit field too, so only a hex sha names a commit.
"""

from datetime import UTC, datetime

from bleep.hosts.coolify_names import COOLIFY_UUID
from bleep.plugins.coolify.client import CoolifyClient, CoolifyError
from bleep.schemas.coolify import CoolifyDeployment
from bleep.schemas.github import RunningCommit
from bleep.schemas.services import Service
from bleep.tools.github import SHA, DeployUnknown

DEPLOYMENTS = 30  # the newest ones read to find the commit deployed before the running one


class CoolifyDeploys:
    """The running commit of a service Coolify deployed, read from its deployments."""

    def __init__(self, client: CoolifyClient) -> None:
        self._client = client

    async def running_commit(self, service: Service) -> RunningCommit | None:
        """None for a service no Coolify application's containers make up."""
        uuids = {c.labels.get(COOLIFY_UUID) for c in service.containers} - {None, ""}
        if not uuids:
            return None
        if len(uuids) > 1:
            raise DeployUnknown("the service's containers belong to several Coolify applications")
        (uuid,) = uuids
        try:
            found = await self._client.deployments(uuid, take=DEPLOYMENTS)
        except CoolifyError as error:
            raise DeployUnknown(f"Coolify's deployments could not be read ({error})") from None
        return running_commit(found)


def running_commit(deployments: list[CoolifyDeployment]) -> RunningCommit:
    """The commit the newest finished deployment of `deployments` (newest first) runs, since
    when, and the commit deployed before it when the list still holds one."""
    finished = [d for d in deployments if d.status == "finished" and not d.pull_request_id]
    if not finished:
        raise DeployUnknown("Coolify lists no finished deployment of the application")
    sha = _sha(finished[0].commit)
    if sha is None:
        raise DeployUnknown("Coolify records no commit for the deployment that runs")
    first, previous = finished[0], None
    for d in finished[1:]:
        if _sha(d.commit) != sha:
            previous = _sha(d.commit)
            break
        first = d
    return RunningCommit(sha=sha, how="coolify", since=_time(first.finished_at), previous=previous)


def _sha(commit: str | None) -> str | None:
    return commit.lower() if commit and SHA.fullmatch(commit) else None


def _time(text: str | None) -> datetime | None:
    """Coolify's `2026-11-04T07:52:40.000000Z`, or None for anything else."""
    try:
        at = datetime.fromisoformat(text or "")
    except ValueError:
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)
