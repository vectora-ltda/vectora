"""Cliente HTTP nativo da Anthropic — base ``https://api.anthropic.com/v1``,
auth ``x-api-key`` + header ``anthropic-version``, endpoint ``/messages``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

BASE_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"

_DEFAULT_TIMEOUT_S = 120.0


class AnthropicError(RuntimeError):
    """Base de toda falha da Anthropic."""


class AnthropicAuthError(AnthropicError):
    """Key ausente ou inválida (401)."""


class AnthropicRateLimitError(AnthropicError):
    """Limite de requisições atingido (429) ou sobrecarga (529)."""


class AnthropicServerError(AnthropicError):
    """Falha do lado da Anthropic (5xx)."""


class AnthropicResponseError(AnthropicError):
    """Resposta com forma inesperada — corpo não-JSON, campo obrigatório
    ausente, ou `event: error` no meio do stream. Separada dos erros de
    status: aqui o problema é o que voltou."""


def _extrair_mensagem(corpo: Any, fallback: str) -> str:
    if isinstance(corpo, dict):
        erro = corpo.get("error")
        if isinstance(erro, dict) and erro.get("message"):
            return str(erro["message"])
    return fallback


class AnthropicClient:
    """Cliente async. `api_key` obrigatório — Anthropic não tem modo anônimo."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        betas: list[str] | None = None,
        http_client: Any = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not (api_key or "").strip():
            msg = "ANTHROPIC_API_KEY não configurado."
            raise AnthropicAuthError(msg)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._betas = betas or []
        self._timeout_s = timeout_s
        self._client = http_client

    def _headers(self) -> dict[str, str]:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
        }
        if self._betas:
            headers["anthropic-beta"] = ",".join(self._betas)
        return headers

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
        msg = _extrair_mensagem(corpo, f"Anthropic respondeu {status}")
        if status == 401:
            raise AnthropicAuthError(msg)
        if status in (429, 529):
            raise AnthropicRateLimitError(msg)
        if status >= 500:
            raise AnthropicServerError(msg)
        raise AnthropicResponseError(msg)

    async def create_message(self, payload: dict) -> dict:
        """``POST /messages`` sem streaming."""
        client = await self._ensure_client()
        corpo_req = {**payload, "stream": False}
        resp = await client.post(
            f"{self._base_url}/messages", json=corpo_req, headers=self._headers()
        )
        try:
            corpo = resp.json()
        except Exception:
            corpo = None
        self._raise_for_status(resp.status_code, corpo)
        if not isinstance(corpo, dict):
            trecho = (resp.text or "")[:200]
            msg = f"Anthropic devolveu corpo não-JSON em /messages: {trecho!r}"
            raise AnthropicResponseError(msg)
        return corpo

    async def stream_message(self, payload: dict) -> AsyncIterator[dict]:
        """Consome o stream SSE de ``/messages``.

        `event: error` pode aparecer no meio do stream (ex: sobrecarga) —
        vira exceção tipada em vez de deixar o turno morrer silenciosamente.
        `ping` é ignorado.
        """
        client = await self._ensure_client()
        corpo = {**payload, "stream": True}
        async with client.stream(
            "POST",
            f"{self._base_url}/messages",
            json=corpo,
            headers={"Accept": "text/event-stream", **self._headers()},
        ) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                try:
                    erro = resp.json()
                except Exception:
                    erro = None
                self._raise_for_status(resp.status_code, erro)

            evento_atual: str | None = None
            async for bruta in resp.aiter_lines():
                linha = bruta.strip()
                if not linha:
                    continue
                if linha.startswith("event:"):
                    evento_atual = linha[len("event:") :].strip()
                    continue
                if not linha.startswith("data:"):
                    continue
                dado = linha[len("data:") :].strip()
                try:
                    payload_evento = json.loads(dado)
                except json.JSONDecodeError:
                    logger.warning(
                        "anthropic: chunk SSE malformado descartado",
                        extra={"trecho": dado[:120]},
                    )
                    continue
                if evento_atual == "error" or (
                    isinstance(payload_evento, dict)
                    and payload_evento.get("type") == "error"
                ):
                    erro = payload_evento.get("error") or {}
                    msg = _extrair_mensagem(
                        {"error": erro}, "Anthropic sinalizou erro no stream"
                    )
                    raise AnthropicResponseError(msg)
                if isinstance(payload_evento, dict):
                    yield payload_evento
                    if payload_evento.get("type") == "message_stop":
                        return
