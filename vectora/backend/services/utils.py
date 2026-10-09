"""Utility Functions and Multi-Provider LLM Loading.

``load_native_llm()`` resolve o provider ativo (env > runtime_settings >
defaults) e devolve o ``ChatClient`` nativo do provider — Google Gemini,
OpenAI, Anthropic, Cohere, Ollama e OpenRouter — via
``backend/llm/fallback_chat_client.load_chat_client``.

Inclui o async context manager de ciclo de vida da aplicação.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from backend.llm.base import ChatClient


# provider canônico (underscore) → env var do modelo.
# A UI/LLM_PROVIDER pode usar hífen ("google-genai"); normalizamos p/ underscore.
#
# Sem modelo default por provider de propósito: um default aqui seria
# adotado silenciosamente sempre que o usuário configurou o provider mas
# não o modelo — mesmo anti-padrão que `RuntimeSettings._DEFAULTS` tinha
# (ver `backend/workspace/runtime_settings.py`). Sem env var nem
# `runtime_settings.active_model` setados, `load_native_llm` levanta
# erro em vez de inventar um modelo.
_PROVIDER_SPEC: dict[str, str] = {
    "google_genai": "GOOGLE_MODEL",
    "openai": "OPENAI_MODEL",
    "anthropic": "ANTHROPIC_MODEL",
    "cohere": "COHERE_CHAT_MODEL",
    "ollama": "OLLAMA_MODEL",
    "openrouter": "OPENROUTER_MODEL",
    "nine_router": "NINE_ROUTER_MODEL",
    "llamacpp": "LLAMACPP_MODEL",
}


def load_native_llm(model_id: str = "") -> ChatClient:
    """Carrega o ``ChatClient`` nativo do provider ativo.

    Mesma resolução de provider/modelo que o antigo ``load_llm`` (env >
    runtime_settings > defaults), mas devolve um ``ChatClient``
    (``backend/llm/base.py``) em vez de ``BaseChatModel``. ``model_id``
    (opcional, formato ``"provider:model"``) sobrepõe o provider/modelo
    padrão — usado pela troca de modelo por request.
    """
    import os

    from backend.workspace.runtime_settings import runtime_settings

    if model_id:
        prov, _sep, name = model_id.partition(":")
        provider = prov.replace("-", "_")
        env_var = _PROVIDER_SPEC.get(provider, "")
        model_name = name or (os.getenv(env_var) if env_var else None) or ""
    else:
        provider = (
            os.getenv("LLM_PROVIDER") or runtime_settings.active_provider
        ).replace("-", "_")
        if not provider:
            msg = (
                "Nenhum provider de LLM configurado — configure um "
                "provider (setup/`/model`) antes de usar recursos que "
                "dependem de LLM."
            )
            raise ValueError(msg)
        env_var = _PROVIDER_SPEC.get(provider)
        if env_var is None:
            msg = (
                f"LLM_PROVIDER desconhecido: {provider!r}. Suportados: "
                "google_genai, openai, anthropic, cohere, ollama, openrouter, nine_router, llamacpp"
            )
            raise ValueError(msg)
        active = (
            runtime_settings.active_model
            if runtime_settings.active_provider.replace("-", "_") == provider
            else ""
        )
        model_name = os.getenv(env_var) or active or ""

    if not model_name and provider == "nine_router":
        from backend.settings import settings

        model_name = settings.nine_router_default_model or ""

    if not model_name:
        msg = (
            f"Nenhum modelo de LLM configurado para o provider {provider!r} "
            "— configure um modelo (setup/`/model`) em vez de depender de "
            "um default automático."
        )
        raise ValueError(msg)

    mid = f"{provider}:{model_name}"
    from backend.llm.fallback_chat_client import load_chat_client

    return load_chat_client(mid)


@asynccontextmanager
async def async_lifespan() -> AsyncGenerator[None]:
    """Async context manager for application lifecycle.

    Inicia o BackgroundEmbeddingWorker na entrada e para gracefully na saída.
    O worker lê da fila SQLite e escreve embeddings no LanceDB.

    Sem este lifespan, os documentos enfileirados pela tool `embedding` ficam
    em status "pending" indefinidamente — nada os processa.

    Usage:
        async with async_lifespan():
            # Application runs here
            pass
    """
    import logging

    from backend.embedding.background import get_background_worker

    _log = logging.getLogger(__name__)

    worker = await get_background_worker()
    await worker.start()
    _log.info("BackgroundEmbeddingWorker iniciado via async_lifespan")

    try:
        yield
    finally:
        await worker.stop()
        _log.info("BackgroundEmbeddingWorker parado via async_lifespan")
