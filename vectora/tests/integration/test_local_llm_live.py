"""Exercita o contrato OpenAI-compatible de um llama.cpp real.

O teste não inicia servidor, não substitui ``httpx`` e não usa mocks. O
workflow ``local-llm`` compila o ``llama-server`` e baixa o modelo Qwen fixado e
expõe ``VECTORA_LOCAL_LLM_BASE_URL`` antes de executar este módulo.
"""

from __future__ import annotations

import json
import os
from typing import Final

import httpx
import pytest

pytestmark = [pytest.mark.local_llm, pytest.mark.asyncio, pytest.mark.live]

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


@pytest.mark.skipif(
    not os.environ.get("VECTORA_LOCAL_LLM_BASE_URL"),
    reason="VECTORA_LOCAL_LLM_BASE_URL não aponta para um servidor local real",
)
async def test_llamacpp_systemone_endpoint_or_clef_adapter() -> None:
    """Exercita o adapter ClefLocalAdapter e o endpoint /v1/systemone no llama.cpp local."""
    from backend.llm.systemone.clef_local import ClefLocalAdapter
    from backend.llm.systemone.types import NoulAnswer, NoulQuestion, SystemOneRequest

    server_base = _BASE_URL.removesuffix("/v1")
    adapter = ClefLocalAdapter(base_url=server_base, timeout=30.0)

    is_healthy = await adapter.health()
    assert is_healthy is True

    caps = await adapter.capabilities()
    assert caps.supports_noul is True
    assert caps.supports_choice is True
    assert caps.supports_score is True

    req = SystemOneRequest(
        model="clef-flash",
        state="O usuário enviou uma mensagem solicitando listagem de diretório.",
        questions={
            "is_file_operation": NoulQuestion(
                criterion="A solicitação envolve leitura de arquivos?"
            )
        },
    )

    async with httpx.AsyncClient(base_url=server_base, timeout=_TIMEOUT) as client:
        res = await client.post("/v1/systemone", json=req.model_dump())
        if res.status_code == 200:
            ans = res.json()["answers"]
            assert "is_file_operation" in ans
            decide_res = await adapter.decide(req)
            assert "is_file_operation" in decide_res.answers
            assert isinstance(decide_res.answers["is_file_operation"], NoulAnswer)
        else:
            assert res.status_code in {404, 405, 501}, res.text
            pytest.skip(f"llama.cpp não expõe /v1/systemone (HTTP {res.status_code})")


@pytest.mark.skipif(
    not os.environ.get("VECTORA_LOCAL_LLM_BASE_URL"), reason="llama.cpp local ausente"
)
async def test_llamacpp_structured_decision_contract() -> None:
    """Valida decisão estruturada no endpoint usado pelo Jev/Clef."""
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=_TIMEOUT) as client:
        models_response = await client.get("/models")
        assert models_response.status_code == 200, models_response.text
        model_id = models_response.json()["data"][0]["id"]
        completion = await client.post(
            "/chat/completions",
            json={
                "model": model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": "Classifique como technical ou billing: API indisponível e cliente pede reembolso. Retorne JSON com decision e confidence.",
                    }
                ],
                "max_tokens": 96,
                "temperature": 0,
                "stream": False,
                "response_format": {"type": "json_object"},
            },
        )
        assert completion.status_code == 200, completion.text
        decision = json.loads(completion.json()["choices"][0]["message"]["content"])
        decision_kind = str(decision["decision"]).strip().lower()
        assert decision_kind in {"technical", "billing"}
        assert 0 <= float(decision["confidence"]) <= 1
