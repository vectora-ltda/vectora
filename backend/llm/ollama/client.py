"""Cliente HTTP do Ollama — base do chat, das capacidades e dos embeddings.

Aqui o "provider" é a máquina do próprio usuário, então as mensagens de erro
dizem **o que fazer** (subir o servidor, baixar o modelo) em vez de repetir o
status HTTP.

Streaming é **NDJSON** (`application/x-ndjson`): um objeto JSON por linha até
`done: true`. Não é SSE — parsear como SSE devolve zero chunk, porque nenhuma
linha começa com `data:`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:11434"

_DEFAULT_TIMEOUT_S = 300.0


class OllamaError(RuntimeError):
    """Base de toda falha do Ollama."""


class OllamaUnreachableError(OllamaError):
    """Servidor fora do ar ou endereço errado."""


class OllamaModelNotFoundError(OllamaError):
    """Modelo não baixado neste servidor."""


class OllamaResponseError(OllamaError):
    """Resposta com forma inesperada — campo obrigatório ausente."""


class OllamaClient:
    """Cliente async. `api_key` só é necessário no Ollama Cloud."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        http_client: Any = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._client = http_client

    @property
    def base_url(self) -> str:
        return self._base_url

    def _headers(self) -> dict[str, str]:
        # Bearer só no Ollama Cloud; o servidor local ignora o header.
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

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

    def _erro_de_conexao(self, exc: Exception) -> OllamaUnreachableError:
        msg = (
            f"Ollama inacessível em {self._base_url} — confira se o servidor "
            f"está rodando (`ollama serve`) e se a porta 11434 está correta "
            f"nas Settings. Detalhe: {exc}"
        )
        return OllamaUnreachableError(msg)

    def _raise_for_status(self, status: int, corpo: Any, *, path: str) -> None:
        if status < 400:
            return
        detalhe = ""
        if isinstance(corpo, dict):
            detalhe = str(corpo.get("error") or "")
        if status == 404:
            msg = (
                f"modelo não encontrado no Ollama ({detalhe or path}) — baixe "
                f"com `ollama pull <modelo>` antes de usar"
            )
            raise OllamaModelNotFoundError(msg)
        msg = f"Ollama respondeu {status} em {path}: {detalhe or 'sem detalhe'}"
        raise OllamaResponseError(msg)

    async def post_json(self, path: str, payload: dict) -> dict:
        client = await self._ensure_client()
        try:
            resp = await client.post(
                f"{self._base_url}{path}", json=payload, headers=self._headers()
            )
        except Exception as exc:
            if type(exc).__name__ in ("ConnectError", "ConnectTimeout", "ReadTimeout"):
                raise self._erro_de_conexao(exc) from exc
            raise
        try:
            corpo = resp.json()
        except Exception:
            corpo = None
        self._raise_for_status(resp.status_code, corpo, path=path)
        if not isinstance(corpo, dict):
            trecho = (resp.text or "")[:200]
            msg = f"Ollama devolveu corpo não-JSON em {path}: {trecho!r}"
            raise OllamaResponseError(msg)
        return corpo

    async def get_json(self, path: str) -> dict:
        client = await self._ensure_client()
        try:
            resp = await client.get(f"{self._base_url}{path}", headers=self._headers())
        except Exception as exc:
            if type(exc).__name__ in ("ConnectError", "ConnectTimeout", "ReadTimeout"):
                raise self._erro_de_conexao(exc) from exc
            raise
        try:
            corpo = resp.json()
        except Exception:
            corpo = None
        self._raise_for_status(resp.status_code, corpo, path=path)
        if not isinstance(corpo, dict):
            msg = f"Ollama devolveu corpo não-JSON em {path}"
            raise OllamaResponseError(msg)
        return corpo

    async def stream_ndjson(self, path: str, payload: dict) -> AsyncIterator[dict]:
        """Consome o stream NDJSON — um objeto por linha até `done: true`.

        Linha malformada é descartada com aviso em vez de abortar: o que já
        chegou é conteúdo válido que o usuário está lendo.
        """
        client = await self._ensure_client()
        corpo = {**payload, "stream": True}
        try:
            async with client.stream(
                "POST",
                f"{self._base_url}{path}",
                json=corpo,
                headers=self._headers(),
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    try:
                        erro = resp.json()
                    except Exception:
                        erro = None
                    self._raise_for_status(resp.status_code, erro, path=path)
                async for bruta in resp.aiter_lines():
                    linha = bruta.strip()
                    if not linha:
                        continue
                    try:
                        evento = json.loads(linha)
                    except json.JSONDecodeError:
                        logger.warning(
                            "ollama: linha NDJSON malformada descartada",
                            extra={"path": path, "trecho": linha[:120]},
                        )
                        continue
                    if isinstance(evento, dict):
                        yield evento
                        if evento.get("done"):
                            return
        except OllamaError:
            raise
        except Exception as exc:
            if type(exc).__name__ in ("ConnectError", "ConnectTimeout", "ReadTimeout"):
                raise self._erro_de_conexao(exc) from exc
            raise
