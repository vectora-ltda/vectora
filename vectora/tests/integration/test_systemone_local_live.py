"""Contratos live dos runtimes locais usados pelo SystemOne.

Os testes são opt-in. Ollama valida o contrato generativo ``/api/chat``;
Laya valida decisões tipadas em ``/v1/systemone``. Laya não é enviado ao
Ollama porque seus pesos/servidor não são um modelo Ollama.
"""

from __future__ import annotations

import os
from typing import Final

import httpx
import pytest

from backend.llm.systemone.laya_local import LayaLocalAdapter
from backend.llm.systemone.types import ChoiceQuestion, SystemOneRequest

pytestmark = [pytest.mark.local_llm, pytest.mark.asyncio, pytest.mark.live]

_OLLAMA_URL: Final[str | None] = os.environ.get("VECTORA_OLLAMA_BASE_URL")
_OLLAMA_MODEL: Final[str] = os.environ.get("VECTORA_OLLAMA_MODEL", "qwen2.5:0.5b")
_LAYA_URL: Final[str | None] = os.environ.get("VECTORA_LAYA_BASE_URL")
_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(60.0, connect=10.0)


@pytest.mark.skipif(
    not _OLLAMA_URL,
    reason="VECTORA_OLLAMA_BASE_URL não aponta para um Ollama local real",
)
async def test_ollama_local_chat_contract() -> None:
    """Confirma readiness, descoberta e inferência no Ollama local."""

    assert _OLLAMA_URL is not None
    base_url = _OLLAMA_URL.rstrip("/")
    async with httpx.AsyncClient(base_url=base_url, timeout=_TIMEOUT) as client:
        version = await client.get("/api/version")
        assert version.status_code == 200, version.text
        tags = await client.get("/api/tags")
        assert tags.status_code == 200, tags.text
        names = {item.get("name") for item in tags.json().get("models", [])}
        assert _OLLAMA_MODEL in names, f"Modelo {_OLLAMA_MODEL!r} não está em /api/tags"
        response = await client.post(
            "/api/chat",
            json={
                "model": _OLLAMA_MODEL,
                "messages": [{"role": "user", "content": "Responda somente OK."}],
                "stream": False,
                "options": {"temperature": 0, "num_predict": 16},
            },
        )
        assert response.status_code == 200, response.text
        content = response.json().get("message", {}).get("content", "")
        assert isinstance(content, str) and content.strip()


@pytest.mark.skipif(
    not _LAYA_URL,
    reason="VECTORA_LAYA_BASE_URL não aponta para um laya-serve local real",
)
async def test_laya_local_systemone_contract() -> None:
    """Confirma decisão tipada no runtime Laya, separado do Ollama."""

    assert _LAYA_URL is not None
    request = SystemOneRequest(
        model="convaiinnovations/laya",
        state="A API está indisponível e o cliente pede reembolso.",
        questions={
            "queue": ChoiceQuestion(
                instructions="Escolha a fila responsável.",
                options=["technical", "billing"],
                criteria={
                    "technical": "bugs, falhas e indisponibilidade",
                    "billing": "cobranças, pagamentos e reembolsos",
                },
            )
        },
    )
    adapter = LayaLocalAdapter(base_url=_LAYA_URL, timeout=60.0)
    assert await adapter.health() is True
    response = await adapter.decide(request)
    answer = response.answers["queue"]
    assert answer.type == "choice"
    assert getattr(answer, "selection", None) in {"technical", "billing"}
