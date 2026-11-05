"""Cliente HTTP nativo da OpenAI — base ``https://api.openai.com/v1``, auth
``Bearer OPENAI_API_KEY``, endpoint ``/responses`` (Responses API — recomendada
pela OpenAI pra tudo novo desde 2026, sucessora de Chat Completions).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openai.com/v1"

_DEFAULT_TIMEOUT_S = 120.0


class OpenAIError(RuntimeError):
    """Base de toda falha da OpenAI."""


class OpenAIAuthError(OpenAIError):
    """Key ausente ou inválida (401)."""


class OpenAIRateLimitError(OpenAIError):
    """Limite de requisições atingido (429)."""


class OpenAIServerError(OpenAIError):
    """Falha do lado da OpenAI (5xx)."""


class OpenAIResponseError(OpenAIError):
    """Resposta com forma inesperada — corpo não-JSON, campo obrigatório
    ausente. Separada dos erros de status: aqui o problema é o que voltou."""


def _extrair_mensagem(corpo: Any, fallback: str) -> str:
    if isinstance(corpo, dict):
        erro = corpo.get("error")
        if isinstance(erro, dict) and erro.get("message"):
            return str(erro["message"])
    return fallback


class OpenAIClient:
    """Cliente async. `api_key` obrigatório — OpenAI não tem modo anônimo."""

    def __init__(
        self,
        api_key: str,
        *,
        organization: str | None = None,
        project: str | None = None,
        base_url: str = BASE_URL,
        http_client: Any = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not (api_key or "").strip():
            msg = "OPENAI_API_KEY não configurado."
            raise OpenAIAuthError(msg)
        self._api_key = api_key
        self._organization = organization
        self._project = project
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._client = http_client

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._organization:
            headers["OpenAI-Organization"] = self._organization
        if self._project:
            headers["OpenAI-Project"] = self._project
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
        msg = _extrair_mensagem(corpo, f"OpenAI respondeu {status}")
        if status == 401:
            raise OpenAIAuthError(msg)
        if status == 429:
            raise OpenAIRateLimitError(msg)
        if status >= 500:
            raise OpenAIServerError(msg)
        raise OpenAIResponseError(msg)

    async def create_response(self, payload: dict) -> dict:
        """``POST /responses`` sem streaming."""
        client = await self._ensure_client()
        corpo_req = {**payload, "stream": False}
        resp = await client.post(
            f"{self._base_url}/responses", json=corpo_req, headers=self._headers()
        )
        try:
            corpo = resp.json()
        except Exception:
            corpo = None
        self._raise_for_status(resp.status_code, corpo)
        if not isinstance(corpo, dict):
            trecho = (resp.text or "")[:200]
            msg = f"OpenAI devolveu corpo não-JSON em /responses: {trecho!r}"
            raise OpenAIResponseError(msg)
        return corpo

    async def stream_response(self, payload: dict) -> AsyncIterator[dict]:
        """Consome o stream SSE de ``/responses``.

        Cada evento tem um campo `type` (``response.output_item.added``,
        ``response.function_call_arguments.delta``, etc). Linha malformada é
        descartada com aviso em vez de abortar o turno inteiro.
        """
        client = await self._ensure_client()
        corpo = {**payload, "stream": True}
        async with client.stream(
            "POST",
            f"{self._base_url}/responses",
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
            async for bruta in resp.aiter_lines():
                linha = bruta.strip()
                if not linha or not linha.startswith("data:"):
                    continue
                dado = linha[len("data:") :].strip()
                if dado == "[DONE]":
                    return
                try:
                    evento = json.loads(dado)
                except json.JSONDecodeError:
                    logger.warning(
                        "openai: chunk SSE malformado descartado",
                        extra={"trecho": dado[:120]},
                    )
                    continue
                if isinstance(evento, dict):
                    yield evento
                    if evento.get("type") == "response.completed":
                        return
