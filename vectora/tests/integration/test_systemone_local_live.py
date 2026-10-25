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
from pydantic import BaseModel

from backend.llm.ollama.chat_client import OllamaChatClient
from backend.llm.ollama.client import OllamaClient
from backend.llm.systemone.laya_local import LayaLocalAdapter
from backend.llm.systemone.types import (
    ChoiceQuestion,
    NoulQuestion,
    ScoreQuestion,
    SystemOneRequest,
)
from backend.tools.registry import ToolExtras, ToolSpec
from backend.vtypes.message import ContentBlock, MessageRole, VMessage

pytestmark = [pytest.mark.local_llm, pytest.mark.asyncio, pytest.mark.live]

_OLLAMA_URL: Final[str | None] = os.environ.get("VECTORA_OLLAMA_BASE_URL")
_OLLAMA_MODEL: Final[str] = os.environ.get("VECTORA_OLLAMA_MODEL", "qwen2.5:0.5b")
_OLLAMA_TOOLS_MODEL: Final[str | None] = os.environ.get("VECTORA_OLLAMA_TOOLS_MODEL")
_LAYA_URL: Final[str | None] = os.environ.get("VECTORA_LAYA_BASE_URL")
_LAYA_MODEL: Final[str] = os.environ.get("VECTORA_LAYA_MODEL", "multilingual")
_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(60.0, connect=10.0)


class _ToolArgs(BaseModel):
    path: str


async def _read_fixture(**_: object) -> str:
    """Executa a operação read fixture."""
    return "fixture"


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
    not _OLLAMA_URL,
    reason="VECTORA_OLLAMA_BASE_URL não aponta para um Ollama local real",
)
async def test_ollama_local_preserves_vectora_identity() -> None:
    """Regressão contra respostas truncadas que ignoram a identidade do agente.

    Readiness e HTTP 200 não provam que o prompt de sistema chegou ao modelo.
    Esta prova usa o mesmo endpoint local, exige a identidade declarada e rejeita
    o marcador corrompido observado nos testes manuais (``sável!``).
    """

    assert _OLLAMA_URL is not None
    async with httpx.AsyncClient(
        base_url=_OLLAMA_URL.rstrip("/"), timeout=_TIMEOUT
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "model": _OLLAMA_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Você é Vectora, uma assistente de engenharia. "
                            "Quando perguntarem sua identidade, responda "
                            "explicitamente que você é Vectora."
                        ),
                    },
                    {"role": "user", "content": "Quem é você?"},
                ],
                "stream": False,
                "options": {"temperature": 0, "num_predict": 64},
            },
        )
        assert response.status_code == 200, response.text
        content = response.json().get("message", {}).get("content", "")
        assert isinstance(content, str) and content.strip()
        normalized = " ".join(content.casefold().split())
        assert normalized not in {"sável", "sável!"}
        assert "vectora" in normalized


@pytest.mark.skipif(
    not _OLLAMA_URL or not _OLLAMA_TOOLS_MODEL,
    reason="configure VECTORA_OLLAMA_BASE_URL and VECTORA_OLLAMA_TOOLS_MODEL",
)
async def test_ollama_chat_client_executes_a_real_structured_tool_call() -> None:
    """Prova o caminho do cliente nativo, além de um POST HTTP isolado.

    O modelo de tools é separado do modelo textual leve para que um 200 sem
    ``tool_calls`` nunca seja tratado como prova de function calling.
    """

    assert _OLLAMA_URL is not None
    assert _OLLAMA_TOOLS_MODEL is not None
    tool = ToolSpec(
        name="file_read",
        description="Read one file from the workspace.",
        args_model=_ToolArgs,
        handler=_read_fixture,
        extras=ToolExtras(),
        needs_ctx=False,
    )
    client = OllamaClient(base_url=_OLLAMA_URL, timeout_s=180.0)
    try:
        response = await OllamaChatClient(_OLLAMA_TOOLS_MODEL, client).agenerate(
            [
                VMessage(
                    role=MessageRole.SYSTEM,
                    content=[
                        ContentBlock(
                            kind="text",
                            text="Você é Vectora. Use file_read para ler arquivos.",
                        )
                    ],
                ),
                VMessage(
                    role=MessageRole.USER,
                    content=[
                        ContentBlock(
                            kind="text",
                            text=(
                                "Chame file_read com path '/workspace/prova.txt'. "
                                "Faça somente a chamada da ferramenta."
                            ),
                        )
                    ],
                ),
            ],
            tools=[tool],
            temperature=0,
            max_tokens=96,
        )
    finally:
        await client.aclose()
    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "file_read"
    assert response.tool_calls[0].args == {"path": "/workspace/prova.txt"}


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
