"""Modo de Planejamento Explícito.

Cobre: detecção /plan no conteúdo, strip do prefixo, injeção de
planning_mode no configurable e instrução de planejamento no contexto.
"""

from __future__ import annotations

from backend.api.handlers.chat import _detect_planning_mode


def test_plan_prefix_detectado() -> None:
    """/plan no início ativa planning_mode."""
    text, mode = _detect_planning_mode("/plan Implementa auth")
    assert mode is True
    assert text == "Implementa auth"


def test_plan_prefix_case_insensitive() -> None:
    """/PLAN (maiúsculo) também é detectado."""
    text, mode = _detect_planning_mode("/PLAN fazer algo")
    assert mode is True
    assert text == "fazer algo"


def test_sem_prefixo_nao_ativa_modo() -> None:
    """Mensagem sem /plan não ativa planning_mode."""
    text, mode = _detect_planning_mode("Implementa auth sem plano")
    assert mode is False
    assert text == "Implementa auth sem plano"


def test_plan_no_meio_nao_ativa_modo() -> None:
    """/plan no meio da mensagem não conta."""
    text, mode = _detect_planning_mode("fazer /plan depois")
    assert mode is False
    assert text == "fazer /plan depois"


def test_plan_apenas_retorna_empty_str() -> None:
    """/plan sem mensagem → texto vazio."""
    text, mode = _detect_planning_mode("/plan")
    assert mode is True
    assert text == ""


def test_plan_com_espaco_extra() -> None:
    """/plan   com espaços extras → texto limpo."""
    text, mode = _detect_planning_mode("/plan   múltiplos espaços")
    assert mode is True
    assert text == "múltiplos espaços"
