"""The Coolify plugin: read an instance's applications, their deployments and env keys, and for
an approved plan redeploy an application or set one of its env vars.

Where Coolify answers and the token (`COOLIFY_URL`, `COOLIFY_TOKEN`) come from the environment
or the dashboard's secret file, never from the stored settings, and a new address set from the
dashboard clears the token: it goes to that address with every request.
`COOLIFY_URL` must be https unless its host is a loopback, private or CGNAT address or a
single-label Docker service name (`check_url`).

The read tools are offered on a host that runs Coolify applications (containers with Coolify's
application labels), and never show or read Bleep's own application (`_not_own`). What they
return was set in Coolify or by a commit, so it is redacted, cleaned and wrapped as untrusted like
every other tool output, and an env var's value is never read at all. Every Coolify failure comes
back as a sentence the model can go on from.

An action runs the Docker act tools' checks first (`tools/docker_act.py`): the step is the
approved plan's and the grant is for that plan, the target passes the plan check's target rules
(a Coolify application none of whose containers is protected or Bleep's own) and its params.
The application is named by its containers' labels as the registry sees them, so a plan cannot
reach an application this host does not run. The approval bound the target to the application
uuid it resolved to then; Coolify is called with that uuid, and only while the target still
names it, so an application deleted and recreated under the same name is not acted on (#139).
A redeploy waits until Coolify reports the deployment finished, failed or cancelled (at most
`deploy_timeout_s`), so Verify checks the service only after the new containers are up. A
set_env value goes to Coolify and nowhere else: not into a result, a log line or the guard log.
See docs/design/plugins.md.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from bleep.guard.grant import GrantInvalid, step_snapshot, verify_step
from bleep.guard.log import GuardLogger
from bleep.guard.pipeline import guard_text
from bleep.guard.plan_check import (
    NoCoolifyApp,
    check_params,
    check_shown,
    check_target,
    coolify_app_uuid,
)
from bleep.hosts.coolify_names import COOLIFY_NAME, name_matches
from bleep.hosts.discovery import is_self
from bleep.hosts.registry import Registry
from bleep.plugins import urls
from bleep.plugins.base import Plugin, PluginContext
from bleep.plugins.coolify.client import CoolifyClient, CoolifyError, CoolifyUnavailable
from bleep.schemas.coolify import CoolifyApplication, CoolifyDeployment, CoolifyEnvKey
from bleep.schemas.graph import Action, ApprovalGrant, ApprovalRecord, PlanStep, StepResult
from bleep.schemas.plugins import PluginHealth
from bleep.tools.common import UNTRUSTED
from bleep.tools.docker_act import StepRefused
from bleep.tools.registry import ActHandler, ToolContext, ToolSpec

log = logging.getLogger(__name__)

URL_ENV = "COOLIFY_URL"
TOKEN_ENV = "COOLIFY_TOKEN"
MESSAGE_CHARS = 200  # of one commit message in the deployments list
FIELD_CHARS = 200  # of any other one field in a list line
COMPOSE_DOMAINS_MAX = 16_000  # chars of docker_compose_domains Bleep parses at all
DOMAINS_CHARS = 500  # of one application's domains in the list
POLL_S = 10  # between reads of a running deployment
DONE = {"finished"}
ENDED = {"finished", "failed", "cancelled-by-user"}
_VERSION = re.compile(r"v?\d+(\.\d+){1,3}[\w.-]{0,20}")
_UUID_LABEL, _ID_LABEL = "coolify.applicationUuid", "coolify.applicationId"
_ITEM = re.compile(r"[^,]+")  # an item of a comma list; a space does not end it
# Coolify keeps the ref it deploys ("HEAD") in its commit fields too; only a hex sha (SHA-1 or
# SHA-256, or a short one) is a commit.
_SHA = re.compile(r"[0-9a-fA-F]{7,64}")
# What Coolify writes into the git fields of an application built without git: a Docker image,
# or a Dockerfile typed into Coolify.
PLACEHOLDER_REPO = "coollabsio/coolify"
_SERVICE = re.compile(r"[\w.-]{1,64}", re.ASCII)  # a Compose service name
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
# One domain as Bleep takes it: scheme://[userinfo@]host[:port][/path, ?query or #fragment],
# the host a DNS-style name or a bracketed IPv6 address, the rest printable ASCII without `,` or
# `;`. Nothing in it can end a line, start another item or name another service.
_URL = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<userinfo>[^\s/?#@,;]*@)?"
    rf"(?P<rest>(?:{_LABEL}(?:\.{_LABEL})*\.?|\[[0-9A-Fa-f:.]+\])"
    r"(?::[0-9]{1,5})?(?:[/?#][\x21-\x2b\x2d-\x3a\x3c-\x7e]*)?)"
)
APPLICATIONS = (
    "List the Coolify applications: name, uuid, status (running:healthy, exited:unhealthy, ...),"
    " domains, git repo and branch, deployed commit and build pack. The ones whose containers"
    " run on this host are marked. Use it to find the application behind a service and its uuid."
    + UNTRUSTED
)
DEPLOYMENTS = (
    "Show a Coolify application's latest deployments, newest first: when, status (queued,"
    " in_progress, finished, failed, cancelled-by-user), commit, what started it and the commit"
    " message. Use it when the incident could follow a deploy. `app` is the application's uuid"
    " or name from coolify_applications." + UNTRUSTED
)
ENV_KEYS = (
    "List the environment variable keys a Coolify application has, with whether each is used at"
    " build time, at run time or in previews. Values are never shown. Use it when a setting may be"
    " missing or misnamed. A coolify.set_env takes effect only at the next deployment, so a plan"
    " that sets one redeploys the application after it. `app` is the application's uuid or name"
    " from coolify_applications." + UNTRUSTED
)


class CoolifySettings(BaseModel):
    """The address and the token are in the env; only how long a redeploy may take is set."""

    model_config = ConfigDict(extra="forbid")

    deploy_timeout_s: int = Field(
        900, ge=60, le=3600, description="How long a redeploy may take before Bleep stops waiting"
    )


class CoolifyUrlRefused(urls.UrlRefused):
    """`COOLIFY_URL` is not one Bleep sends the token to; the text never quotes it."""


def check_url(raw: str) -> str:
    """`raw` as the client's base URL, or `CoolifyUrlRefused` (`plugins/urls.check_url`)."""
    hint = "; use Coolify's https domain"
    return urls.check_url(raw, URL_ENV, CoolifyUrlRefused, https_hint=hint).rstrip("/")


class AppArgs(BaseModel):
    app: str = Field(min_length=1, max_length=200, description="The application's uuid or name.")


class NoArgs(BaseModel):
    pass


def _host_apps(registry: Registry, host_id: str) -> set[str]:
    """The uuids, and ids for containers an older Coolify labelled, of the Coolify applications
    whose containers run on the host. The API never shows an id, so only a uuid marks one."""
    return {
        value
        for c in registry.snapshot(host_id) or []
        for value in (c.labels.get(_UUID_LABEL), c.labels.get(_ID_LABEL))
        if value
    }


def _not_own(apps: list[CoolifyApplication], registry: Registry) -> list[CoolifyApplication]:
    """`apps` without Bleep's own: one with a `bleep.self=true` container on any host, matched by
    the uuid on its labels or, for a container that carries none, by its resource name."""
    own = [c for h in registry.hosts() for c in registry.snapshot(h.config.id) or []]
    own = [c for c in own if is_self(c.labels)]
    uuids = {c.labels[_UUID_LABEL] for c in own if c.labels.get(_UUID_LABEL)}
    names = {c.labels[COOLIFY_NAME] for c in own if c.labels.get(COOLIFY_NAME)}
    return [a for a in apps if a.uuid not in uuids and not (a.name and name_matches(a.name, names))]


def _no_userinfo(text: str) -> str:
    """A repo URL or domain list without any `user:token@` in it: each item loses everything up to
    its last `@`, after the scheme if it has one. A path with an `@` loses more than it must."""

    def strip(match: re.Match[str]) -> str:
        item = match.group(0)
        if "@" not in item:
            return item
        scheme, sep, rest = item.partition("://")
        host = (rest if sep else item).rsplit("@", 1)[1]
        return f"{scheme}://{host}" if sep else host

    return _ITEM.sub(strip, text)


def _commit(sha: str | None) -> str | None:
    """The short commit `sha` names, or None when it is a ref or anything else."""
    return sha[:7] if sha and _SHA.fullmatch(sha) else None


def _built_from_git(a: CoolifyApplication) -> bool:
    if a.build_pack == "dockerimage":
        return False
    return bool(a.git_repository) and not (
        a.build_pack == "dockerfile" and a.git_repository == PLACEHOLDER_REPO
    )


def _one_line(text: str | None, limit: int = FIELD_CHARS) -> str:
    """`text` as it may stand in one line of a list: every run of whitespace, a line break of any
    kind among them (`\\n`, `\\r`, U+0085, U+2028, U+2029, VT, FF), is one space, and it is cut to
    `limit`. Empty for None. A field from Coolify with a line break would otherwise print a second
    line that reads as another application or deployment, and the cut keeps a line under the
    guard's 4,000-char line cap, which would split a longer one in two."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _source(a: CoolifyApplication) -> str:
    """Where `a` is built from, as ` repo=…@branch commit=…`; nothing for an application built
    without git, whose git fields hold Coolify's own repo, `main` and `HEAD`."""
    if not _built_from_git(a):
        return ""
    commit = _commit(a.git_commit_sha)
    repo = f" repo={_one_line(_no_userinfo(a.git_repository))}@{_one_line(a.git_branch) or '?'}"
    return repo + (f" commit={commit}" if commit else "")


def _trimmed(text: str) -> str:
    """`text` without the spaces and commas Coolify trims off a domain list's ends on save. A loop,
    since a regex anchored at the end is quadratic on a long run of spaces inside the text."""
    start, stop = 0, len(text)
    while start < stop and (text[start] == "," or text[start].isspace()):
        start += 1
    while stop > start and (text[stop - 1] == "," or text[stop - 1].isspace()):
        stop -= 1
    return text[start:stop]


def _url_list(text: str) -> str:
    """`text` as Coolify trims it and without the userinfo `_URL` finds before each host, when
    each comma item is a plain URL; an `@` in a path or query stays."""
    urls = [_URL.fullmatch(item.strip()) for item in _trimmed(text).split(",")]
    return ",".join(_without_userinfo(url) for url in urls)


def _without_userinfo(url: re.Match[str] | None) -> str:
    if url is None:
        raise ValueError("not a list of URLs")
    return url["scheme"] + url["rest"]


def _service_domains(name: str, service: object) -> str | None:
    """`name: domains` for one Compose service, None when it has no domain."""
    if not _SERVICE.fullmatch(name) or not isinstance(service, dict):
        raise ValueError("not a Compose service")
    domain = service.get("domain")
    if domain is not None and not isinstance(domain, str):
        raise ValueError("not a domain list")
    return f"{name}: {_url_list(domain)}" if domain else None


def _compose_domains(raw: str) -> str:
    """A Compose application's domains per service, `web: https://a,https://b; api: …`: `none`
    when no service has one, `?` when `raw` is not what Coolify writes there."""
    if len(raw) > COMPOSE_DOMAINS_MAX:
        return "?"
    try:
        services = json.loads(raw) if raw.strip() else None
        if services is None or services == {} or services == []:
            return "none"
        if not isinstance(services, dict):
            return "?"
        shown = [s for name, svc in services.items() if (s := _service_domains(name, svc))]
    except ValueError, RecursionError:
        return "?"
    return "; ".join(shown) or "none"


def _domains(a: CoolifyApplication) -> str:
    if a.fqdn:
        try:
            return _url_list(a.fqdn)
        except ValueError:
            return "?"
    if a.docker_compose_domains is not None:
        return _compose_domains(a.docker_compose_domains)
    return "none"


def _failed(error: CoolifyError) -> str:
    return f"Coolify could not be read: {error}."


def describe_applications(apps: list[CoolifyApplication], here: set[str]) -> str:
    if not apps:
        return "Coolify has no applications this token can see."
    lines = ["Coolify applications:"]
    for a in sorted(apps, key=lambda a: (a.uuid not in here, a.name or "")):
        mark = " (on this host)" if a.uuid in here else ""
        lines.append(
            f"- {_one_line(a.name) or '?'} uuid={_one_line(a.uuid)}{mark}"
            f" status={_one_line(a.status) or '?'}"
            f" domains={_one_line(_domains(a), DOMAINS_CHARS)}{_source(a)}"
            f" build={_one_line(a.build_pack) or '?'}"
        )
    return "\n".join(lines)


def _via(d: CoolifyDeployment) -> str:
    how = "webhook" if d.is_webhook else "api" if d.is_api else "by hand"
    if d.rollback:
        how += ", rollback"
    if d.restart_only:
        how += ", restart only"
    return how


def _first_line(text: str | None) -> str:
    """The first line of `text` that is not blank, a line ending at a line break of any kind."""
    return next(iter((text or "").strip().splitlines()), "")


def describe_deployments(app: CoolifyApplication, found: list[CoolifyDeployment]) -> str:
    name = _one_line(app.name) or "?"
    if not found:
        return f"{name} has no deployments."
    lines = [f"Latest deployments of {name} ({_one_line(app.uuid)}), newest first:"]
    for d in found:
        message = _one_line(_first_line(d.commit_message), MESSAGE_CHARS)
        commit = _commit(d.commit)
        commit = f" commit={commit}" if commit else ""
        lines.append(
            f"- {_one_line(d.created_at) or '?'} {_one_line(d.status) or '?'}{commit} ({_via(d)})"
            f" finished={_one_line(d.finished_at) or 'not yet'}: {message or 'no message'}"
        )
    return "\n".join(lines)


def describe_env_keys(app: CoolifyApplication, keys: list[CoolifyEnvKey]) -> str:
    name = _one_line(app.name) or "?"
    if not keys:
        return f"{name} has no environment variables."
    lines = [f"Environment variables of {name} (keys only; values are never read):"]
    for k in sorted(keys, key=lambda k: (bool(k.is_preview), k.key)):
        used = [w for w, on in (("build", k.is_buildtime), ("runtime", k.is_runtime)) if on]
        if k.is_preview:
            used.append("preview")
        lines.append(f"- {_one_line(k.key)}" + (f" ({', '.join(used)})" if used else ""))
    return "\n".join(lines)


def _pick(apps: list[CoolifyApplication], ref: str) -> CoolifyApplication | str:
    """The application `ref` names by uuid, else by name; a sentence when none or several do."""
    by_uuid = [a for a in apps if a.uuid == ref]
    if by_uuid:
        return by_uuid[0]
    named = [a for a in apps if a.name is not None and a.name == ref]
    if len(named) == 1:
        return named[0]
    if named:
        return f"{len(named)} Coolify applications are named {ref}; pass the uuid instead."
    return f"Coolify has no application {ref}; coolify_applications lists them."


async def _guard(ctx: ToolContext, text: str) -> str:
    """Redact, clean and wrap `text` off the event loop, as every tool's output is."""
    wrapped, redactions = await asyncio.to_thread(guard_text, "coolify", text)
    if redactions and ctx.guard_log is not None:
        ctx.guard_log.record(
            ctx.incident_id, "redacted", f"{redactions} secret(s) redacted from coolify output"
        )
    return wrapped


def _tool(
    name: str, description: str, schema: type[BaseModel], run: Callable[..., Awaitable[str]]
) -> StructuredTool:
    async def call(**args) -> str:
        try:
            return await run(**args)
        except CoolifyError as error:
            return _failed(error)

    return StructuredTool(
        name=name,
        description=description,
        args_schema=schema,
        coroutine=call,
        handle_validation_error=True,
    )


def read_specs(client: CoolifyClient) -> list[ToolSpec]:
    def applications_tool(ctx: ToolContext) -> BaseTool:
        async def run() -> str:
            apps = _not_own(await client.applications(), ctx.registry)
            text = describe_applications(apps, _host_apps(ctx.registry, ctx.host_id))
            return await _guard(ctx, text)

        return _tool("coolify_applications", APPLICATIONS, NoArgs, run)

    def per_app(
        name: str, description: str, read: Callable[[CoolifyApplication], Awaitable[str]]
    ) -> Callable[[ToolContext], BaseTool]:
        def build(ctx: ToolContext) -> BaseTool:
            async def run(app: str) -> str:
                picked = _pick(_not_own(await client.applications(), ctx.registry), app.strip())
                if isinstance(picked, str):
                    return await _guard(ctx, picked)
                return await _guard(ctx, await read(picked))

            return _tool(name, description, AppArgs, run)

        return build

    async def deployments(app: CoolifyApplication) -> str:
        return describe_deployments(app, await client.deployments(app.uuid))

    async def env_keys(app: CoolifyApplication) -> str:
        return describe_env_keys(app, await client.env_keys(app.uuid))

    def offered(ctx: ToolContext) -> bool:
        return bool(_host_apps(ctx.registry, ctx.host_id))

    builds = {
        "coolify_applications": applications_tool,
        "coolify_deployments": per_app("coolify_deployments", DEPLOYMENTS, deployments),
        "coolify_env_keys": per_app("coolify_env_keys", ENV_KEYS, env_keys),
    }
    return [ToolSpec(name, "read", "coolify", build, offered) for name, build in builds.items()]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CoolifyAction:
    """One Coolify action, run only for a step of an approved plan (see the module docstring)."""

    name: Action
    client: CoolifyClient
    secret: SecretStr | str = field(repr=False)  # BLEEP_SECRET, the grants' key
    deploy_timeout_s: int = 900
    guard_log: GuardLogger | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    clock: Callable[[], datetime] = _utcnow

    def check(
        self,
        step: PlanStep,
        grant: ApprovalGrant | None,
        registry: Registry,
        *,
        plan: ApprovalRecord,
        step_index: int,
    ) -> str:
        """Every check `run` makes before it calls Coolify: the uuid the approval bound the
        application to, while the target still names it. Raises `GrantInvalid` or
        `StepRefused`, recorded as `blocked_action`."""
        what = f"step {step_index + 1} ({step.action} {step.target} on {step.host_id})"
        try:
            if step.action != self.name:
                raise StepRefused(f"{what}: {self.name} cannot run {step.action}")
            verify_step(step, grant, plan, step_index, self.secret)
            number = step_index + 1
            violations = (
                check_params(number, step)
                or check_target(number, step, registry)
                or check_shown(number, step)
            )
            if violations:
                raise StepRefused("; ".join(v.text for v in violations))
            try:
                current = coolify_app_uuid(registry, step)
            except NoCoolifyApp:
                raise StepRefused(
                    f"{what}: names no single Coolify application on this host"
                ) from None
            snapshot = step_snapshot(plan.snapshots, step)
            if snapshot is None or snapshot.app_uuid is None:
                raise StepRefused(
                    f"{what}: the approved plan holds no snapshot of this application"
                )
            if current != snapshot.app_uuid:
                raise StepRefused(
                    f"{what}: names the Coolify application {current} now, not {snapshot.app_uuid}"
                    " as approved; it was recreated or replaced since the approval"
                )
            return snapshot.app_uuid
        except GrantInvalid as refusal:
            self._blocked(plan, f"{what}: refused, {refusal}")
            raise
        except StepRefused as refusal:
            self._blocked(plan, str(refusal))
            raise

    async def run(
        self,
        step: PlanStep,
        grant: ApprovalGrant | None,
        registry: Registry,
        *,
        plan: ApprovalRecord,
        step_index: int,
    ) -> StepResult:
        """Run `step` under `grant`. A Coolify failure is a result with `ok=False`."""
        uuid = self.check(step, grant, registry, plan=plan, step_index=step_index)
        started = self.clock()
        try:
            if self.name == "coolify.redeploy":
                ok, detail = await self._redeploy(uuid, step.target)
            else:
                key = step.params["key"]
                how = await self.client.set_env(uuid, key, SecretStr(step.params["value"]))
                ok, detail = True, f"{how} {key} on {step.target}; it applies at the next deploy"
        except CoolifyError as error:
            log.warning("%s failed on %s: %s", self.name, step.host_id, type(error).__name__)
            ok, detail = False, f"Coolify: {error}"
        return StepResult(
            step_index=step_index,
            action=self.name,
            target=step.target,
            ok=ok,
            detail=detail,
            started_at=started,
            finished_at=self.clock(),
        )

    async def _redeploy(self, uuid: str, target: str) -> tuple[bool, str]:
        """Queue a deployment and wait until it ends, for at most `deploy_timeout_s` of wall time
        (by `clock`, so slow answers count too); whether it finished, and how it went."""
        try:
            deployment = await self.client.deploy(uuid)
        except CoolifyUnavailable as error:  # the request may have reached Coolify all the same
            return False, f"Coolify: {error}; a deployment of {target} may already be queued"
        deadline = self.clock() + timedelta(seconds=self.deploy_timeout_s)
        while self.clock() < deadline:
            await self.sleep(POLL_S)
            try:
                status = (await self.client.deployment(deployment)).status
            except CoolifyUnavailable:  # timeouts, refused connections, 429 and 5xx: ask again
                continue
            except CoolifyError as error:
                return False, (
                    f"Coolify: {error}; deployment {deployment} of {target} was queued and may"
                    " still run, Bleep stopped following it"
                )
            if status in ENDED:
                ok = status in DONE
                return ok, f"redeploy of {target} {status} (deployment {deployment})"
        return False, (
            f"redeploy of {target} had not finished after {self.deploy_timeout_s} s "
            f"(deployment {deployment}); Bleep stopped waiting, it may still run in Coolify"
        )

    def _blocked(self, plan: ApprovalRecord, text: str) -> None:
        if self.guard_log is not None:
            self.guard_log.record(plan.incident_id, "blocked_action", text)


class CoolifyPlugin(Plugin):
    """`transport`, `sleep` and `clock` are for tests: a mock transport, and a sleep that does not
    wait with the clock that measures a redeploy's deadline."""

    name = "coolify"
    title = "Coolify"
    settings_model = CoolifySettings
    secrets = (URL_ENV, TOKEN_ENV)
    connection = (URL_ENV, TOKEN_ENV)

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._transport = transport
        self._sleep = sleep
        self._clock = clock

    def client(self, ctx: PluginContext) -> CoolifyClient:
        """Raises `CoolifyUrlRefused` for a `COOLIFY_URL` Bleep must not send the token to; the
        registry then refuses to switch the plugin on, naming that type only."""
        try:
            url = check_url(ctx.secrets[URL_ENV].get_secret_value())
        except CoolifyUrlRefused as refusal:
            log.warning("coolify: %s", refusal)
            raise
        transport = urls.guarded(self._transport, ctx.public_only)
        return CoolifyClient(url, ctx.secrets[TOKEN_ENV], transport=transport)

    def tools(self, ctx: PluginContext) -> list[ToolSpec]:
        return read_specs(self.client(ctx))

    def act_handlers(self, ctx: PluginContext) -> dict[str, ActHandler]:
        timeout = CoolifySettings.model_validate(ctx.settings.model_dump()).deploy_timeout_s
        return {
            action: CoolifyAction(
                name=action,
                client=self.client(ctx),
                secret=ctx.grant_secret,
                deploy_timeout_s=timeout,
                guard_log=ctx.guard_log,
                sleep=self._sleep,
                clock=self._clock,
            )
            for action in ("coolify.redeploy", "coolify.set_env")
        }

    async def health(self, ctx: PluginContext) -> PluginHealth:
        """Coolify's version, read with the token; the error's own words when that fails."""
        try:
            version = await self.client(ctx).version()
        except CoolifyError as error:
            return PluginHealth(ok=False, detail=str(error))
        name = f"Coolify {version}" if _VERSION.fullmatch(version) else "Coolify"
        return PluginHealth(ok=True, detail=f"{name} answers with the token")
