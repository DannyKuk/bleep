"""Builds the chat model for a role. Both providers speak the OpenAI chat protocol."""

from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_openrouter import ChatOpenRouter

from bleep.schemas.settings import ModelSettings

Role = Literal["main", "cheap"]


def chat_model(settings: ModelSettings, role: Role) -> BaseChatModel:
    if role not in ("main", "cheap"):
        raise ValueError(f"unknown model role {role!r}")
    if settings.provider == "local":
        return ChatOpenAI(
            base_url=settings.local_base_url,
            model=settings.local_model,
            api_key="local",
            temperature=settings.temperature,
        )
    name = settings.model if role == "main" else settings.cheap_model
    key = {"api_key": settings.openrouter_api_key} if settings.openrouter_api_key else {}
    return ChatOpenRouter(model=name, temperature=settings.temperature, **key)
