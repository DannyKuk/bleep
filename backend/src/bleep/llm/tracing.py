"""Tracing of incident runs and chat readings: off, a self-hosted Langfuse, or LangSmith. See
docs/design/observability.md.

The tracer's callbacks go into the config of each graph run, not onto the chat models, so one
trace holds the whole run: every node, tool call and model call under it. The keys are read from
the environment or the dashboard's secret file and stay in the tracing client; nothing here puts
them in the run's metadata. A chat reading (`talk`) is a trace of its own, in the session of the
surface it came from, `talk-panel` or `talk-telegram` (#411).
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import Client
from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from bleep.config import SettingsError
from bleep.schemas.incidents import Incident
from bleep.schemas.settings import TracingChoice
from bleep.schemas.talk import TalkSurface

log = logging.getLogger(__name__)


def _env(name: str) -> AliasChoices:
    return AliasChoices(name, name.lower())


class TracingSettings(BaseSettings):
    """`TRACING` and the backend's own env names (no `BLEEP_` prefix), as in `.env.example`."""

    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore")

    tracing: Literal["off", "langfuse", "langsmith"] = Field(
        "off", validation_alias=_env("TRACING")
    )
    langfuse_public_key: SecretStr | None = Field(
        None, validation_alias=_env("LANGFUSE_PUBLIC_KEY")
    )
    langfuse_secret_key: SecretStr | None = Field(
        None, validation_alias=_env("LANGFUSE_SECRET_KEY")
    )
    # Where the backend sends traces; inside Compose that is the web container's own name.
    langfuse_base_url: str = Field(
        "http://langfuse-web:3000", validation_alias=_env("LANGFUSE_BASE_URL")
    )
    # Where a browser opens them: the link stored on the incident uses this one.
    langfuse_ui_url: str = Field("http://localhost:3001", validation_alias=_env("LANGFUSE_UI_URL"))
    # Langfuse v4 opens a trace only under its project; the profile creates the project `bleep`.
    langfuse_project_id: str | None = Field(None, validation_alias=_env("LANGFUSE_PROJECT_ID"))
    langsmith_api_key: SecretStr | None = Field(None, validation_alias=_env("LANGSMITH_API_KEY"))
    langsmith_endpoint: str | None = Field(None, validation_alias=_env("LANGSMITH_ENDPOINT"))
    langsmith_project: str = Field("bleep", validation_alias=_env("LANGSMITH_PROJECT"))


def trace_name(incident: Incident) -> str:
    return f"incident:{incident.title}"


def talk_session(surface: TalkSurface) -> str:
    """The session a chat reading is traced in: one per surface, each reading its own trace."""
    return f"talk-{surface}"


def talk_tags(surface: TalkSurface) -> list[str]:
    return ["talk", f"surface:{surface}"]


def trace_tags(incident: Incident) -> list[str]:
    subject = incident.subject
    tags = [f"host:{subject.host_id}"]
    if subject.service_id:
        tags.append(f"service:{subject.service_id}")
    return tags


class NoTracing:
    """Tracing off: nothing is added to a run."""

    def run_config(self, incident: Incident) -> RunnableConfig:
        return {}

    def talk_config(self, surface: TalkSurface) -> RunnableConfig:
        return {}

    def trace_url(self, incident: Incident) -> str | None:
        return None

    async def close(self) -> None:
        return None


class LangfuseTracing:
    """One Langfuse trace per incident, with the incident id as its session.

    A run that waits for a human ends its graph invocation at Approval and is resumed later,
    perhaps by another process. Every invocation is pinned to the trace whose id is derived from
    the incident id, so the start and every resume land in the same trace, and its link is known
    before the first span is sent.
    """

    def __init__(self, settings: TracingSettings) -> None:
        from langfuse import Langfuse  # imported only when tracing to Langfuse

        assert settings.langfuse_public_key and settings.langfuse_secret_key
        self._public_key = settings.langfuse_public_key.get_secret_value()
        self._client = Langfuse(
            public_key=self._public_key,
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            base_url=settings.langfuse_base_url,
        )
        self._ui_url = settings.langfuse_ui_url.rstrip("/")
        self._project_id = settings.langfuse_project_id

    def trace_id(self, incident: Incident) -> str:
        return self._client.create_trace_id(seed=incident.id)

    def run_config(self, incident: Incident) -> RunnableConfig:
        from langfuse.langchain import CallbackHandler

        handler = CallbackHandler(
            public_key=self._public_key, trace_context={"trace_id": self.trace_id(incident)}
        )
        return {
            "callbacks": [handler],
            "run_name": trace_name(incident),
            "tags": trace_tags(incident),
            "metadata": {
                "langfuse_session_id": incident.id,
                "langfuse_trace_name": trace_name(incident),
            },
        }

    def talk_config(self, surface: TalkSurface) -> RunnableConfig:
        from langfuse.langchain import CallbackHandler

        session = talk_session(surface)
        return {
            "callbacks": [CallbackHandler(public_key=self._public_key)],
            "run_name": session,
            "tags": talk_tags(surface),
            "metadata": {"langfuse_session_id": session, "langfuse_trace_name": session},
        }

    def trace_url(self, incident: Incident) -> str | None:
        # Langfuse's project-free /trace/<id> page looks the id up in the legacy traces table,
        # which v4 no longer writes, so the link names the project; without its id, no link.
        if self._project_id is None:
            return None
        return f"{self._ui_url}/project/{self._project_id}/traces/{self.trace_id(incident)}"

    async def close(self) -> None:
        await asyncio.to_thread(self._client.shutdown)


class LangSmithTracing:
    """LangSmith gives every graph invocation its own trace and groups them into a thread by
    the `session_id` metadata, the incident id. Its links need the workspace id from its API,
    so none is stored."""

    def __init__(self, settings: TracingSettings) -> None:
        assert settings.langsmith_api_key
        self._client = Client(
            api_key=settings.langsmith_api_key.get_secret_value(),
            api_url=settings.langsmith_endpoint,
        )
        self._project = settings.langsmith_project

    def run_config(self, incident: Incident) -> RunnableConfig:
        tracer = LangChainTracer(project_name=self._project, client=self._client)
        return {
            "callbacks": [tracer],
            "run_name": trace_name(incident),
            "tags": trace_tags(incident),
            "metadata": {"session_id": incident.id},
        }

    def talk_config(self, surface: TalkSurface) -> RunnableConfig:
        tracer = LangChainTracer(project_name=self._project, client=self._client)
        session = talk_session(surface)
        return {
            "callbacks": [tracer],
            "run_name": session,
            "tags": talk_tags(surface),
            "metadata": {"session_id": session},
        }

    def trace_url(self, incident: Incident) -> str | None:
        return None

    async def close(self) -> None:
        await asyncio.to_thread(self._client.flush)


# The keys a tracer needs, by `TRACING`: they can be set from the dashboard, never cleared there
# while the tracer that needs them is on.
TRACING_KEYS: dict[str, tuple[str, ...]] = {
    "langfuse": ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"),
    "langsmith": ("LANGSMITH_API_KEY",),
}


AnyTracer = NoTracing | LangfuseTracing | LangSmithTracing


def _langfuse_view(settings: TracingSettings) -> tuple[str | None, ...]:
    """What a Langfuse client is built from, to tell whether it changed."""
    keys = (settings.langfuse_public_key, settings.langfuse_secret_key)
    return (
        settings.tracing,
        *(key.get_secret_value() if key is not None else None for key in keys),
        settings.langfuse_base_url,
        settings.langfuse_ui_url,
        settings.langfuse_project_id,
    )


def _langfuse_key(settings: TracingSettings) -> str | None:
    """The public key a Langfuse tracer would be built with: `TRACING=langfuse` and both keys
    set; else None."""
    public, secret = settings.langfuse_public_key, settings.langfuse_secret_key
    if settings.tracing != "langfuse" or public is None or secret is None:
        return None
    return public.get_secret_value()


class ReloadingTracer:
    """The tracer `TRACING` names, built with the keys `key` reads (the env, else the
    dashboard) and the rest as `choice` gives it (the env, else the settings page).

    Building it here only checks that the env's `TRACING` has its keys (Bleep refuses to start
    otherwise); the tracer itself is built at its first use, after the stored settings are
    loaded, so its first client already has the settings page's address. `reload` builds it
    again after a change, except a running Langfuse tracer: Langfuse keeps one client per public
    key for the whole process (`LangfuseResourceManager`), so a second one would reuse the first
    one's address and secret key, and closing the first would shut down what the second shares.
    A change to its keys, address or project applies at the next start, and `restart_needed` says
    so; switching it off, or clearing a key, applies at once (`reload`)."""

    def __init__(
        self,
        key: Callable[[str], SecretStr | None],
        choice: Callable[[], TracingChoice] | None = None,
    ) -> None:
        self._key = key
        self._choice = choice
        check_tracer(self._settings())
        self._tracer: AnyTracer | None = None
        self._running: TracingSettings | None = None
        self._pending = False
        # Langfuse keeps a client per public key for the process: these have had one.
        self._langfuse_keys: set[str] = set()

    def _settings(self) -> TracingSettings:
        keys = {name.lower(): self._key(name) for names in TRACING_KEYS.values() for name in names}
        chosen = self._choice().model_dump() if self._choice is not None else {}
        return TracingSettings(**keys, **chosen)

    def current(self) -> AnyTracer:
        """The tracer in force, built at the first use."""
        if self._tracer is None:
            self._running = self._settings()
            self._tracer = self._build(self._running)
        return self._tracer

    def restart_needed(self) -> bool:
        """A change to a running Langfuse tracer waits for the next start."""
        return self._pending

    def run_config(self, incident: Incident) -> RunnableConfig:
        return self.current().run_config(incident)

    def talk_config(self, surface: TalkSurface) -> RunnableConfig:
        return self.current().talk_config(surface)

    def trace_url(self, incident: Incident) -> str | None:
        return self.current().trace_url(incident)

    async def reload(self) -> None:
        """Build the tracer with the settings in force now, then close the old one, which sends
        what it still holds. A run already going keeps the callbacks it was given. Not built yet,
        nothing to do: the first use builds it.

        Langfuse off, or one of its keys gone, applies at once: never trace on with a key that
        was cleared. A running Langfuse tracer whose keys, address or project changed stays as it
        is until the next start, and so does tracing off when Langfuse would come back with a
        public key a client was made for in this process: that client would come back with it."""
        old = self._tracer
        if old is None:
            return
        settings = self._settings()
        wanted = _langfuse_key(settings)
        if isinstance(old, LangfuseTracing) and wanted is not None:
            assert self._running is not None
            self._pending = _langfuse_view(settings) != _langfuse_view(self._running)
            if self._pending:
                log.info("tracing: the Langfuse change applies at the next start")
            return
        self._running = settings
        self._pending = wanted is not None and wanted in self._langfuse_keys
        if self._pending:
            log.info("tracing: Langfuse with a key used before here applies at the next start")
            self._tracer = NoTracing()
        else:
            self._tracer = self._build(settings)
        await old.close()

    def _build(self, settings: TracingSettings) -> AnyTracer:
        tracer = _built(settings)
        if isinstance(tracer, LangfuseTracing) and (key := _langfuse_key(settings)) is not None:
            self._langfuse_keys.add(key)
        return tracer

    async def close(self) -> None:
        if self._tracer is not None:
            await self._tracer.close()


def _built(settings: TracingSettings) -> AnyTracer:
    """`build_tracer`, or tracing off when it fails: never trace on with a key that was
    cleared."""
    try:
        return build_tracer(settings)
    except SettingsError as missing:
        log.warning("tracing: %s; tracing is off until they are set", missing)
    except Exception as error:
        log.warning(
            "tracing: the tracer could not be built (%s); tracing is off", type(error).__name__
        )
    return NoTracing()


def check_tracer(settings: TracingSettings) -> None:
    """Refuse a `TRACING` whose keys are missing."""
    if settings.tracing == "langfuse":
        missing = [
            name
            for name, value in (
                ("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key),
                ("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key),
            )
            if value is None
        ]
        if missing:
            raise SettingsError(
                f"TRACING=langfuse needs {' and '.join(missing)} set, in .env or on the dashboard"
            )
    if settings.tracing == "langsmith" and settings.langsmith_api_key is None:
        raise SettingsError(
            "TRACING=langsmith needs LANGSMITH_API_KEY set, in .env or on the dashboard"
        )


def build_tracer(settings: TracingSettings) -> AnyTracer:
    """The tracer `TRACING` names; refuses to start with its keys missing."""
    check_tracer(settings)
    if settings.tracing == "langfuse":
        return LangfuseTracing(settings)
    if settings.tracing == "langsmith":
        return LangSmithTracing(settings)
    return NoTracing()
