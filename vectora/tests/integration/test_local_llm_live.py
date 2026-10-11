"""Exercita o contrato OpenAI-compatible de um llama.cpp real.

O teste não inicia servidor, não substitui ``httpx`` e não usa mocks. O
workflow ``local-llm`` compila o ``llama-server``, baixa o modelo SmolLM2 e
expõe ``VECTORA_LOCAL_LLM_BASE_URL`` antes de executar este módulo.
"""

from __future__ import annotations

import os
from typing import Final

import httpx
import pytest

pytestmark = [pytest.mark.local_llm, pytest.mark.asyncio]

_BASE_URL: Final[str] = os.environ.get(
    "VECTORA_LOCAL_LLM_BASE_URL", "http://127.0.0.1:18080/v1"
).rstrip("/")
_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(45.0, connect=10.0)


@pytest.mark.skipif(
    not os.environ.get("VECTORA_LOCAL_LLM_BASE_URL"),
    reason="VECTORA_LOCAL_LLM_BASE_URL não aponta para um servidor local real",
)
async def test_llamacpp_real_exposes_model_and_generates_completion() -> None:
    """Confirma descoberta e inferência usando o mesmo endpoint de produção."""

    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=_TIMEOUT) as client:
        health = await client.get(_BASE_URL.removesuffix("/v1") + "/health")
        assert health.status_code == 200, health.text

        models_response = await client.get("/models")
        assert models_response.status_code == 200, models_response.text
        models = models_response.json().get("data", [])
        assert models, "llama.cpp não publicou nenhum modelo em /v1/models"
        model_id = models[0]["id"]

        completion = await client.post(
            "/chat/completions",
            json={
                "model": model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": "Responda somente com a palavra OK.",
                    }
                ],
                "max_tokens": 8,
                "temperature": 0,
                "stream": False,
            },
        )
        assert completion.status_code == 200, completion.text
        payload = completion.json()
        text = payload["choices"][0]["message"]["content"]
        assert isinstance(text, str) and text.strip()
