"""Adaptadores de API remota para Cloudflare Clef e JEV SystemOne."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from backend.llm.systemone.errors import (
    SystemOneAuthenticationError,
    SystemOneCapabilityError,
    SystemOneConnectionError,
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

_DEFAULT_TIMEOUT_SECONDS = 30.0


class BaseSystemOneApiAdapter(SystemOneProvider):
    """Base compartilhada para adapters de API HTTP SystemOne (JEV e Clef API)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        provider_name: str,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Inicializa o estado da instância."""
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._provider_name = provider_name
        self._timeout = timeout
        self._external_client = client

    @property
    def provider_id(self) -> str:
        """Executa a operação provider id."""
        return self._provider_name

    async def aclose(self) -> None:
        """Encerra recursos de transporte HTTP quando gerenciados pelo adapter."""
        return

    async def health(self) -> bool:
        """Executa a operação health."""
        if not self._api_key:
            return False
        url = f"{self._base_url}/health"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            if self._external_client is not None:
                res = await self._external_client.get(url, headers=headers)
                return res.status_code == 200
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(url, headers=headers)
                return res.status_code == 200
        except Exception:
            return False

    def _decide_endpoint(self, model: str) -> str:
        """Executa a operação decide endpoint."""
        return f"{self._base_url}/v1/systemone"

    async def decide(self, request: SystemOneRequest) -> SystemOneResponse:
        """Executa a operação decide."""
        if not self._api_key or not self._api_key.strip():
            raise SystemOneAuthenticationError(
                f"Chave de API necessária para {self.provider_id}",
                provider=self.provider_id,
            )

        caps = await self.capabilities()
        if request.media_urls and not (caps.supports_image or caps.supports_video):
            raise SystemOneCapabilityError(
                f"{self.provider_id} não suporta mídias na configuração atual.",
                provider=self.provider_id,
            )

        endpoint = self._decide_endpoint(request.model)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

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
        if request.media_urls:
            payload["media_urls"] = request.media_urls

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
                f"Timeout ({self._timeout}s) em {self.provider_id}",
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
                f"Autenticação rejeitada por {self.provider_id} (HTTP {res.status_code})",
                provider=self.provider_id,
            )
        if res.status_code == 429:
            retry_after = res.headers.get("Retry-After")
            retry_s = (
                float(retry_after) if retry_after and retry_after.isdigit() else None
            )
            raise SystemOneRateLimitError(
                f"Limite de requisições excedido em {self.provider_id}",
                provider=self.provider_id,
                retry_after=retry_s,
            )
        if res.status_code >= 500:
            raise SystemOneConnectionError(
                f"Erro HTTP {res.status_code} de {self.provider_id}",
                provider=self.provider_id,
            )
        if res.status_code >= 400:
            raise SystemOneValidationError(
                f"Erro HTTP {res.status_code} de {self.provider_id}",
                provider=self.provider_id,
            )

        try:
            data = res.json()
        except Exception as exc:
            raise SystemOneValidationError(
                f"Resposta de {self.provider_id} não é JSON válido",
                provider=self.provider_id,
            ) from exc

        return parse_systemone_response(
            data,
            request,
            provider=self.provider_id,
            latency_ms=elapsed_ms,
        )


class ClefApiAdapter(BaseSystemOneApiAdapter):
    """Adaptador para Clef via API remota (Cloudflare Workers AI / Clef Cloud)."""

    def __init__(
        self,
        api_key: str,
        account_id: str,
        base_url: str = "https://api.cloudflare.com/client/v4/accounts",
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Inicializa o estado da instância."""
        if not account_id or not account_id.strip():
            raise ValueError("account_id é obrigatório para ClefApiAdapter")
        self._account_id = account_id.strip()
        super().__init__(
            base_url,
            api_key,
            provider_name="clef-api",
            timeout=timeout,
            client=client,
        )

    async def health(self) -> bool:
        """Executa a operação health."""
        if not self._api_key:
            return False
        url = "https://api.cloudflare.com/client/v4/user/tokens/verify"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            if self._external_client is not None:
                return (
                    await self._external_client.get(url, headers=headers)
                ).status_code == 200
            async with httpx.AsyncClient(timeout=5.0) as client:
                return (await client.get(url, headers=headers)).status_code == 200
        except Exception:
            return False

    def _decide_endpoint(self, model: str) -> str:
        """Executa a operação decide endpoint."""
        suffix = f"/{self._account_id}"
        if self._base_url.endswith(suffix):
            return f"{self._base_url}/ai/run/{model}"
        return f"{self._base_url}/{self._account_id}/ai/run/{model}"

    async def capabilities(self) -> ProviderCapabilities:
        """Executa a operação capabilities."""
        return ProviderCapabilities(
            supports_noul=True,
            supports_choice=True,
            supports_score=True,
            supports_image=True,
            supports_video=False,
            supported_models=["@cf/cloudflare/clef", "@cf/cloudflare/clef-flash"],
            evidence="documented",
        )


class JevApiAdapter(BaseSystemOneApiAdapter):
    """Adaptador para JEV via API remota (JEV Cloud)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.jev.ai",
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Inicializa o estado da instância."""
        super().__init__(
            base_url,
            api_key,
            provider_name="jev-api",
            timeout=timeout,
            client=client,
        )

    async def capabilities(self) -> ProviderCapabilities:
        """Executa a operação capabilities."""
        return ProviderCapabilities(
            supports_noul=True,
            supports_choice=True,
            supports_score=True,
            supports_image=True,
            supports_video=False,
            supported_models=["jev-1", "jev-fast"],
            evidence="documented",
        )
