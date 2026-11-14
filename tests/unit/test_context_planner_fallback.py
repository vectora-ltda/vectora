"""Planejamento de contexto que nunca derruba o turno (compactação de reserva)."""

from __future__ import annotations

import logging

import pytest

from backend.services import context_planner
from backend.services.context_compaction import _message_tokens
from backend.services.context_planner import (
    PlannedContext,
    plan_context_with_fallback,
)
from backend.vtypes.message import MessageRole, VMessage, text_message


def _conversa() -> list[VMessage]:
    return [
        text_message(MessageRole.SYSTEM, "regras do agente"),
        text_message(MessageRole.USER, "objetivo antigo " * 40),
        text_message(MessageRole.ASSISTANT, "resposta antiga " * 40),
        text_message(MessageRole.USER, "objetivo atual"),
    ]


def _planner_quebrado(monkeypatch: pytest.MonkeyPatch) -> None:
    def _falha(*_args, **_kwargs):
        raise RuntimeError("memória indisponível")

    monkeypatch.setattr(context_planner, "plan_context", _falha)


def test_sem_falha_devolve_o_resultado_do_planejador() -> None:
    mensagens = _conversa()

    planejado = plan_context_with_fallback(mensagens, max_tokens=100_000)

    assert isinstance(planejado, PlannedContext)
    assert [m.text() for m in planejado.messages][-1] == "objetivo atual"
    assert "reserva" not in planejado.manifest.blocks[-1].reason


def test_falha_do_planejador_nao_derruba_e_usa_a_compactacao_deterministica(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _planner_quebrado(monkeypatch)

    with caplog.at_level(logging.ERROR):
        planejado = plan_context_with_fallback(_conversa(), max_tokens=60)

    assert planejado.messages[0].role is MessageRole.SYSTEM
    assert planejado.messages[-1].text() == "objetivo atual"
    assert any("Context compacted" in m.text() for m in planejado.messages)
    assert any("determinística de reserva" in r.getMessage() for r in caplog.records)


def test_o_manifesto_registra_que_foi_o_caminho_de_reserva(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _planner_quebrado(monkeypatch)

    planejado = plan_context_with_fallback(_conversa(), max_tokens=60)

    (bloco,) = planejado.manifest.blocks
    assert bloco.domain == "short_term"
    assert "indisponível" in bloco.reason
    assert planejado.manifest.total_tokens == bloco.token_count
    assert bloco.token_count == sum(_message_tokens(m) for m in planejado.messages)


def test_reserva_respeita_a_flag_de_compactacao_desligada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _planner_quebrado(monkeypatch)
    mensagens = _conversa()

    planejado = plan_context_with_fallback(mensagens, max_tokens=1, enabled=False)

    assert planejado.messages == mensagens


def test_reserva_mantem_as_instrucoes_do_sistema_mesmo_sem_orcamento(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _planner_quebrado(monkeypatch)

    planejado = plan_context_with_fallback(_conversa(), max_tokens=2)

    assert planejado.messages[0].role is MessageRole.SYSTEM
    assert planejado.messages[0].text() == "regras do agente"


def test_reserva_com_historico_curto_devolve_tudo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _planner_quebrado(monkeypatch)
    mensagens = [text_message(MessageRole.USER, "oi")]

    planejado = plan_context_with_fallback(mensagens, max_tokens=50)

    assert [m.text() for m in planejado.messages] == ["oi"]


def test_erro_de_programacao_no_planejador_tambem_cai_na_reserva(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _falha(*_a, **_k):
        raise KeyError("campo")

    monkeypatch.setattr(context_planner, "plan_context", _falha)

    planejado = plan_context_with_fallback(_conversa(), max_tokens=60)

    assert planejado.messages[-1].text() == "objetivo atual"
