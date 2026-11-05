"""`HITLEvent.pre_approved` dentro de `stream_engine_events`.

O ponto crítico: `pre_approved` é só uma anotação no evento SSE. A pausa do
motor nativo (`stopped_reason == "interrupted"`) já aconteceu antes deste
código rodar — nada aqui decide se a tool executa.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from backend.api.native_stream import stream_engine_events
from backend.engine.stream_events import HitlRequested


def _parse(sse: str) -> dict:
    assert sse.startswith("data: ")
    return json.loads(sse[len("data: ") :].strip())


def _hitl_run(tool_name: str, args: dict):
    """``run`` sintético: emite um único ``HitlRequested`` e pausa
    (``stopped_reason="interrupted"``), como o motor nativo faria ao
    encontrar uma tool que exige aprovação."""

    async def run(on_event):
        await on_event(
            HitlRequested(
                tool_name=tool_name,
                args_json=json.dumps(args),
                interrupt_id="i1",
            )
        )
        return "interrupted"

    return run


@pytest.fixture(autouse=True)
def _isolated(_no_thread_persistence):
    pass


@pytest.mark.asyncio
async def test_pre_approved_reflete_o_avaliador(monkeypatch):
    async def _sempre_true(*_a, **_k):
        return True

    monkeypatch.setattr("backend.api.native_stream._pre_approved", _sempre_true)

    run = _hitl_run("terminal", {"command": "git status"})
    out = [_parse(s) async for s in stream_engine_events(run, thread_id="tid")]

    hitl = next(e for e in out if e["type"] == "hitl")
    assert hitl["pre_approved"] is True


@pytest.mark.asyncio
async def test_falha_no_avaliador_nao_derruba_o_stream(monkeypatch):
    """Erro/borda: `_pre_approved` já degrada sozinho, mas se algo escapar
    (bug futuro) o stream ainda não pode quebrar por causa de uma anotação
    cosmética — HITL é a parte que importa."""
    quebrado = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("backend.api.native_stream._pre_approved", quebrado)

    run = _hitl_run("terminal", {"command": "git status"})
    out = [_parse(s) async for s in stream_engine_events(run, thread_id="tid")]

    hitl = next(e for e in out if e["type"] == "hitl")
    assert hitl["pre_approved"] is False


@pytest.mark.asyncio
async def test_pre_approved_nao_afeta_a_emissao_do_evento_hitl():
    """Com ou sem pré-aprovação, o evento `hitl` sempre é emitido — a pausa
    já aconteceu no motor nativo antes disso."""
    run = _hitl_run("file_write", {"path": "x.py"})
    out = [_parse(s) async for s in stream_engine_events(run, thread_id="tid")]

    tipos = [e["type"] for e in out]
    assert "hitl" in tipos
