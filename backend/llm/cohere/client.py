"""Cliente HTTP nativo do Cohere — base ``https://api.cohere.com``, auth
``Bearer COHERE_API_KEY``, sempre API v2 (Embed/Rerank).

Embed v2 não devolve índice explícito por item (diferente do Voyage/
OpenRouter) — a ordem da resposta é a ordem do request, documentado aqui
para quem consumir ``embed()`` não assumir o contrário.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

BASE_URL = "https://api.cohere.com"

_DEFAULT_TIMEOUT_S = 120.0

#: Limite de textos por chamada do Embed v2 — acima disso a API rejeita.
_EMBED_BATCH_SIZE = 96


class CohereError(RuntimeError):
    """Base de toda falha do Cohere."""


class CohereAuthError(CohereError):
    """Key ausente ou inválida (401)."""


class CohereRateLimitError(CohereError):
    """Limite de requisições atingido (429)."""


class CohereServerError(CohereError):
    """Falha do lado do Cohere (5xx)."""


class CohereResponseError(CohereError):
    """Resposta com forma inesperada — corpo não-JSON, campo obrigatório
    ausente. Separada dos erros de status: aqui o problema é o que voltou."""


def _extrair_mensagem(corpo: Any, fallback: str) -> str:
    if isinstance(corpo, dict):
        msg = corpo.get("message")
        if isinstance(msg, str) and msg:
            return msg
    return fallback


class CohereClient:
    """Cliente async. `api_key` obrigatório — Cohere não tem modo anônimo."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        http_client: Any = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not (api_key or "").strip():
            msg = "COHERE_API_KEY não configurado."
            raise CohereAuthError(msg)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._client = http_client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

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
        msg = _extrair_mensagem(corpo, f"Cohere respondeu {status}")
        if status == 401:
            raise CohereAuthError(msg)
        if status == 429:
            raise CohereRateLimitError(msg)
        if status >= 500:
            raise CohereServerError(msg)
        raise CohereResponseError(msg)

    async def _post_json(self, path: str, payload: dict) -> dict:
        client = await self._ensure_client()
        resp = await client.post(
            f"{self._base_url}{path}", json=payload, headers=self._headers()
        )
        try:
            corpo = resp.json()
        except Exception:
            corpo = None
        self._raise_for_status(resp.status_code, corpo)
        if not isinstance(corpo, dict):
            trecho = (resp.text or "")[:200]
            msg = f"Cohere devolveu corpo não-JSON em {path}: {trecho!r}"
            raise CohereResponseError(msg)
        return corpo

    async def embed(
        self,
        texts: list[str],
        *,
        model: str,
        input_type: str,
        embedding_types: list[str] | None = None,
        truncate: str = "END",
    ) -> list[list[float]]:
        """``POST /v2/embed`` — pagina internamente acima de 96 textos/call."""
        if not texts:
            return []

        vetores: list[list[float]] = []
        tipos = embedding_types or ["float"]
        for inicio in range(0, len(texts), _EMBED_BATCH_SIZE):
            lote = texts[inicio : inicio + _EMBED_BATCH_SIZE]
            corpo = await self._post_json(
                "/v2/embed",
                {
                    "model": model,
                    "input_type": input_type,
                    "texts": lote,
                    "embedding_types": tipos,
                    "truncate": truncate,
                },
            )
            embeddings = corpo.get("embeddings")
            floats = embeddings.get("float") if isinstance(embeddings, dict) else None
            if not isinstance(floats, list) or len(floats) != len(lote):
                msg = (
                    "Cohere devolveu `embeddings.float` ausente ou com "
                    f"quantidade divergente do lote ({len(lote)} textos) em "
                    "/v2/embed — a ingestão para aqui em vez de gravar "
                    "vetores nulos no índice"
                )
                raise CohereResponseError(msg)
            vetores.extend(floats)
        return vetores

    async def rerank(
        self,
        *,
        query: str,
        documents: list[str],
        model: str,
        top_n: int | None = None,
        max_tokens_per_doc: int = 4096,
    ) -> list[dict[str, Any]]:
        """``POST /v2/rerank`` — devolve ``results[]`` cru (index/relevance_score)."""
        if not documents:
            return []

        payload: dict[str, Any] = {
            "model": model,
            "query": query,
            "documents": documents,
            "max_tokens_per_doc": max_tokens_per_doc,
        }
        if top_n is not None:
            payload["top_n"] = top_n

        corpo = await self._post_json("/v2/rerank", payload)
        resultados = corpo.get("results")
        if not isinstance(resultados, list):
            msg = "Cohere devolveu `results` ausente/inválido em /v2/rerank"
            raise CohereResponseError(msg)
        return resultados

    async def chat(self, payload: dict) -> dict:
        """``POST /v2/chat`` sem streaming."""
        return await self._post_json("/v2/chat", {**payload, "stream": False})

    async def stream_chat(self, payload: dict) -> AsyncIterator[dict]:
        """Consome o stream SSE de ``/v2/chat``.

        Cada evento tem um campo `type` (``message-start``, ``content-delta``,
        ``tool-call-start``, ``tool-call-delta``, ``tool-call-end``,
        ``message-end``, etc). Linha malformada é descartada com aviso em vez
        de abortar o turno inteiro.
        """
        client = await self._ensure_client()
        corpo = {**payload, "stream": True}
        async with client.stream(
            "POST",
            f"{self._base_url}/v2/chat",
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
                if not dado:
                    continue
                try:
                    evento = json.loads(dado)
                except json.JSONDecodeError:
                    logger.warning(
                        "cohere: chunk SSE malformado descartado",
                        extra={"trecho": dado[:120]},
                    )
                    continue
                if isinstance(evento, dict):
                    yield evento
                    if evento.get("type") == "message-end":
                        return
