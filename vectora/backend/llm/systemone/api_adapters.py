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
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    ProviderCapabilities,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneAnswer,
    SystemOneMetadata,
    SystemOneRequest,
    SystemOneResponse,
    SystemOneUsage,
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
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._provider_name = provider_name
        self._timeout = timeout

    @property
    def provider_id(self) -> str:
        return self._provider_name

    async def health(self) -> bool:
        if not self._api_key:
            return False
        url = f"{self._base_url}/health"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(url, headers=headers)
                return res.status_code == 200
        except Exception:
            return False

    async def decide(self, request: SystemOneRequest) -> SystemOneResponse:
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

        endpoint = f"{self._base_url}/v1/systemone"
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
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise SystemOneTimeoutError(
                f"Timeout ({self._timeout}s) em {self.provider_id}",
                provider=self.provider_id,
            ) from exc
        except httpx.RequestError as exc:
            raise SystemOneConnectionError(
                f"Erro de conexão com {self.provider_id} em {self._base_url}: {exc}",
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
        if res.status_code >= 400:
            raise SystemOneValidationError(
                f"Erro HTTP {res.status_code} de {self.provider_id}: {res.text}",
                provider=self.provider_id,
            )

        try:
            data = res.json()
        except Exception as exc:
            raise SystemOneValidationError(
                f"Resposta de {self.provider_id} não é JSON válido",
                provider=self.provider_id,
            ) from exc

        raw_answers = data.get("answers")
        if not isinstance(raw_answers, dict):
            raise SystemOneValidationError(
                f"Resposta inválida de {self.provider_id}: 'answers' ausente ou não é objeto",
                provider=self.provider_id,
            )

        answers: dict[str, SystemOneAnswer] = {}
        for q_id, q_spec in request.questions.items():
            if q_id not in raw_answers:
                raise SystemOneValidationError(
                    f"Resposta de {self.provider_id} não contém resposta para '{q_id}'",
                    provider=self.provider_id,
                )
            ans_raw = raw_answers[q_id]
            if not isinstance(ans_raw, dict):
                raise SystemOneValidationError(
                    f"Resposta para '{q_id}' deve ser um objeto JSON",
                    provider=self.provider_id,
                )

            q_type = q_spec.type
            confidence = ans_raw.get("confidence")
            if confidence is not None:
                confidence = float(confidence)

            if q_type == "noul":
                prob = ans_raw.get("probability")
                if prob is None:
                    raise SystemOneValidationError(
                        f"Resposta Noul para '{q_id}' ausente do campo 'probability'",
                        provider=self.provider_id,
                    )
                answers[q_id] = NoulAnswer(
                    probability=float(prob),
                    confidence=confidence,
                )
            elif q_type == "choice":
                selection = ans_raw.get("selection")
                if not selection or not isinstance(selection, str):
                    raise SystemOneValidationError(
                        f"Resposta Choice para '{q_id}' inválida ou ausente",
                        provider=self.provider_id,
                    )
                if (
                    isinstance(q_spec, ChoiceQuestion)
                    and selection not in q_spec.options
                ):
                    raise SystemOneValidationError(
                        f"Seleção '{selection}' para '{q_id}' não pertence às opções: {q_spec.options}",
                        provider=self.provider_id,
                    )
                dist = ans_raw.get("distribution")
                answers[q_id] = ChoiceAnswer(
                    selection=selection,
                    distribution=dist,
                    confidence=confidence,
                )
            elif q_type == "score":
                score = ans_raw.get("score")
                if score is None:
                    raise SystemOneValidationError(
                        f"Resposta Score para '{q_id}' ausente",
                        provider=self.provider_id,
                    )
                answers[q_id] = ScoreAnswer(
                    score=float(score),
                    confidence=confidence,
                )

        usage_raw = data.get("usage", {})
        usage = SystemOneUsage(
            prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
            completion_tokens=int(usage_raw.get("completion_tokens", 0)),
            total_tokens=int(usage_raw.get("total_tokens", 0)),
        )

        metadata = SystemOneMetadata(
            provider=self.provider_id,
            model=request.model,
            pattern_version="v1",
            latency_ms=elapsed_ms,
            usage=usage,
        )

        return SystemOneResponse(
            answers=answers,
            metadata=metadata,
            raw_payload=data,
        )


class ClefApiAdapter(BaseSystemOneApiAdapter):
    """Adaptador para Clef via API remota (Cloudflare Workers AI / Clef Cloud)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.cloudflare.com/client/v4/accounts/systemone",
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(base_url, api_key, provider_name="clef-api", timeout=timeout)

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_noul=True,
            supports_choice=True,
            supports_score=True,
            supports_image=True,  # Clef e Clef-Flash suportam imagens e entrada multimodal
            supports_video=False,
            supported_models=["@cf/cloudflare/clef", "@cf/cloudflare/clef-flash"],
        )


class JevApiAdapter(BaseSystemOneApiAdapter):
    """Adaptador para JEV via API remota (JEV Cloud)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.jev.ai",
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(base_url, api_key, provider_name="jev-api", timeout=timeout)

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_noul=True,
            supports_choice=True,
            supports_score=True,
            supports_image=True,  # JEV suporta visão multimodal conforme especificação
            supports_video=False,
            supported_models=["jev-1", "jev-fast"],
        )
