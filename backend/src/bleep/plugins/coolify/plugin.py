"""The Coolify plugin: read an instance's applications, their deployments and env keys.

Where Coolify answers and the token come from the environment (`COOLIFY_URL`, `COOLIFY_TOKEN`),
never from a setting a person can change: the token goes to that address with every request.
`COOLIFY_URL` must be https unless its host is a loopback, private or CGNAT address or a
single-label Docker service name (`check_url`).

The read tools are offered on a host that runs Coolify applications (containers with Coolify's
application labels). What they return was set in Coolify or by a commit, so it is redacted,
cleaned and wrapped as untrusted like every other tool output, and an env var's value is never
read at all. Every Coolify failure comes back as a sentence the model can go on from. The plugin
has no actions. See docs/design/plugins.md.
"""

import asyncio
import ipaddress
import logging
import re
import socket
from collections.abc import Awaitable, Callable

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from bleep.guard.pipeline import guard_text
from bleep.hosts.registry import Registry
from bleep.plugins.base import Plugin, PluginContext
from bleep.plugins.coolify.client import CoolifyClient, CoolifyError
from bleep.schemas.coolify import CoolifyApplication, CoolifyDeployment, CoolifyEnvKey
from bleep.schemas.plugins import PluginHealth
from bleep.tools.docker_read import UNTRUSTED
from bleep.tools.registry import ToolContext, ToolSpec

log = logging.getLogger(__name__)

URL_ENV = "COOLIFY_URL"
TOKEN_ENV = "COOLIFY_TOKEN"
MESSAGE_CHARS = 200  # of one commit message in the deployments list
_VERSION = re.compile(r"v?\d+(\.\d+){1,3}[\w.-]{0,20}")
_UUID_LABEL, _ID_LABEL = "coolify.applicationUuid", "coolify.applicationId"
# The addresses Bleep sends the token to over plain http: loopback, private and CGNAT (Tailscale).
NEARBY = tuple(
    ipaddress.ip_network(net)
    for net in (
        "127.0.0.0/8",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "fc00::/7",
    )
)
# A single-label name, such as a Docker service; one starting with a digit could be an address.
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}")
_ITEM = re.compile(r"[^,]+")  # an item of a comma list; a space does not end it
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
    " missing or misnamed. `app` is the application's uuid or name from coolify_applications."
    + UNTRUSTED
)


class CoolifySettings(BaseModel):
    """Nothing to set yet: the address and the token are in the env."""

    model_config = ConfigDict(extra="forbid")


class CoolifyUrlRefused(ValueError):
    """`COOLIFY_URL` is not one Bleep sends the token to; the text never quotes it."""


def _nearby(host: str) -> bool:
    """A host whose traffic stays on this machine or its network: an address in `NEARBY`, or a
    single-label name such as a Docker service. No DNS lookup is made: where a name resolves
    (Docker's DNS, else the host's search domains) is the operator's to set up."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return bool(_NAME.fullmatch(host)) and not _inet_aton(host)
    return any(ip in net for net in NEARBY if net.version == ip.version)


def _inet_aton(host: str) -> bool:
    """Whether the C library would read `host` as an IPv4 address (`134744072`, `0x8080808`)."""
    try:
        socket.inet_aton(host)
    except OSError:
        return False
    return True


def check_url(raw: str) -> str:
    """`raw` as the client's base URL, or `CoolifyUrlRefused`: http(s) with a host and no user or
    password, and https unless the host is `_nearby`."""
    try:
        url = httpx.URL(raw.strip())
    except httpx.InvalidURL:
        raise CoolifyUrlRefused(f"{URL_ENV} is not a URL") from None
    if url.scheme not in ("http", "https") or not url.host or url.userinfo:
        raise CoolifyUrlRefused(f"{URL_ENV} must be an http(s) URL with a host and no password")
    if url.query or url.fragment or "?" in raw or "#" in raw:
        raise CoolifyUrlRefused(f"{URL_ENV} must have no query or fragment")
    if url.scheme == "http" and not _nearby(url.host):
        raise CoolifyUrlRefused(
            f"{URL_ENV} must use https unless it is a private address or a Docker service name;"
            " use Coolify's https domain, since the token travels with every request"
        )
    return str(url).rstrip("/")


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


def _failed(error: CoolifyError) -> str:
    return f"Coolify could not be read: {error}."


def describe_applications(apps: list[CoolifyApplication], here: set[str]) -> str:
    if not apps:
        return "Coolify has no applications this token can see."
    lines = ["Coolify applications:"]
    for a in sorted(apps, key=lambda a: (a.uuid not in here, a.name or "")):
        repo = a.git_repository and _no_userinfo(a.git_repository)
        repo = f" repo={repo}@{a.git_branch or '?'}" if repo else ""
        commit = f" commit={a.git_commit_sha[:7]}" if a.git_commit_sha else ""
        mark = " (on this host)" if a.uuid in here else ""
        domains = _no_userinfo(a.fqdn) if a.fqdn else "none"
        lines.append(
            f"- {a.name or '?'} uuid={a.uuid}{mark} status={a.status or '?'} "
            f"domains={domains}{repo}{commit} build={a.build_pack or '?'}"
        )
    return "\n".join(lines)


def _via(d: CoolifyDeployment) -> str:
    how = "webhook" if d.is_webhook else "api" if d.is_api else "by hand"
    if d.rollback:
        how += ", rollback"
    if d.restart_only:
        how += ", restart only"
    return how


def describe_deployments(app: CoolifyApplication, found: list[CoolifyDeployment]) -> str:
    if not found:
        return f"{app.name} has no deployments."
    lines = [f"Latest deployments of {app.name} ({app.uuid}), newest first:"]
    for d in found:
        message = (d.commit_message or "").strip().split("\n", 1)[0][:MESSAGE_CHARS]
        commit = (d.commit or "?")[:7]
        lines.append(
            f"- {d.created_at or '?'} {d.status or '?'} commit={commit} ({_via(d)}) "
            f"finished={d.finished_at or 'not yet'}: {message or 'no message'}"
        )
    return "\n".join(lines)


def describe_env_keys(app: CoolifyApplication, keys: list[CoolifyEnvKey]) -> str:
    if not keys:
        return f"{app.name} has no environment variables."
    lines = [f"Environment variables of {app.name} (keys only; values are never read):"]
    for k in sorted(keys, key=lambda k: (bool(k.is_preview), k.key)):
        used = [w for w, on in (("build", k.is_buildtime), ("runtime", k.is_runtime)) if on]
        if k.is_preview:
            used.append("preview")
        lines.append(f"- {k.key}" + (f" ({', '.join(used)})" if used else ""))
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
            text = describe_applications(
                await client.applications(), _host_apps(ctx.registry, ctx.host_id)
            )
            return await _guard(ctx, text)

        return _tool("coolify_applications", APPLICATIONS, NoArgs, run)

    def per_app(
        name: str, description: str, read: Callable[[CoolifyApplication], Awaitable[str]]
    ) -> Callable[[ToolContext], BaseTool]:
        def build(ctx: ToolContext) -> BaseTool:
            async def run(app: str) -> str:
                picked = _pick(await client.applications(), app.strip())
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


class CoolifyPlugin(Plugin):
    """`transport` is for tests: a mock transport in place of the network."""

    name = "coolify"
    title = "Coolify"
    settings_model = CoolifySettings
    secrets = (URL_ENV, TOKEN_ENV)

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def client(self, ctx: PluginContext) -> CoolifyClient:
        """Raises `CoolifyUrlRefused` for a `COOLIFY_URL` Bleep must not send the token to; the
        registry then refuses to switch the plugin on, naming that type only."""
        try:
            url = check_url(ctx.secrets[URL_ENV].get_secret_value())
        except CoolifyUrlRefused as refusal:
            log.warning("coolify: %s", refusal)
            raise
        return CoolifyClient(url, ctx.secrets[TOKEN_ENV], transport=self._transport)

    def tools(self, ctx: PluginContext) -> list[ToolSpec]:
        return read_specs(self.client(ctx))

    async def health(self, ctx: PluginContext) -> PluginHealth:
        """Coolify's version, read with the token; the error's own words when that fails."""
        try:
            version = await self.client(ctx).version()
        except CoolifyError as error:
            return PluginHealth(ok=False, detail=str(error))
        name = f"Coolify {version}" if _VERSION.fullmatch(version) else "Coolify"
        return PluginHealth(ok=True, detail=f"{name} answers with the token")
