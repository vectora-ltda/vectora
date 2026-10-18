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

_DEFAULT_LLAMACPP_BASE_URL = "http://127.0.0.1:8080"
_DEFAULT_TIMEOUT_SECONDS = 30.0


class ClefLocalAdapter(SystemOneProvider):
    """Adaptador local Clef sobre o endpoint `POST /v1/systemone` do llama.cpp."""

    def __init__(
        self,
        base_url: str = _DEFAULT_LLAMACPP_BASE_URL,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    @property
    def provider_id(self) -> str:
        return "clef-local"

    async def capabilities(self) -> ProviderCapabilities:
        """Capacidades declaradas para Clef local (texto, noul, choice, score; sem mídias por padrão)."""
        return ProviderCapabilities(
            supports_noul=True,
            supports_choice=True,
            supports_score=True,
            supports_image=False,
            supports_video=False,
            supported_models=["clef", "clef-flash", "default"],
        )

    async def health(self) -> bool:
        """Verifica a saúde do servidor llama.cpp local."""
        url = f"{self._base_url}/health"
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
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

        # Serializa as perguntas de acordo com a especificação SystemOne do llama.cpp
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
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise SystemOneTimeoutError(
                f"Tempo esgotado ({self._timeout}s) ao chamar llama.cpp local",
                provider=self.provider_id,
            ) from exc
        except httpx.RequestError as exc:
            raise SystemOneConnectionError(
                f"Falha de conexão com llama.cpp em {self._base_url}: {exc}",
                provider=self.provider_id,
            ) from exc

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        if res.status_code in (401, 403):
            raise SystemOneAuthenticationError(
                "Autenticação rejeitada pelo endpoint llama.cpp",
                provider=self.provider_id,
            )
        if res.status_code == 429:
            raise SystemOneRateLimitError(
                "Limite de requisições excedido no endpoint llama.cpp",
                provider=self.provider_id,
            )
        if res.status_code >= 400:
            raise SystemOneValidationError(
                f"Endpoint llama.cpp retornou erro HTTP {res.status_code}: {res.text}",
                provider=self.provider_id,
            )

        try:
            data = res.json()
        except Exception as exc:
            raise SystemOneValidationError(
                "Resposta do llama.cpp não contém JSON válido",
                provider=self.provider_id,
            ) from exc

        raw_answers = data.get("answers")
        if not isinstance(raw_answers, dict):
            raise SystemOneValidationError(
                "Resposta inválida do llama.cpp: campo 'answers' ausente ou não é um objeto",
                provider=self.provider_id,
            )

        answers: dict[str, SystemOneAnswer] = {}
        for q_id, q_spec in request.questions.items():
            if q_id not in raw_answers:
                raise SystemOneValidationError(
                    f"Resposta do llama.cpp incompleta: pergunta '{q_id}' não respondida",
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
                        f"Seleção '{selection}' para '{q_id}' não pertence às opções permitidas: {q_spec.options}",
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
