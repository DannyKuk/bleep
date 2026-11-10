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


class ReloadingTracer:
    """The tracer `TRACING` names, built with the keys `key` reads (the env, else the
    dashboard) and built again by `reload` after one of them changed on the dashboard."""

    def __init__(self, key: Callable[[str], SecretStr | None]) -> None:
        self._key = key
        self._tracer = build_tracer(self._settings())

    def _settings(self) -> TracingSettings:
        keys = {name.lower(): self._key(name) for names in TRACING_KEYS.values() for name in names}
        return TracingSettings(**keys)

    def run_config(self, incident: Incident) -> RunnableConfig:
        return self._tracer.run_config(incident)

    def talk_config(self, surface: TalkSurface) -> RunnableConfig:
        return self._tracer.talk_config(surface)

    def trace_url(self, incident: Incident) -> str | None:
        return self._tracer.trace_url(incident)

    async def reload(self) -> None:
        """Build the tracer with the keys in force now, then close the old one, which sends what
        it still holds. A run already going keeps the callbacks it was given."""
        old = self._tracer
        try:
            self._tracer = build_tracer(self._settings())
        except SettingsError as missing:
            log.warning("tracing: %s; tracing is off until they are set", missing)
            self._tracer = NoTracing()
        except Exception as error:  # never trace on with a key that was replaced or cleared
            log.warning(
                "tracing: the tracer could not be built (%s); tracing is off", type(error).__name__
            )
            self._tracer = NoTracing()
        await old.close()

    async def close(self) -> None:
        await self._tracer.close()


def build_tracer(settings: TracingSettings) -> NoTracing | LangfuseTracing | LangSmithTracing:
    """The tracer `TRACING` names; refuses to start with its keys missing."""
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
        return LangfuseTracing(settings)
    if settings.tracing == "langsmith":
        if settings.langsmith_api_key is None:
            raise SettingsError(
                "TRACING=langsmith needs LANGSMITH_API_KEY set, in .env or on the dashboard"
            )
        return LangSmithTracing(settings)
    return NoTracing()
