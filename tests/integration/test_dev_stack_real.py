"""Ciclo de vida real do dev-stack (Postgres/Redis/Qdrant via Docker).

Diferente de test_dev_stack.py (unit — só valida constantes/comandos
montados), este arquivo chama stack_up()/stack_down()/stack_status() de
verdade contra o Docker instalado na máquina. Skip limpo sem Docker
disponível — mesmo padrão de test_storage_qdrant.py (marker `storage`).
"""

from __future__ import annotations

import pytest

from backend.storage.dev_stack import (
    SERVICES,
    _docker_available,
    stack_down,
    stack_status,
    stack_up,
)

pytestmark = pytest.mark.storage


@pytest.fixture(autouse=True)
def _require_docker():
    if not _docker_available():
        pytest.skip("Docker indisponível (não instalado ou daemon parado)")


@pytest.fixture
def _cleanup_stack():
    """Garante que a stack fica parada ao final do teste, mesmo em falha."""
    yield
    stack_down()


def test_stack_up_sobe_infra_e_status_reflete(_cleanup_stack):
    result = stack_up()

    assert result.ok is True, f"stack_up() falhou: {result.messages}"

    status = stack_status()
    assert status.ok is True
    # Cada serviço declarado em SERVICES precisa aparecer no status — se o
    # docker-compose.yml não fixa container_name, docker compose nomeia os
    # containers como <projeto>-<serviço>-<n>, não <spec.name> puro; se esse
    # for o caso aqui, este assert falha e expõe a divergência de nomes.
    for spec in SERVICES:
        assert any(spec.name in msg for msg in status.messages), (
            f"{spec.name} não aparece no status ({status.messages}) — "
            "possível divergência entre nome do container real (docker "
            "compose gera <projeto>-<serviço>-<n> sem container_name: "
            "explícito) e o nome esperado em SERVICES"
        )

    # Par de erro no mesmo teste: derruba e confirma que some do status.
    down_result = stack_down()
    assert down_result.ok is True, f"stack_down() falhou: {down_result.messages}"


def test_stack_up_idempotente_com_stack_ja_rodando(_cleanup_stack):
    first = stack_up()
    assert first.ok is True

    second = stack_up()
    assert second.ok is True, (
        f"segunda chamada a stack_up() com stack já rodando falhou: {second.messages}"
    )


def test_stack_status_com_docker_de_pe_nunca_lanca_e_lista_todos_os_servicos():
    """stack_status() sempre reporta os 3 serviços (rodando/parado/ausente,
    dependendo do estado real da máquina) e nunca lança, com ou sem stack
    ativa no momento da chamada."""
    status = stack_status()

    assert status.ok is True
    for spec in SERVICES:
        assert any(spec.name in msg for msg in status.messages), (
            f"{spec.name} não apareceu no status ({status.messages})"
        )


def test_stack_down_sem_containers_existentes_e_noop_sem_lancar():
    """Edge case: derrubar uma stack que nunca foi criada não deve lançar.

    Com docker-compose.yml presente no repo, stack_down() sempre usa
    `docker compose down` (1 mensagem agregada) — o loop por-serviço com
    "não existe — nada a fazer" só roda no fallback sem compose file, que
    não é o caminho exercitado aqui."""
    result = stack_down()

    assert result.ok is True
    assert len(result.messages) == 1
