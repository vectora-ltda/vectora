"""Contratos live dos runtimes locais usados pelo SystemOne.

Os testes são opt-in. Ollama valida o contrato generativo ``/api/chat``;
Laya valida decisões tipadas em ``/v1/systemone``. Laya não é enviado ao
Ollama porque seus pesos/servidor não são um modelo Ollama.
"""

from __future__ import annotations

import json
import os
from typing import Final

import httpx
import pytest

from backend.llm.systemone.laya_local import LayaLocalAdapter
from backend.llm.systemone.types import (
    ChoiceQuestion,
    NoulQuestion,
    ScoreQuestion,
    SystemOneRequest,
)

pytestmark = [pytest.mark.local_llm, pytest.mark.asyncio, pytest.mark.live]

_OLLAMA_URL: Final[str | None] = os.environ.get("VECTORA_OLLAMA_BASE_URL")
_OLLAMA_MODEL: Final[str] = os.environ.get("VECTORA_OLLAMA_MODEL", "qwen2.5:0.5b")
_LAYA_URL: Final[str | None] = os.environ.get("VECTORA_LAYA_BASE_URL")
_LAYA_MODEL: Final[str] = os.environ.get("VECTORA_LAYA_MODEL", "multilingual")
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
        model=_LAYA_MODEL,
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
    adapter = LayaLocalAdapter(base_url=_LAYA_URL, model=_LAYA_MODEL, timeout=300.0)
    assert await adapter.health() is True
    response = await adapter.decide(request)
    answer = response.answers["queue"]
    assert answer.type == "choice"
    assert getattr(answer, "selection", None) in {"technical", "billing"}


@pytest.mark.skipif(not _OLLAMA_URL, reason="Ollama local ausente")
async def test_ollama_structured_decision_contract() -> None:
    """Valida decisão estruturada sem confundir texto livre com decisão."""
    assert _OLLAMA_URL is not None
    schema = {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["technical", "billing"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
        },
        "required": ["decision", "confidence", "reason"],
    }
    async with httpx.AsyncClient(
        base_url=_OLLAMA_URL.rstrip("/"), timeout=_TIMEOUT
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "model": _OLLAMA_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": "Classifique como technical ou billing: API indisponível e cliente pede reembolso. Retorne somente JSON.",
                    }
                ],
                "format": schema,
                "stream": False,
                "options": {"temperature": 0, "num_predict": 96},
            },
        )
        assert response.status_code == 200, response.text
        decision = json.loads(response.json().get("message", {}).get("content", ""))
        assert decision["decision"] in {"technical", "billing"}
        assert 0 <= float(decision["confidence"]) <= 1
        assert isinstance(decision["reason"], str) and decision["reason"].strip()


@pytest.mark.skipif(not _LAYA_URL, reason="Laya local ausente")
async def test_laya_returns_all_typed_decision_kinds() -> None:
    """Exercita Noul, Choice e Score em uma chamada."""
    assert _LAYA_URL is not None
    request = SystemOneRequest(
        model=_LAYA_MODEL,
        state="API indisponível; cobrança duplicada; cliente ameaça cancelar.",
        questions={
            "queue": ChoiceQuestion(
                instructions="Escolha a fila.",
                options=["technical", "billing"],
                criteria={
                    "technical": "falha de API",
                    "billing": "cobrança e reembolso",
                },
            ),
            "severity": ScoreQuestion(
                criterion="Avalie a severidade",
                min_score=0,
                max_score=2,
                levels=["baixa", "média", "alta"],
            ),
            "cancel": NoulQuestion(criterion="O cliente ameaça cancelar?"),
        },
    )
    response = await LayaLocalAdapter(
        base_url=_LAYA_URL, model=_LAYA_MODEL, timeout=300.0
    ).decide(request)
    assert set(response.answers) == set(request.questions)
    assert response.answers["queue"].type == "choice"
    assert response.answers["severity"].type == "score"
    assert response.answers["cancel"].type == "noul"
    for answer in response.answers.values():
        if answer.confidence is not None:
            assert 0 <= answer.confidence <= 1
