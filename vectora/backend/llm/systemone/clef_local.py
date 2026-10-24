"""Adaptador para o modelo Clef executando localmente via llama.cpp.

Consome nativamente o endpoint `POST /v1/systemone` preparado no llama.cpp.
Não converte uma chamada de decisão para `/v1/chat/completions` nem simula o contrato.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from backend.llm.systemone.errors import (
    SystemOneAuthenticationError,
    SystemOneCapabilityError,
    SystemOneConnectionError,
    SystemOneError,
    SystemOneRateLimitError,
    SystemOneTimeoutError,
    SystemOneValidationError,
)
from backend.llm.systemone.protocol import SystemOneProvider
from backend.llm.systemone.types import (
    ChoiceQuestion,
    NoulQuestion,
    ProviderCapabilities,
    ScoreQuestion,
    SystemOneRequest,
    SystemOneResponse,
    parse_systemone_response,
)

logger = logging.getLogger(__name__)

_DEFAULT_LLAMACPP_BASE_URL = "http://127.0.0.1:8080"
_DEFAULT_TIMEOUT_SECONDS = 30.0


class ClefLocalAdapter(SystemOneProvider):
    """Adaptador local Clef sobre o endpoint `POST /v1/systemone` do llama.cpp."""

    def __init__(
        self,
        base_url: str = _DEFAULT_LLAMACPP_BASE_URL,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        *,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._model = model.strip() if model and model.strip() else None
        self._external_client = client

    @property
    def provider_id(self) -> str:
        return "clef-local"

    async def aclose(self) -> None:
        """Encerra recursos de transporte HTTP quando gerenciados pelo adapter."""
        return

    async def capabilities(self) -> ProviderCapabilities:
        """Capacidades declaradas para Clef local (texto, noul, choice, score; sem mídias por padrão)."""
        models = ["clef", "clef-flash", "default"]
        if self._model is not None:
            models.append(self._model)
        return ProviderCapabilities(
            supports_noul=True,
            supports_choice=True,
            supports_score=True,
            supports_image=False,
            supports_video=False,
            supported_models=list(dict.fromkeys(models)),
            evidence="observed",
        )

    async def health(self) -> bool:
        """Verifica a saúde do servidor llama.cpp local."""
        url = f"{self._base_url}/health"
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            if self._external_client is not None:
                resp = await self._external_client.get(url, headers=headers)
                return resp.status_code == 200
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
                return resp.status_code == 200
        except Exception:
            return False

    async def decide(self, request: SystemOneRequest) -> SystemOneResponse:
        """Submete uma decisão para o endpoint POST /v1/systemone do llama.cpp."""
        caps = await self.capabilities()
        if request.media_urls and not (caps.supports_image or caps.supports_video):
            raise SystemOneCapabilityError(
                "Clef local não suporta anexos de imagem ou vídeo no runtime atual.",
                provider=self.provider_id,
            )

        endpoint = f"{self._base_url}/v1/systemone"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        serialized_questions: dict[str, Any] = {}
        for q_id, q in request.questions.items():
            if isinstance(q, NoulQuestion):
                serialized_questions[q_id] = {
                    "type": "noul",
                    "criterion": q.criterion,
                    "instructions": q.instructions,
                }
            elif isinstance(q, ChoiceQuestion):
                serialized_questions[q_id] = {
                    "type": "choice",
                    "options": q.options,
                    "instructions": q.instructions,
                }
            elif isinstance(q, ScoreQuestion):
                serialized_questions[q_id] = {
                    "type": "score",
                    "criterion": q.criterion,
                    "min_score": q.min_score,
                    "max_score": q.max_score,
                    "instructions": q.instructions,
                }

        payload: dict[str, Any] = {
            "model": request.model,
            "state": request.state,
            "questions": serialized_questions,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        start_time = time.perf_counter()
        try:
            if self._external_client is not None:
                res = await self._external_client.post(
                    endpoint, json=payload, headers=headers
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    res = await client.post(endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise SystemOneTimeoutError(
                f"Tempo esgotado ({self._timeout}s) ao chamar llama.cpp local",
                provider=self.provider_id,
            ) from exc
        except httpx.RequestError as exc:
            raise SystemOneConnectionError(
                "systemone_connection_error",
                provider=self.provider_id,
            ) from exc

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        if res.status_code in (401, 403):
            raise SystemOneAuthenticationError(
                "Autenticação rejeitada pelo endpoint llama.cpp",
                provider=self.provider_id,
            )
        if res.status_code == 429:
            retry_after = res.headers.get("Retry-After")
            retry_s = (
                float(retry_after) if retry_after and retry_after.isdigit() else None
            )
            raise SystemOneRateLimitError(
                "Limite de requisições excedido no endpoint llama.cpp",
                provider=self.provider_id,
                retry_after=retry_s,
            )
        if res.status_code == 404:
            raise SystemOneCapabilityError(
                "Endpoint POST /v1/systemone não encontrado no llama.cpp",
                provider=self.provider_id,
            )
        if res.status_code >= 500:
            raise SystemOneConnectionError(
                f"Servidor llama.cpp retornou erro HTTP {res.status_code}",
                provider=self.provider_id,
            )
        if res.status_code >= 400:
            raise SystemOneValidationError(
                f"Endpoint llama.cpp retornou erro HTTP {res.status_code}",
                provider=self.provider_id,
            )

        try:
            data = res.json()
        except Exception as exc:
            raise SystemOneValidationError(
                "Resposta do llama.cpp não contém JSON válido",
                provider=self.provider_id,
            ) from exc

        return parse_systemone_response(
            data,
            request,
            provider=self.provider_id,
            latency_ms=elapsed_ms,
        )
