"""Cliente HTTP nativo do Google Gemini — base
``https://generativelanguage.googleapis.com/v1beta``, auth via query param
``key``, endpoints ``:generateContent``/``:streamGenerateContent?alt=sse``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

_DEFAULT_TIMEOUT_S = 120.0


class GoogleGenAIError(RuntimeError):
    """Base de toda falha do Google Gemini."""


class GoogleGenAIAuthError(GoogleGenAIError):
    """Key ausente ou inválida (401/403)."""


class GoogleGenAIRateLimitError(GoogleGenAIError):
    """Limite de requisições atingido (429)."""


class GoogleGenAIServerError(GoogleGenAIError):
    """Falha do lado do Google (5xx)."""


class GoogleGenAIResponseError(GoogleGenAIError):
    """Resposta com forma inesperada — corpo não-JSON, campo obrigatório
    ausente, ou resposta bloqueada por safety filter (sem `candidates`).
    Separada dos erros de status: aqui o problema é o que voltou."""


def _extrair_mensagem(corpo: Any, fallback: str) -> str:
    if isinstance(corpo, dict):
        erro = corpo.get("error")
        if isinstance(erro, dict) and erro.get("message"):
            return str(erro["message"])
    return fallback


class GoogleGenAIClient:
    """Cliente async. `api_key` obrigatório — Google não tem modo anônimo."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        http_client: Any = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not (api_key or "").strip():
            msg = "GOOGLE_API_KEY não configurado."
            raise GoogleGenAIAuthError(msg)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._client = http_client

    async def _ensure_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    async def aclose(self) -> None:
        import contextlib

        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()

    def _raise_for_status(self, status: int, corpo: Any) -> None:
        if status < 400:
            return
        msg = _extrair_mensagem(corpo, f"Google Gemini respondeu {status}")
        if status in (401, 403):
            raise GoogleGenAIAuthError(msg)
        if status == 429:
            raise GoogleGenAIRateLimitError(msg)
        if status >= 500:
            raise GoogleGenAIServerError(msg)
        raise GoogleGenAIResponseError(msg)

    async def generate_content(self, model: str, payload: dict) -> dict:
        """``POST /{model}:generateContent`` sem streaming."""
        client = await self._ensure_client()
        resp = await client.post(
            f"{self._base_url}/models/{model}:generateContent",
            params={"key": self._api_key},
            json=payload,
        )
        try:
            corpo = resp.json()
        except Exception:
            corpo = None
        self._raise_for_status(resp.status_code, corpo)
        if not isinstance(corpo, dict):
            trecho = (resp.text or "")[:200]
            msg = f"Google Gemini devolveu corpo não-JSON: {trecho!r}"
            raise GoogleGenAIResponseError(msg)
        return corpo

    async def stream_generate_content(
        self, model: str, payload: dict
    ) -> AsyncIterator[dict]:
        """``POST /{model}:streamGenerateContent?alt=sse``.

        Cada evento SSE é um `GenerateContentResponse` completo (não um
        delta parcial de um campo específico, diferente de Anthropic/OpenAI)
        — mas os `parts[]` de texto entre chunks são incrementais (concatenar
        na ordem de chegada), comportamento estabelecido do SDK oficial.
        """
        client = await self._ensure_client()
        async with client.stream(
            "POST",
            f"{self._base_url}/models/{model}:streamGenerateContent",
            params={"key": self._api_key, "alt": "sse"},
            json=payload,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                try:
                    erro = resp.json()
                except Exception:
                    erro = None
                self._raise_for_status(resp.status_code, erro)
            async for bruta in resp.aiter_lines():
                linha = bruta.strip()
                if not linha or not linha.startswith("data:"):
                    continue
                dado = linha[len("data:") :].strip()
                if not dado:
                    continue
                try:
                    evento = json.loads(dado)
                except json.JSONDecodeError:
                    logger.warning(
                        "google_genai: chunk SSE malformado descartado",
                        extra={"trecho": dado[:120]},
                    )
                    continue
                if isinstance(evento, dict):
                    yield evento
