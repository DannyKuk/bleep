"""What Bleep reads from a Coolify instance: the few fields its tools show, nothing else.

Coolify's answers carry far more (webhook secrets, compose files, build logs, env values); these
models ignore every field they do not name, so those never leave the client. Every field but the
uuid or key may be null, so one odd record reads as unknowns. Text fields are set in Coolify or
by a commit, so they are untrusted wherever they go next.
"""

import re

from pydantic import BaseModel, Field

# What Bleep accepts as a Coolify uuid; it goes into request paths. Coolify's are 24 [a-z0-9].
UUID = re.compile(r"[A-Za-z0-9-]{1,64}")
_UUID_FIELD = rf"^{UUID.pattern}$"


class CoolifyApplication(BaseModel):
    uuid: str = Field(pattern=_UUID_FIELD)  # the API never shows an application's numeric id
    name: str | None = None
    status: str | None = None  # "running:healthy", "exited:unhealthy", ...
    fqdn: str | None = None  # the application's domains, comma separated
    git_repository: str | None = None
    git_branch: str | None = None
    git_commit_sha: str | None = None
    build_pack: str | None = None
    last_online_at: str | None = None


class CoolifyDeployment(BaseModel):
    deployment_uuid: str = Field(pattern=_UUID_FIELD)
    status: str | None = None  # queued, in_progress, finished, failed, cancelled-by-user
    commit: str | None = None
    commit_message: str | None = None
    created_at: str | None = None
    finished_at: str | None = None
    is_webhook: bool | None = None
    is_api: bool | None = None
    restart_only: bool | None = None
    rollback: bool | None = None
    pull_request_id: int | None = None


class CoolifyEnvKey(BaseModel):
    """One environment variable of an application: its key and flags, never its value."""

    key: str
    is_buildtime: bool | None = None
    is_runtime: bool | None = None
    is_preview: bool | None = None
