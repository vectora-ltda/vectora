"""Adapter para o servidor local ``laya-serve``.

Laya usa o mesmo endpoint nominal do Jev, mas o schema de perguntas e de
respostas é próprio: ``criteria``/``choice``/``probabilities``. A conversão
fica isolada aqui para que os consumidores do SystemOne continuem usando o
contrato canônico do Vectora.
"""

from __future__ import annotations

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

_DEFAULT_TIMEOUT_SECONDS = 30.0


class LayaLocalAdapter(SystemOneProvider):
    """Provider SystemOne para ``laya-serve`` (não é um provider de chat)."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        model: str = "convaiinnovations/laya",
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._external_client = client

    @property
    def provider_id(self) -> str:
        return "laya-local"

    async def aclose(self) -> None:
        return

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_noul=True,
            supports_choice=True,
            supports_score=True,
            supports_image=False,
            supports_video=False,
            supported_models=[
                self._model,
                "english",
                "multilingual",
                "typed-decisions",
            ],
            evidence="documented",
        )

    async def health(self) -> bool:
        headers = self._headers()
        try:
            if self._external_client is not None:
                response = await self._external_client.get(
                    f"{self._base_url}/health", headers=headers
                )
            else:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(
                        f"{self._base_url}/health", headers=headers
                    )
            return response.status_code == 200
        except (httpx.HTTPError, OSError):
            return False

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    @staticmethod
    def _questions(request: SystemOneRequest) -> dict[str, dict[str, Any]]:
        serialized: dict[str, dict[str, Any]] = {}
        for question_id, question in request.questions.items():
            if isinstance(question, NoulQuestion):
                serialized[question_id] = {
                    "type": "noul",
                    "instructions": question.instructions or question.criterion,
                    "criteria": question.criteria
                    or {
                        "true": question.criterion,
                        "false": f"A afirmação não é verdadeira: {question.criterion}",
                    },
                }
            elif isinstance(question, ChoiceQuestion):
                serialized[question_id] = {
                    "type": "choice",
                    "instructions": question.instructions
                    or "Escolha a opção adequada.",
                    "criteria": question.criteria
                    or {option: option for option in question.options},
                }
            elif isinstance(question, ScoreQuestion):
                levels = question.levels or [
                    f"{question.min_score:g}",
                    f"{question.max_score:g}",
                ]
                serialized[question_id] = {
                    "type": "score",
                    "instructions": question.instructions or question.criterion,
                    "criteria": levels,
                }
        return serialized

    @staticmethod
    def _normalize_response(data: object, request: SystemOneRequest) -> dict[str, Any]:
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
            raise ValueError("Laya retornou um envelope inválido")
        normalized: dict[str, Any] = {"answers": {}}
        for question_id, question in request.questions.items():
            raw = data["answers"].get(question_id)
            if not isinstance(raw, dict):
                raise ValueError("Laya não retornou todas as respostas")
            confidence = raw.get("answer_confidence", raw.get("confidence"))
            if isinstance(question, NoulQuestion):
                if "noul" not in raw:
                    raise ValueError("Resposta Noul do Laya ausente")
                answer = {"type": "noul", "probability": raw["noul"]}
            elif isinstance(question, ChoiceQuestion):
                if "choice" not in raw:
                    raise ValueError("Resposta Choice do Laya ausente")
                answer = {
                    "type": "choice",
                    "selection": raw["choice"],
                    "distribution": raw.get("probabilities"),
                }
            else:
                if "score" not in raw:
                    raise ValueError("Resposta Score do Laya ausente")
                score = float(raw["score"])
                if question.levels and 0 <= score <= len(question.levels) - 1:
                    span = question.max_score - question.min_score
                    score = question.min_score + span * score / (
                        len(question.levels) - 1
                    )
                answer = {
                    "type": "score",
                    "score": score,
                    "min_score": question.min_score,
                    "max_score": question.max_score,
                }
            if confidence is not None:
                answer["confidence"] = confidence
            normalized["answers"][question_id] = answer

        usage = data.get("usage")
        if isinstance(usage, dict):
            prompt_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
            completion_tokens = usage.get(
                "output_tokens", usage.get("completion_tokens", 0)
            )
            normalized["usage"] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": usage.get(
                    "total_tokens", prompt_tokens + completion_tokens
                ),
            }
        return normalized

    async def decide(self, request: SystemOneRequest) -> SystemOneResponse:
        if request.media_urls:
            raise SystemOneCapabilityError(
                "Laya local aceita texto/JSON, mas não imagens ou vídeo.",
                provider=self.provider_id,
            )
        payload: dict[str, Any] = {
            "model": request.model or self._model,
            "state": request.state,
            "questions": self._questions(request),
        }
        start = time.perf_counter()
        try:
            if self._external_client is not None:
                response = await self._external_client.post(
                    f"{self._base_url}/v1/systemone",
                    json=payload,
                    headers=self._headers(),
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        f"{self._base_url}/v1/systemone",
                        json=payload,
                        headers=self._headers(),
                    )
        except httpx.TimeoutException as exc:
            raise SystemOneTimeoutError(
                f"Tempo esgotado ({self._timeout}s) ao chamar Laya local",
                provider=self.provider_id,
            ) from exc
        except httpx.RequestError as exc:
            raise SystemOneConnectionError(
                "systemone_connection_error", provider=self.provider_id
            ) from exc
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if response.status_code in (401, 403):
            raise SystemOneAuthenticationError(
                "Autenticação rejeitada pelo Laya local", provider=self.provider_id
            )
        if response.status_code == 429:
            raise SystemOneRateLimitError(
                "Limite de requisições excedido no Laya local",
                provider=self.provider_id,
            )
        if response.status_code >= 500:
            raise SystemOneConnectionError(
                f"Servidor Laya retornou HTTP {response.status_code}",
                provider=self.provider_id,
            )
        if response.status_code >= 400:
            raise SystemOneValidationError(
                f"Laya rejeitou a requisição (HTTP {response.status_code})",
                provider=self.provider_id,
            )
        try:
            normalized = self._normalize_response(response.json(), request)
        except (TypeError, ValueError, KeyError) as exc:
            raise SystemOneValidationError(
                "Resposta do Laya não corresponde ao contrato SystemOne",
                provider=self.provider_id,
            ) from exc
        return parse_systemone_response(
            normalized,
            request,
            provider=self.provider_id,
            latency_ms=elapsed_ms,
        )
