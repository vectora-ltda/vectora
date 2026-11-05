"""Conversas reais, fim a fim, contra um backend Vectora real (subprocesso
``python -m backend.main start``, fixture ``live_backend`` de
``tests/e2e/conftest.py``) — sem mock de LLM, tool nem transporte HTTP.

Sobe o backend como subprocesso e fala HTTP real (``spawned_backend``) em vez
de invocar o grafo in-process — mesmo binário/entrypoint que o Electron/CLI
usam em produção, exercitando `AuthMiddleware`, SSE de verdade, serialização
de eventos. `VECTORA_HOME` isola todo o estado do processo (checkpointer,
workspace registry, safe roots) num diretório temporário — as threads criadas
por estes testes não tocam o `~/.vectora` real do usuário.

Guardado pelo marker ``live`` (só via ``scons tests-live``) e por
``Settings.configured_llm_providers()``/``settings.tavily_api_key``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from backend.settings import settings

pytestmark = pytest.mark.live

requires_google = pytest.mark.skipif(
    "google-genai" not in settings.configured_llm_providers(),
    reason="GOOGLE_API_KEY não configurado em ~/.vectora/.env",
)
requires_tavily = pytest.mark.skipif(
    not settings.tavily_api_key,
    reason="TAVILY_API_KEY não configurado em ~/.vectora/.env",
)


async def _stream_chat(
    base_url: str,
    *,
    content: str,
    thread_id: str = "",
    chat_mode: bool = True,
    workspace_id: str = "",
    timeout_s: float = 120.0,
) -> list[dict[str, Any]]:
    """POST real em ``/vectora.chat.v1.ChatService/StreamChat``, devolve a
    lista de eventos SSE decodificados (``{"type": ..., ...}`` cada um)."""
    payload = {
        "thread_id": thread_id,
        "content": content,
        "config": {"chat_mode": chat_mode, "workspace_id": workspace_id},
    }
    events: list[dict[str, Any]] = []
    async with (
        httpx.AsyncClient(base_url=base_url, timeout=timeout_s) as client,
        client.stream(
            "POST", "/vectora.chat.v1.ChatService/StreamChat", json=payload
        ) as response,
    ):
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            events.append(json.loads(line[len("data: ") :]))
    return events


def _assembled_text(events: list[dict[str, Any]]) -> str:
    return "".join(e.get("content", "") for e in events if e.get("type") == "token")


def _thread_id_of(events: list[dict[str, Any]]) -> str:
    for e in events:
        if e.get("type") == "thread":
            return str(e["thread_id"])
    raise AssertionError(f"nenhum ThreadEvent no stream: {events!r}")


# ---------------------------------------------------------------------------
# Q&A simples
# ---------------------------------------------------------------------------


@requires_google
async def test_qa_simples_real(live_backend: str):
    events = await _stream_chat(
        live_backend, content="Quanto é 15 + 27? Responda só com o número."
    )
    assert any(e.get("type") == "done" for e in events)
    assert not any(e.get("type") == "error" for e in events)
    text = _assembled_text(events)
    assert text.strip()
    assert "42" in text


async def test_qa_content_ausente_erro_422(live_backend: str):
    # Par de erro/borda: payload sem `content` (campo obrigatório do
    # StreamChatRequest) — 422 do FastAPI, sem sequer chamar o LLM.
    async with httpx.AsyncClient(base_url=live_backend, timeout=30.0) as client:
        resp = await client.post(
            "/vectora.chat.v1.ChatService/StreamChat",
            json={"thread_id": "", "config": {"chat_mode": True}},
        )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tool call de busca web real
# ---------------------------------------------------------------------------


@requires_google
@requires_tavily
async def test_tool_call_busca_web_real(live_backend: str):
    events = await _stream_chat(
        live_backend,
        content=(
            "Use a ferramenta de busca web agora mesmo para procurar "
            "'FastAPI framework' e me diga em uma frase o que você encontrou."
        ),
        timeout_s=180.0,
    )
    assert any(e.get("type") == "done" for e in events)
    tool_calls = [e for e in events if e.get("type") == "tool_call"]
    assert any(e.get("tool_name") == "web_search" for e in tool_calls), (
        f"esperava uma chamada real a web_search, eventos: {events!r}"
    )
    tool_results = [e for e in events if e.get("type") == "tool_result"]
    assert any(not e.get("is_error", False) for e in tool_results)
    assert _assembled_text(events).strip()


# ---------------------------------------------------------------------------
# Leitura/escrita de arquivo real
# ---------------------------------------------------------------------------


@requires_google
async def test_leitura_escrita_arquivo_real(live_backend: str, tmp_path):
    workspace_dir = tmp_path / "agent-workspace"
    workspace_dir.mkdir()

    async with httpx.AsyncClient(base_url=live_backend, timeout=30.0) as client:
        resp = await client.post(
            "/vectora.workspace.v1.WorkspaceService/CreateWorkspace",
            json={"path": str(workspace_dir), "trust": True, "git_init": False},
        )
        resp.raise_for_status()
        body = resp.json()
        assert body["status"] == "ok", body
        workspace_id = body["workspace"]["id"]

    try:
        marker = f"vectora-live-test-{uuid.uuid4().hex[:8]}"
        events = await _stream_chat(
            live_backend,
            content=(
                f"Crie um arquivo chamado nota.txt com exatamente o conteúdo "
                f"'{marker}' (sem mais nada) e confirme quando terminar."
            ),
            chat_mode=False,
            workspace_id=workspace_id,
            timeout_s=180.0,
        )
        assert any(e.get("type") == "done" for e in events)
        assert not any(e.get("type") == "error" for e in events)

        written = workspace_dir / "nota.txt"
        assert written.exists(), (
            f"agente não criou nota.txt em {workspace_dir}; eventos: {events!r}"
        )
        assert marker in written.read_text(encoding="utf-8")
    finally:
        # Self-cleanup: mesmo padrão de tmp_git_repo/real_workspace em
        # tests/conftest.py — remove a entrada de teste do registry.
        from backend.workspace.workspace import workspace_registry

        workspace_registry.delete(workspace_id)


# ---------------------------------------------------------------------------
# Multi-turno com contexto
# ---------------------------------------------------------------------------


@requires_google
async def test_multiturno_com_contexto_real(live_backend: str):
    codeword = f"zorblatt-{uuid.uuid4().hex[:6]}"
    first = await _stream_chat(
        live_backend,
        content=(
            f"Minha palavra-código secreta de hoje é '{codeword}'. Apenas "
            "confirme que guardou, não faça mais nada."
        ),
    )
    thread_id = _thread_id_of(first)
    assert any(e.get("type") == "done" for e in first)

    second = await _stream_chat(
        live_backend,
        content="Qual é a minha palavra-código secreta de hoje? Responda só com ela.",
        thread_id=thread_id,
    )
    assert any(e.get("type") == "done" for e in second)
    assert codeword in _assembled_text(second)


# ---------------------------------------------------------------------------
# Erro recuperável — tool com args inválidos não derruba a conversa
# ---------------------------------------------------------------------------


@requires_google
async def test_erro_recuperavel_tool_args_invalidos_real(live_backend: str, tmp_path):
    workspace_dir = tmp_path / "agent-workspace-erro"
    workspace_dir.mkdir()

    async with httpx.AsyncClient(base_url=live_backend, timeout=30.0) as client:
        resp = await client.post(
            "/vectora.workspace.v1.WorkspaceService/CreateWorkspace",
            json={"path": str(workspace_dir), "trust": True, "git_init": False},
        )
        resp.raise_for_status()
        workspace_id = resp.json()["workspace"]["id"]

    try:
        events = await _stream_chat(
            live_backend,
            content=(
                "Leia agora o arquivo 'este-arquivo-definitivamente-nao-existe.txt' "
                "e depois me diga, em uma frase, o que aconteceu."
            ),
            chat_mode=False,
            workspace_id=workspace_id,
            timeout_s=180.0,
        )
        # A tool de leitura falha (arquivo inexistente) — o grafo precisa
        # sobreviver a isso e terminar normalmente, sem propagar como
        # ErrorEvent fatal pro cliente.
        assert any(e.get("type") == "done" for e in events)
        assert not any(e.get("type") == "error" for e in events)
        assert _assembled_text(events).strip()
    finally:
        from backend.workspace.workspace import workspace_registry

        workspace_registry.delete(workspace_id)
