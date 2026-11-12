"""What Bleep reads from a Coolify instance: the few fields its tools show, nothing else.

Coolify's answers carry far more (webhook secrets, compose files, build logs, env values); these
models ignore every field they do not name, so those never leave the client. Every field but the
uuid or key may be null, so one odd record reads as unknowns. Text fields are set in Coolify or
by a commit, so they are untrusted wherever they go next.
"""

import json
import re
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field

# What Bleep accepts as a Coolify uuid; it goes into request paths. Coolify's are 24 [a-z0-9].
UUID = re.compile(r"[A-Za-z0-9-]{1,64}")
_UUID_FIELD = rf"^{UUID.pattern}$"


def _as_json(value: object) -> object:
    """A JSON field Coolify may one day send decoded, written back as the text it sends now, so
    an odd value reads as unparsable instead of losing the whole application."""
    return value if value is None or isinstance(value, str) else json.dumps(value)


def _flag(value: object) -> object:
    """A flag as Coolify sends it (a bool, or 0/1 from an older database), else None."""
    if isinstance(value, bool):
        return value
    return bool(value) if value in (0, 1) else None


def _text(value: object) -> object:
    return value if isinstance(value, str) else None


class CoolifyApplication(BaseModel):
    uuid: str = Field(pattern=_UUID_FIELD)  # the API never shows an application's numeric id
    name: str | None = None
    status: str | None = None  # "running:healthy", "exited:unhealthy", ...
    fqdn: str | None = None  # the application's domains, comma separated
    # A Compose application's domains instead of fqdn, per service, as a JSON string:
    # {"web": {"domain": "https://a.example.com,https://www.a.example.com", ...}, ...}
    docker_compose_domains: Annotated[str | None, BeforeValidator(_as_json)] = None
    git_repository: str | None = None
    git_branch: str | None = None
    git_commit_sha: str | None = None
    build_pack: str | None = None
    last_online_at: str | None = None
    # The path Coolify's own health check asks the app for, and whether it asks at all (#348).
    # Not booleans in every answer, so an odd value reads as unknown instead of losing the app.
    health_check_enabled: Annotated[bool | None, BeforeValidator(_flag)] = None
    health_check_path: Annotated[str | None, BeforeValidator(_text)] = None


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
