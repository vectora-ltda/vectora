"""Testes para backend/scheduling/subagent_runner.py.

Cobre: resolução de spec por SOUL e paridade entre `SUBAGENT_TYPES` (literal
estático, evita ciclo de import) e o catálogo real. A montagem do agente
isolado por SOUL (execução agendada, `trigger_config.subagent_type`) é
responsabilidade de `backend/scheduling/background_tasks.py::run_task`
(motor nativo) — coberta em `tests/unit/test_services_background.py`.
"""

from __future__ import annotations

import pytest

from backend.scheduling.subagent_runner import SUBAGENT_TYPES, _spec_for


def test_subagent_types_bate_com_o_catalogo_real():
    """SUBAGENT_TYPES é um literal estático (evita ciclo de import
    nodes.tools -> background -> subagent_runner -> souls -> nodes.tools) —
    esse teste é o que garante que ele nunca fica desatualizado."""
    from backend.agents.souls import SOUL_CATALOG

    assert set(SUBAGENT_TYPES) == set(SOUL_CATALOG)
    assert "coder" in SUBAGENT_TYPES
    assert "search" in SUBAGENT_TYPES


@pytest.mark.parametrize("nome", list(SUBAGENT_TYPES))
def test_spec_for_retorna_a_soul_certa_do_catalogo_pra_todas_as_10(nome: str):
    """Gap real (revisão de 2026-08-30): só `coder`/`search` provavam a
    implementação de `_spec_for` — um bug de digitação em qualquer uma das
    outras 8 SOULs (ex.: `SOUL_CATALOG.get(nome.replace("-", "_"))`) passaria
    despercebido, já que o teste de paridade acima só compara os NOMES das
    chaves, não que `_spec_for` resolve cada uma pra entrada certa."""
    from backend.agents.souls import SOUL_CATALOG

    assert _spec_for(nome) is SOUL_CATALOG[nome]


def test_spec_for_tipo_invalido_levanta_value_error():
    with pytest.raises(ValueError, match="subagent_type inválido"):
        _spec_for("orchestrator")
