"""Fallback automático entre providers de LLM ao esgotar quota.

Quando um provider devolve 429/quota, ``try_with_fallback`` percorre a cadeia
configurada (``llm_fallback_order`` em settings.json), pulando o provider atual
e os sem API key. Cada troca é registrada (ContextVar) para o handler de chat
drenar e notificar o frontend, e propagada via callback ``on_switch``.

Defensivo: erros que NÃO são de quota propagam imediatamente (sem fallback).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_QUOTA_MARKERS = ("429", "resource_exhausted", "quota", "rate limit", "rate_limit")
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connecttimeout",
    "readtimeout",
    "connection error",
    "connection refused",
    # Sobrecarga momentânea do provider (não é quota do usuário nem erro de
    # app) — confirmado com um 503 real do Gemini: "This model is currently
    # experiencing high demand. Spikes in demand are usually temporary."
    # Anthropic usa "overloaded_error" pro mesmo cenário.
    "503",
    "service unavailable",
    "overloaded",
    "high demand",
)
_PROVIDER_INCOMPATIBLE_MARKERS = (
    # Cohere Command A+ (e variantes): o client nativo sempre inclui
    # `tool_plan` ao serializar uma mensagem de assistente com tool_calls
    # no histórico — alguns deployments do modelo rejeitam esse campo com
    # 400. Nunca resolve no MESMO provider (retry idêntico falha sempre),
    # mas o próximo da cadeia de fallback processa a mesma mensagem sem
    # problema.
    "tool plan` cannot be used with this model",
    "tool_plan",
)
_MULTIMODAL_INCOMPATIBLE_MARKERS = (
    "image content is not supported",
    "image input is not supported",
    "image_url is not supported",
    "unsupported content type: image",
    "does not support image",
    "model does not support vision",
)


class QuotaExhaustedError(Exception):
    """Todos os providers da cadeia esgotaram a quota."""

    def __init__(
        self,
        message: str,
        *,
        model_id: str | None = None,
        provider: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.model_id = model_id
        self.provider = provider
        self.retry_after = retry_after


def is_quota_error(exc: BaseException) -> bool:
    """True se a exceção indica quota/rate-limit esgotada.

    Checa tipo antes de substring — exceções tipadas de provider (ex.
    `OpenRouterRateLimitError`) são reconhecidas mesmo se a mensagem exata
    mudar; o substring continua como rede de segurança pra exceções não
    tipadas de outros providers (Anthropic/Gemini/Cohere levantam erro
    genérico da SDK, não uma classe própria de rate limit).
    """
    if isinstance(exc, QuotaExhaustedError):
        return True

    from backend.llm.openrouter.client import OpenRouterRateLimitError

    if isinstance(exc, OpenRouterRateLimitError):
        return True

    msg = str(exc).lower()
    return any(marker in msg for marker in _QUOTA_MARKERS)


def is_transient_error(exc: BaseException) -> bool:
    """True se a exceção indica falha transiente (timeout, conexão) — vale tentar o próximo provider."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


def is_provider_incompatible_error(exc: BaseException) -> bool:
    """True se o provider rejeitou a request por incompatibilidade permanente
    entre o histórico e o schema do modelo (ex.: Cohere recusando `tool_plan`).
    Não é transiente (o MESMO provider falha sempre com a mesma mensagem),
    mas ainda vale trocar pro próximo da cadeia — a mensagem em si é válida."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _PROVIDER_INCOMPATIBLE_MARKERS)


def is_multimodal_incompatible_error(exc: BaseException) -> bool:
    """Return true only for explicit image/vision incompatibility errors."""
    msg = str(exc).lower()
    if is_quota_error(exc) or is_transient_error(exc):
        return False
    return any(marker in msg for marker in _MULTIMODAL_INCOMPATIBLE_MARKERS)


def _provider_of(model_id: str) -> str:
    return model_id.split(":", 1)[0]


def _provider_has_key(provider: str) -> bool:
    """True se o provider tem credencial configurada (ollama é sempre local).

    ``provider`` chega normalizado com underscore (mesma convenção de
    ``_provider_of``/``chat.py::_build_configurable``), então as chaves do
    keymap usam underscore também — "google-genai" (hífen) nunca bateria
    aqui e o Gemini nunca entraria como fallback de outro provider.
    """
    if provider == "ollama":
        return True
    from backend.settings import settings

    if provider == "nine_router":
        return bool(settings.nine_router_api_key and settings.nine_router_base_url)
    if provider == "dmr":
        return bool(settings.dmr_base_url)

    keymap = {
        "openai": settings.openai_api_key,
        "google_genai": settings.google_api_key,
        "anthropic": settings.anthropic_api_key,
        "cohere": settings.cohere_api_key,
        "openrouter": settings.openrouter_api_key,
    }
    return bool(keymap.get(provider))


def _fallback_order() -> list[str]:
    """Ordem de fallback configurada pelo usuário (lista de 'provider:model')."""
    from backend.workspace.runtime_settings import runtime_settings

    order = runtime_settings.get("llm_fallback_order", []) or []
    return [str(x) for x in order] if isinstance(order, list) else []


def _default_fallback_chain(current_provider: str) -> list[str]:
    """Retorna a cadeia vazia quando fallback não foi configurado.

    Fallback é uma decisão explícita do usuário: uma lista vazia não autoriza
    o agente a trocar de provider por conta própria, mesmo que outras chaves
    estejam presentes no ambiente.
    """
    _ = current_provider
    return []


def get_fallback_chain(current_model_id: str) -> list[str]:
    """Cadeia ordenada de modelos a tentar após o atual esgotar.

    Remove o provider atual (mesma quota) e os providers sem API key. Sem
    configuração explícita, retorna uma cadeia vazia.
    """
    configured = _fallback_order()
    if not configured:
        return []

    chain: list[str] = []
    for mid in configured:
        # Catalog/runtime IDs use hyphens while the credential key map uses
        # underscores (for example ``google-genai`` and ``nine-router``).
        prov = _provider_of(mid).replace("-", "_")
        current_prov = _provider_of(current_model_id).replace("-", "_")
        if prov == current_prov:
            continue
        if not _provider_has_key(prov):
            continue
        chain.append(mid)
    return chain


# ---------------------------------------------------------------------------
# Fila de trocas (drenada pelo handler de chat para notificar o frontend)
# ---------------------------------------------------------------------------

_PENDING_SWITCHES: ContextVar[list[dict[str, str]] | None] = ContextVar(
    "pending_switches", default=None
)


def record_switch(from_model: str, to_model: str) -> None:
    lst = _PENDING_SWITCHES.get()
    if lst is None:
        lst = []
        _PENDING_SWITCHES.set(lst)
    lst.append({"from": from_model, "to": to_model})


def drain_switches() -> list[dict[str, str]]:
    """Devolve as trocas pendentes e limpa a fila."""
    lst = _PENDING_SWITCHES.get()
    _PENDING_SWITCHES.set(None)
    return list(lst) if lst else []


async def emit_model_switch_event(from_model: str, to_model: str) -> None:
    """Registra a troca de embeddings/rerank na mesma fila (``record_switch``/
    ``drain_switches``) que o fallback de chat usa — o handler de chat drena
    e notifica o frontend independente de qual camada (chat/embeddings/
    rerank) gerou a troca.
    """
    record_switch(from_model, to_model)


async def try_with_fallback(  # noqa: UP047
    fn: Callable[[str], Awaitable[T]],
    model_id: str,
    *,
    on_switch: Callable[[str, str], Any] | None = None,
) -> T:
    """Executa ``fn(model_id)``; em quota error percorre a cadeia de fallback.

    Erros não-quota propagam de imediato. Esgotada a cadeia → QuotaExhaustedError.
    """
    try:
        return await fn(model_id)
    except Exception as exc:
        if not is_quota_error(exc):
            raise
        last_exc: BaseException = exc
        current = model_id

    for next_mid in get_fallback_chain(model_id):
        record_switch(current, next_mid)
        if on_switch is not None:
            try:
                on_switch(current, next_mid)
            except Exception:
                logger.debug("provider_fallback: on_switch falhou", exc_info=True)
        logger.warning(
            "provider_fallback: quota esgotada em %s → tentando %s", current, next_mid
        )
        try:
            return await fn(next_mid)
        except Exception as exc2:
            last_exc = exc2
            current = next_mid

    raise QuotaExhaustedError(
        f"Todos os providers esgotaram a quota (último: {current}).",
        model_id=current,
        provider=_provider_of(current),
    ) from last_exc
