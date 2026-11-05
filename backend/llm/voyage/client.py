"""Cliente HTTP nativo da Voyage AI — base ``https://api.voyageai.com/v1``,
auth ``Bearer VOYAGE_API_KEY``.

Diferente do Cohere Embed v2, a Voyage devolve ``data[].index`` explícito em
embeddings e rerank — usado para reordenar em vez de confiar na ordem de
chegada.
"""

from __future__ import annotations

from typing import Any

BASE_URL = "https://api.voyageai.com/v1"

_DEFAULT_TIMEOUT_S = 120.0


class VoyageError(RuntimeError):
    """Base de toda falha da Voyage AI."""


class VoyageAuthError(VoyageError):
    """Key ausente ou inválida (401)."""


class VoyageRateLimitError(VoyageError):
    """Limite de requisições atingido (429)."""


class VoyageServerError(VoyageError):
    """Falha do lado da Voyage (5xx)."""


class VoyageResponseError(VoyageError):
    """Resposta com forma inesperada — corpo não-JSON, campo obrigatório
    ausente. Separada dos erros de status: aqui o problema é o que voltou."""


def _extrair_mensagem(corpo: Any, fallback: str) -> str:
    if isinstance(corpo, dict):
        detail = corpo.get("detail")
        if isinstance(detail, str) and detail:
            return detail
    return fallback


class VoyageClient:
    """Cliente async. `api_key` obrigatório — Voyage não tem modo anônimo."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        http_client: Any = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not (api_key or "").strip():
            msg = "VOYAGE_API_KEY não configurado."
            raise VoyageAuthError(msg)
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
        msg = _extrair_mensagem(corpo, f"Voyage respondeu {status}")
        if status == 401:
            raise VoyageAuthError(msg)
        if status == 429:
            raise VoyageRateLimitError(msg)
        if status >= 500:
            raise VoyageServerError(msg)
        raise VoyageResponseError(msg)

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
            msg = f"Voyage devolveu corpo não-JSON em {path}: {trecho!r}"
            raise VoyageResponseError(msg)
        return corpo

    async def embed(
        self,
        texts: list[str],
        *,
        model: str,
        input_type: str | None = None,
        truncation: bool = True,
        output_dimension: int | None = None,
        output_dtype: str = "float",
    ) -> list[list[float]]:
        """``POST /embeddings`` — reordena por ``data[].index`` explícito."""
        if not texts:
            return []

        payload: dict[str, Any] = {
            "input": texts,
            "model": model,
            "truncation": truncation,
            "output_dtype": output_dtype,
        }
        if input_type:
            payload["input_type"] = input_type
        if output_dimension:
            payload["output_dimension"] = output_dimension

        corpo = await self._post_json("/embeddings", payload)
        dados = corpo.get("data")
        if not isinstance(dados, list) or not dados:
            msg = (
                "Voyage devolveu `data` vazio em /embeddings — a ingestão "
                "para aqui em vez de gravar vetores nulos no índice"
            )
            raise VoyageResponseError(msg)

        por_indice: dict[int, list[float]] = {}
        for item in dados:
            vetor = item.get("embedding")
            if not isinstance(vetor, list):
                msg = "Item de /embeddings sem campo `embedding` utilizável"
                raise VoyageResponseError(msg)
            por_indice[int(item.get("index", len(por_indice)))] = vetor

        faltando = [i for i in range(len(texts)) if i not in por_indice]
        if faltando:
            msg = f"Voyage não devolveu embedding para os índices {faltando}"
            raise VoyageResponseError(msg)

        return [por_indice[i] for i in range(len(texts))]

    async def rerank(
        self,
        *,
        query: str,
        documents: list[str],
        model: str,
        top_k: int | None = None,
        truncation: bool = True,
    ) -> list[dict[str, Any]]:
        """``POST /rerank`` — devolve ``data[]`` cru (index/relevance_score)."""
        if not documents:
            return []

        payload: dict[str, Any] = {
            "query": query,
            "documents": documents,
            "model": model,
            "truncation": truncation,
        }
        if top_k is not None:
            payload["top_k"] = top_k

        corpo = await self._post_json("/rerank", payload)
        dados = corpo.get("data")
        if not isinstance(dados, list):
            msg = "Voyage devolveu `data` ausente/inválido em /rerank"
            raise VoyageResponseError(msg)
        return dados
