"""The settings a person may change: model choice, budgets, thresholds, the time zone, the
language, hosts' and services' rules, URLs and protected containers. Never a secret, in either
direction.

The body is read as plain JSON and checked for secrets before it is validated, so a refusal names
where the secret was without sending its value back, as a validation error would.

A read carries the overrides' `version` (in the body and as the `ETag`), and a change must send it
back in `If-Match`: 428 without it, 409 with the current version when the overrides have changed
since, so a stale page or a second writer never silently reverts a change.
"""

import json
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Body, Depends, Header, Response
from pydantic import ValidationError

from bleep.api.deps import get_parts
from bleep.api.deps import get_settings as app_settings
from bleep.api.errors import Refusal, problems_of
from bleep.config import Settings
from bleep.hosts.coolify_names import coolify_slug
from bleep.incidents.settings_store import SettingsRefused, SettingsStale, check_body
from bleep.plugins.urls import NotPublic, UrlRefused, check_dashboard_url, check_public
from bleep.schemas.settings import (
    ENV_NAMES,
    MODEL_KEY_ENV,
    TRACING_ENDPOINTS,
    TRACING_URLS,
    SecretStatus,
    SettingsUpdate,
    SettingsView,
    TracingChoice,
    url_origin,
)
from bleep.wiring import AppParts

router = APIRouter(tags=["settings"])

Parts = Annotated[AppParts, Depends(get_parts)]

# A settings, plugin or prompt body is a few thousand characters (a prompt override at most
# 20,000); the cap keeps the secret check, which reads the whole body, a small cost.
MAX_BODY_CHARS = 100_000
# The deepest body the dashboard sends is about six levels (settings > services > key > urls >
# item). A fixed limit keeps the answer the same on every platform: `json.dumps` alone raises
# RecursionError at a depth set by the thread's stack size, which differs between macOS and Linux.
MAX_BODY_DEPTH = 32


def _too_deep(body: Any) -> bool:
    pending: list[tuple[Any, int]] = [(body, 1)]
    while pending:
        value, depth = pending.pop()
        if not isinstance(value, dict | list):
            continue
        if depth > MAX_BODY_DEPTH:
            return True
        children = value.values() if isinstance(value, dict) else value
        pending.extend((child, depth + 1) for child in children)
    return False


def settings_view(parts: AppParts) -> SettingsView:
    store = parts.settings_store
    return SettingsView(
        models=store.model_choice(),
        model_key=SecretStatus(
            env=MODEL_KEY_ENV,
            set=store.models().openrouter_api_key is not None,
            source=parts.secrets.state(MODEL_KEY_ENV).source,
        ),
        budgets=store.budgets(),
        thresholds=store.thresholds(),
        hosts=store.host_views(),
        plugins=store.plugin_toggles(),
        timezone=store.timezone(),
        language=store.language(),
        embeddings=store.embeddings(),
        tracing=shown_tracing(store.tracing(), store.from_env()),
        from_env=store.from_env(),
        restart_needed=store.restart_needed(),
        overrides=store.overrides().model_dump(mode="json", exclude_none=True),
        version=store.version(),
    )


def shown_tracing(tracing: TracingChoice, from_env: list[str]) -> TracingChoice:
    """The tracing in force with an address the env decides cut to its origin, as
    `api/secrets.py` shows an address: its path or query can hold a credential, and the env's
    address was never the API's to show."""
    cut = {
        field: url_origin(value)
        for field in TRACING_URLS
        if f"tracing.{field}" in from_env and (value := getattr(tracing, field))
    }
    return tracing.model_copy(update=cut)


@router.get("/settings")
async def get_settings(parts: Parts, response: Response) -> SettingsView:
    view = settings_view(parts)
    response.headers["ETag"] = f'"{view.version}"'
    return view


@router.put("/settings", response_model=SettingsView)
async def put_settings(
    body: Annotated[Any, Body()],
    parts: Parts,
    settings: Annotated[Settings, Depends(app_settings)],
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> SettingsView:
    """Each section given replaces that section's overrides; see `SettingsUpdate`. `If-Match`
    holds the version the body was built from. `budgets` are a 403 while the dev panel, which
    edits them, is off; the models, embeddings and tracing are the settings page's (#372)."""
    if if_match is None or not if_match.strip():
        raise Refusal(428, "need_version_settings")
    update = parse_body(body, SettingsUpdate)
    refuse_names_without_a_slug(update)
    if not settings.dev_panel and update.budgets is not None:
        raise Refusal(403, "budgets_dev_panel")
    await check_tracing_addresses(update, parts)
    try:
        await parts.settings_store.update(update, version=version_of(if_match))
    except SettingsStale as changed:
        raise stale(changed.current, "stale_settings") from None
    except SettingsRefused as refusal:
        raise Refusal.of(422, refusal) from None
    view = settings_view(parts)
    response.headers["ETag"] = f'"{view.version}"'
    return view


async def check_tracing_addresses(update: SettingsUpdate, parts: AppParts) -> None:
    """422 for a tracer's address that changes to one the dashboard may not set: like a
    plugin's, it must be https and resolve to public addresses only, since the tracer's keys are
    sent there; a private or plain http one is for `.env`. Only the two model servers' addresses
    may be private (`api/secrets.py`)."""
    if update.tracing is None:
        return
    in_force, from_env = parts.settings_store.tracing(), parts.settings_store.from_env()
    for field in TRACING_ENDPOINTS:
        url = getattr(update.tracing, field)
        if url is None or url == getattr(in_force, field) or f"tracing.{field}" in from_env:
            continue  # the store refuses a field the env decides, saying so
        name = ENV_NAMES["tracing"][field]
        try:
            check_dashboard_url(url, name)
            host = httpx.URL(url)
            await check_public(host.host, host.port or 443, parts.plugins.resolver)
        except UrlRefused as refused:
            raise Refusal.of(422, refused) from None
        except NotPublic as refusal:
            raise Refusal(
                422,
                "address_public_only",
                fallback=f"{name} set on the dashboard must reach public addresses only, and "
                f"{refusal}; an address on a private network can be set in .env",
                name=name,
                reason=refusal,
            ) from None


def refuse_names_without_a_slug(update: SettingsUpdate) -> None:
    """422 for a protected name with no letter or digit (`🚀`): Coolify's slug of it is empty,
    so it would be stored, shown as protected and match no application. A Docker container name
    always starts with a letter or digit, so no real container is refused."""
    for host_id, host in (update.hosts or {}).items():
        for service, overrides in host.services.items():
            for name in overrides.protected_containers or []:
                if not coolify_slug(name):
                    raise Refusal(
                        422,
                        "protected_no_slug",
                        fallback=f"host {host_id}, service {service}: protected entry {name!r} "
                        "has no letters or digits for Coolify's slug, so it would protect nothing "
                        "by a Coolify name; protect the application by its uuid",
                        host=host_id,
                        service=service,
                        entry=repr(name),
                    )


def version_of(if_match: str) -> str:
    """The version in an `If-Match` header: an ETag, quoted, or the bare version."""
    return if_match.strip().removeprefix("W/").strip('"')


def stale(current: str, template: str) -> Refusal:
    """409: nothing was saved; the body and the `ETag` carry the version now, `current`."""
    return Refusal(409, template, extra={"version": current}, headers={"ETag": f'"{current}"'})


def check_size(body: Any) -> None:
    """413 for a body over `MAX_BODY_CHARS` as JSON, before the secret check reads all of it; 422
    for one nested deeper than `MAX_BODY_DEPTH`. `BodyLimitMiddleware` caps the raw bytes
    earlier."""
    if _too_deep(body):
        raise Refusal(422, "body_too_deep")
    try:
        size = len(json.dumps(body, ensure_ascii=False))
    except RecursionError:
        raise Refusal(422, "body_too_deep") from None
    except TypeError:  # not JSON (a form or text/plain body arrives as bytes)
        raise Refusal(422, "body_not_json") from None
    if size > MAX_BODY_CHARS:
        raise Refusal(
            413,
            "body_too_large_chars",
            fallback=f"the body is over {MAX_BODY_CHARS:,} characters",
            limit=MAX_BODY_CHARS,
        )


def parse_body[T](body: Any, model: type[T]) -> T:
    """`body` as `model`, or 413 when it is too large, or 422: for a secret anywhere in it, and
    for an invalid body with the reasons but never the values sent."""
    check_size(body)
    try:
        check_body(body)
        return model.model_validate(body)  # type: ignore[attr-defined]
    except SettingsRefused as refusal:
        raise Refusal.of(422, refusal) from None
    except ValidationError as error:
        raise Refusal.invalid(problems_of(error)) from None
