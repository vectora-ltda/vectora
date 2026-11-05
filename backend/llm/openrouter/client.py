"""Cliente HTTP do OpenRouter — base das 7 capacidades.

Base ``https://openrouter.ai/api/v1``, auth ``Bearer OPENROUTER_API_KEY``.
Cada capacidade (chat, embeddings, rerank, imagem, TTS, STT, vídeo) usa
``post_json``/``post_bytes``/``stream_sse`` daqui e herda o mapeamento
status → exceção tipada, em vez de repetir tratamento de erro.

Formatos de retorno divergem por capacidade e não podem ser unificados:
imagem devolve base64 dentro de JSON, TTS devolve bytestream binário. Por
isso ``post_json`` e ``post_bytes`` são métodos distintos.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Self

logger = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"

#: Headers de atribuição — o OpenRouter usa os dois pra creditar o tráfego ao
#: app na listagem pública. Sem eles o uso aparece como anônimo.
_REFERER = "https://vectora.chat"
_TITLE = "Vectora"

_DEFAULT_TIMEOUT_S = 120.0


class OpenRouterError(RuntimeError):
    """Base de toda falha do OpenRouter."""


class OpenRouterAuthError(OpenRouterError):
    """Key ausente ou inválida (401)."""


class OpenRouterCreditError(OpenRouterError):
    """Sem crédito na conta (402)."""


class OpenRouterRateLimitError(OpenRouterError):
    """Limite de requisições atingido (429)."""


class OpenRouterServerError(OpenRouterError):
    """Falha do lado do OpenRouter ou do provider roteado (5xx)."""


class OpenRouterResponseError(OpenRouterError):
    """Resposta com forma inesperada — corpo não-JSON, campo obrigatório
    ausente. Separada dos erros de status porque a ação é outra: aqui o
    problema não é a conta nem a requisição, é o que voltou."""


def _extrair_mensagem(corpo: Any, fallback: str) -> str:
    if isinstance(corpo, dict):
        erro = corpo.get("error")
        if isinstance(erro, dict) and erro.get("message"):
            return str(erro["message"])
        if isinstance(erro, str) and erro:
            return erro
    return fallback


class OpenRouterClient:
    """Cliente async. Use como context manager quando dono do http_client."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        http_client: Any = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        require_api_key: bool = True,
        include_attribution: bool = True,
    ) -> None:
        if require_api_key and not (api_key or "").strip():
            msg = (
                "OPENROUTER_API_KEY não configurado — configure a chave em "
                "Integrações antes de usar o provider openrouter."
            )
            raise OpenRouterAuthError(msg)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._require_api_key = require_api_key
        self._include_attribution = include_attribution
        self._client = http_client
        self._owns_client = http_client is None

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key.strip():
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._include_attribution:
            headers.update({"HTTP-Referer": _REFERER, "X-Title": _TITLE})
        if extra:
            headers.update(extra)
        return headers

    async def _ensure_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    async def __aenter__(self) -> Self:
        await self._ensure_client()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        import contextlib

        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()

    def _raise_for_status(self, status: int, corpo: Any) -> None:
        if status < 400:
            return
        msg = _extrair_mensagem(corpo, f"OpenRouter respondeu {status}")
        if status == 401:
            raise OpenRouterAuthError(msg)
        if status == 402:
            raise OpenRouterCreditError(msg)
        if status == 429:
            raise OpenRouterRateLimitError(msg)
        if status >= 500:
            raise OpenRouterServerError(msg)
        raise OpenRouterResponseError(msg)

    async def post_json(
        self, path: str, payload: dict, *, headers: dict[str, str] | None = None
    ) -> dict:
        """POST com resposta JSON — chat, embeddings, rerank, imagem, vídeo."""
        client = await self._ensure_client()
        resp = await client.post(
            f"{self._base_url}{path}",
            json=payload,
            headers=self._headers(headers),
        )
        try:
            corpo = resp.json()
        except Exception:
            corpo = None
        self._raise_for_status(resp.status_code, corpo)
        if not isinstance(corpo, dict):
            # Proxy/CDN no meio devolvendo HTML: sem este corte, o chamador
            # recebe JSONDecodeError cru, que não diz nada sobre a causa.
            trecho = (resp.text or "")[:200]
            msg = f"OpenRouter devolveu corpo não-JSON em {path}: {trecho!r}"
            raise OpenRouterResponseError(msg)
        return corpo

    async def post_bytes(
        self, path: str, payload: dict, *, headers: dict[str, str] | None = None
    ) -> bytes:
        """POST com resposta binária — TTS (`/audio/speech`) devolve o áudio
        cru, não base64. Passar esse corpo por `post_json` corrompe o arquivo."""
        client = await self._ensure_client()
        resp = await client.post(
            f"{self._base_url}{path}",
            json=payload,
            headers=self._headers(headers),
        )
        if resp.status_code >= 400:
            try:
                corpo = resp.json()
            except Exception:
                corpo = None
            self._raise_for_status(resp.status_code, corpo)
        conteudo = resp.content
        if not conteudo:
            msg = f"OpenRouter devolveu corpo vazio em {path}"
            raise OpenRouterResponseError(msg)
        return conteudo

    async def get_json(self, path_or_url: str) -> dict:
        """GET com resposta JSON — consulta de status do vídeo.

        Aceita path relativo **ou** URL absoluta: a resposta do `/videos`
        devolve `polling_url` já completa, e reconstruí-la a partir do id
        quebraria se a API mudar o formato.
        """
        client = await self._ensure_client()
        url = (
            path_or_url
            if path_or_url.startswith("http")
            else f"{self._base_url}{path_or_url}"
        )
        resp = await client.get(url, headers=self._headers())
        try:
            corpo = resp.json()
        except Exception:
            corpo = None
        self._raise_for_status(resp.status_code, corpo)
        if not isinstance(corpo, dict):
            trecho = (resp.text or "")[:200]
            msg = f"OpenRouter devolveu corpo não-JSON em {path_or_url}: {trecho!r}"
            raise OpenRouterResponseError(msg)
        return corpo

    async def post_multipart(
        self,
        path: str,
        *,
        files: dict[str, Any],
        data: dict[str, Any] | None = None,
    ) -> dict:
        """POST multipart com resposta JSON — STT (`/audio/transcriptions`).

        Terceiro formato do conjunto: as demais capacidades mandam JSON no
        corpo, esta manda o arquivo de áudio. Mandar JSON aqui rende 400.
        """
        client = await self._ensure_client()
        resp = await client.post(
            f"{self._base_url}{path}",
            files=files,
            data=data or {},
            headers=self._headers(),
        )
        try:
            corpo = resp.json()
        except Exception:
            corpo = None
        self._raise_for_status(resp.status_code, corpo)
        if not isinstance(corpo, dict):
            trecho = (resp.text or "")[:200]
            msg = f"OpenRouter devolveu corpo não-JSON em {path}: {trecho!r}"
            raise OpenRouterResponseError(msg)
        return corpo

    async def stream_sse(
        self,
        path: str,
        payload: dict,
        *,
        headers: dict[str, str] | None = None,
        retry_attempts: int = 3,
        retry_backoff_s: float = 0.3,
    ) -> AsyncIterator[dict]:
        """Consome o stream SSE do `/chat/completions`.

        Uma linha malformada é descartada com aviso em vez de abortar: o que
        já chegou é conteúdo válido que o usuário está lendo.

        Retry (backoff exponencial, `retry_attempts` tentativas) só cobre a
        fase de CONEXÃO — `httpx.ConnectError`/`ConnectTimeout` levantados
        antes do primeiro byte do stream chegar ao chamador. Uma falha
        depois que já entregamos algum evento nunca é retentada: reconectar
        reenviaria a resposta inteira, duplicando conteúdo que o usuário já
        está lendo. Sem isso, um blip de rede de milissegundos (DNS,
        roteamento) derrubava o turno inteiro mesmo com o provider saudável
        — foi o que aconteceu num log real de produção sem nenhuma outra
        chamada de rede do mesmo boot falhando.
        """
        import httpx

        client = await self._ensure_client()
        corpo = {**payload, "stream": True}
        request_headers = self._headers(
            {"Accept": "text/event-stream", **(headers or {})}
        )

        tentativa = 0
        while True:
            entregou_algo = False
            try:
                async with client.stream(
                    "POST",
                    f"{self._base_url}{path}",
                    json=corpo,
                    headers=request_headers,
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
                                "openrouter: chunk SSE malformado descartado",
                                extra={"path": path, "trecho": dado[:120]},
                            )
                            continue
                        if isinstance(evento, dict):
                            entregou_algo = True
                            yield evento
                return
            except (httpx.ConnectError, httpx.ConnectTimeout):
                tentativa += 1
                if entregou_algo or tentativa >= retry_attempts:
                    raise
                logger.warning(
                    "openrouter: falha ao conectar (tentativa %d/%d) — retentando",
                    tentativa,
                    retry_attempts,
                    extra={"path": path},
                )
                await asyncio.sleep(retry_backoff_s * (2 ** (tentativa - 1)))
