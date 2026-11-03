"""Contratos comuns para descoberta e seleção de provedores LLM."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

ProviderKind = Literal[
    "google-genai",
    "openai",
    "ollama",
    "openrouter",
    "nine_router",
    "llamacpp",
]


class ProviderModel(TypedDict):
    """Modelo descoberto por um endpoint compatível com OpenAI."""

    id: str
    name: NotRequired[str]


class ProviderDownloadProgress(TypedDict):
    """Estado público de um download de modelo local."""

    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    downloaded_bytes: int
    total_bytes: int | None
    error: NotRequired[str | None]
