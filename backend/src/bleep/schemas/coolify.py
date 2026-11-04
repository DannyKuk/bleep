"""What Bleep reads from a Coolify instance: the few fields its tools show, nothing else.

Coolify's answers carry far more (webhook secrets, compose files, build logs, env values); these
models ignore every field they do not name, so those never leave the client. Text fields are set
in Coolify or by a commit, so they are untrusted wherever they go next.
"""

from pydantic import BaseModel


class CoolifyApplication(BaseModel):
    uuid: str
    id: int | None = None
    name: str = ""
    status: str = ""  # "running:healthy", "exited:unhealthy", ...
    fqdn: str | None = None  # the application's domains, comma separated
    git_repository: str | None = None
    git_branch: str | None = None
    git_commit_sha: str | None = None
    build_pack: str | None = None
    last_online_at: str | None = None


class CoolifyDeployment(BaseModel):
    deployment_uuid: str
    status: str  # queued, in_progress, finished, failed, cancelled-by-user
    commit: str | None = None
    commit_message: str | None = None
    created_at: str | None = None
    finished_at: str | None = None
    is_webhook: bool = False
    is_api: bool = False
    restart_only: bool = False
    rollback: bool = False
    pull_request_id: int = 0


class CoolifyEnvKey(BaseModel):
    """One environment variable of an application: its key and flags, never its value."""

    key: str
    is_buildtime: bool | None = None
    is_runtime: bool | None = None
    is_preview: bool = False
