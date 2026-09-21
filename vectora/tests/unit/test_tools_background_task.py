"""Tests TDD para create_background_task.

Cobre: criação de tarefa via serviço, kind inválido, ausência de sessão,
tipo de trigger inválido e invariante de invalidates metadata.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from backend.tools.background import (
    _capability_token,
    create_background_task,
    delete_background_task,
    get_task_result,
    get_task_status,
    run_background_task_now,
    schedule_subagent_task,
    schedule_task,
    toggle_background_task,
)
from backend.tools.context import ToolContext
from backend.tools.registry import TOOL_REGISTRY


def _ctx(
    thread_id: str = "t1", workspace_id: str = "ws1", user_id: str = "u1"
) -> ToolContext:
    return ToolContext(thread_id=thread_id, workspace_id=workspace_id, user_id=user_id)


def _tool_extras(name: str) -> Any:
    spec = TOOL_REGISTRY.get(name)
    assert spec is not None, f"tool {name!r} não registrada"
    return spec.extras


def _fake_task(task_name: str = "Minha tarefa") -> Any:
    class _FakeTask:
        def __init__(self) -> None:
            self.id = "task-123"
            self.session_id = "t1"
            self.user_id = "u1"
            self.name = task_name
            self.kind = "routine"
            self.trigger_type = "interval"

        def to_dict(self) -> dict:
            return {"id": self.id, "name": self.name}

    return _FakeTask()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cria_tarefa_rotina() -> None:
    """Tarefa de rotina com cron válido → status: created."""
    with patch(
        "backend.tools.background.background_tasks.create_task",
        new_callable=AsyncMock,
    ) as mock_create:
        mock_create.return_value = _fake_task("Tarefa teste")

        result = json.loads(
            await create_background_task(
                name="Tarefa teste",
                instruction="Verifica logs diariamente",
                kind="routine",
                trigger_type="interval",
                trigger_config={"cron_expr": "0 9 * * *"},
                ctx=_ctx(),
            )
        )

    assert result["status"] == "created"
    assert result["task_id"] == "task-123"


# ---------------------------------------------------------------------------
# Kind inválido
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kind_invalido_retorna_erro() -> None:
    """Kind não reconhecido → status: error, não propaga."""
    result = json.loads(
        await create_background_task(
            name="X",
            instruction="X",
            kind="magic",
            trigger_type="interval",
            trigger_config={},
            ctx=_ctx(),
        )
    )
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Trigger inválido
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_invalido_retorna_erro() -> None:
    """trigger_type desconhecido → status: error."""
    result = json.loads(
        await create_background_task(
            name="X",
            instruction="X",
            kind="routine",
            trigger_type="telepathy",
            trigger_config={},
            ctx=_ctx(),
        )
    )
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Sem sessão no config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sem_sessao_retorna_erro() -> None:
    """Contexto sem thread_id → status: error."""
    result = json.loads(
        await create_background_task(
            name="X",
            instruction="X",
            kind="routine",
            trigger_type="manual",
            trigger_config={},
            ctx=ToolContext(thread_id=""),
        )
    )
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Metadata invariantes
# ---------------------------------------------------------------------------


def test_create_background_task_invalida_tasks() -> None:
    # invalidates inclui "tasks" — a aba que exibe as tarefas em background.
    extras = _tool_extras("create_background_task")
    assert "tasks" in extras.invalidates


def test_create_background_task_e_destrutiva() -> None:
    extras = _tool_extras("create_background_task")
    assert extras.destructive is False


# ---------------------------------------------------------------------------
# schedule_task — linguagem natural pra cron
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_task_parses_natural_language_and_creates_routine() -> None:
    fake = _fake_task("Resumo diário")
    fake.next_run_at = "2026-07-22T09:00:00+00:00"
    with patch(
        "backend.tools.background.background_tasks.create_task",
        new_callable=AsyncMock,
    ) as mock_create:
        mock_create.return_value = fake

        result = json.loads(
            await schedule_task(
                name="Resumo diário",
                instruction="Resuma os commits de hoje",
                when="todo dia às 9h",
                ctx=_ctx(),
            )
        )

        assert result["status"] == "created"
        assert result["cron_expr"] == "0 9 * * *"
        assert result["next_run_at"] == "2026-07-22T09:00:00+00:00"
        mock_create.assert_awaited_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["trigger_type"] == "interval"
        assert call_kwargs["trigger_config"] == {"cron_expr": "0 9 * * *"}
        assert call_kwargs["kind"] == "routine"


@pytest.mark.asyncio
async def test_schedule_task_ambiguous_when_returns_error_without_creating() -> None:
    # Erro/borda: horário que o parser não reconhece nunca vira um
    # agendamento adivinhado — pede pra reformular, e nem chama create_task.
    with patch(
        "backend.tools.background.background_tasks.create_task",
        new_callable=AsyncMock,
    ) as mock_create:
        result = json.loads(
            await schedule_task(
                name="Tarefa vaga",
                instruction="faça algo",
                when="quando der",
                ctx=_ctx(),
            )
        )

        assert result["status"] == "error"
        mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_task_missing_session_returns_error() -> None:
    result = json.loads(
        await schedule_task(
            name="n",
            instruction="i",
            when="todo dia às 9h",
            ctx=ToolContext(thread_id=""),
        )
    )

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# schedule_subagent_task
# ---------------------------------------------------------------------------


def _fake_subagent_task(
    task_id: str = "task-sub-1", next_run_at: str = "2026-07-23T12:00:00+00:00"
) -> Any:
    class _FakeTask:
        def __init__(self) -> None:
            self.id = task_id
            self.next_run_at = next_run_at

    return _FakeTask()


@pytest.mark.asyncio
async def test_schedule_subagent_task_agenda_coder_com_sucesso() -> None:
    with patch(
        "backend.tools.background.background_tasks.create_task",
        new=AsyncMock(return_value=_fake_subagent_task()),
    ) as mock_create:
        result = json.loads(
            await schedule_subagent_task(
                subagent_type="coder",
                description="corrigir o bug do parser",
                when="em 30 minutos",
                ctx=_ctx(),
            )
        )

        assert result["status"] == "created"
        assert result["task_id"] == "task-sub-1"
        assert result["subagent_type"] == "coder"
        assert result["run_at"] == "2026-07-23T12:00:00+00:00"
        _, kwargs = mock_create.call_args
        assert kwargs["trigger_type"] == "once"
        assert kwargs["trigger_config"] == {"subagent_type": "coder"}
        assert kwargs["next_run_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("soul", ["planner", "reviewer"])
async def test_schedule_subagent_task_agenda_qualquer_soul_do_catalogo(
    soul: str,
) -> None:
    """Gap real (revisão de 2026-08-30): só `coder`/`search` (as 2 SOULs
    legadas) tinham teste de agendamento com sucesso — nada provava que o
    agendamento aceita as outras 8, incluindo uma sem
    `needs_worktree_isolation` (`planner`) e uma read-only (`reviewer`)."""
    with patch(
        "backend.tools.background.background_tasks.create_task",
        new=AsyncMock(return_value=_fake_subagent_task()),
    ) as mock_create:
        result = json.loads(
            await schedule_subagent_task(
                subagent_type=soul,
                description="tarefa de teste",
                when="em 30 minutos",
                ctx=_ctx(),
            )
        )

        assert result["status"] == "created"
        assert result["subagent_type"] == soul
        _, kwargs = mock_create.call_args
        assert kwargs["trigger_config"] == {"subagent_type": soul}


@pytest.mark.asyncio
async def test_schedule_subagent_task_tipo_invalido_nao_cria_tarefa() -> None:
    with patch(
        "backend.tools.background.background_tasks.create_task",
        new=AsyncMock(),
    ) as mock_create:
        result = json.loads(
            await schedule_subagent_task(
                subagent_type="orchestrator",
                description="não deveria rodar",
                when="em 10 minutos",
                ctx=_ctx(),
            )
        )

        assert result["status"] == "error"
        assert "subagent_type inválido" in result["error"]
        mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_subagent_task_horario_nao_reconhecido_retorna_erro() -> None:
    with patch(
        "backend.tools.background.background_tasks.create_task",
        new=AsyncMock(),
    ) as mock_create:
        result = json.loads(
            await schedule_subagent_task(
                subagent_type="search",
                description="pesquisar concorrentes",
                when="algum dia desses",
                ctx=_ctx(),
            )
        )

        assert result["status"] == "error"
        mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_subagent_task_missing_session_returns_error() -> None:
    result = json.loads(
        await schedule_subagent_task(
            subagent_type="search",
            description="d",
            when="em 5 minutos",
            ctx=ToolContext(thread_id=""),
        )
    )

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_schedule_subagent_task_devolve_capability_token_valido() -> None:
    with patch(
        "backend.tools.background.background_tasks.create_task",
        new=AsyncMock(return_value=_fake_subagent_task(task_id="task-sub-tok")),
    ):
        result = json.loads(
            await schedule_subagent_task(
                subagent_type="coder",
                description="corrigir bug",
                when="em 30 minutos",
                ctx=_ctx(),
            )
        )

    assert result["capability_token"] == _capability_token("task-sub-tok")


@pytest.mark.asyncio
async def test_schedule_subagent_task_correlation_id_dedupa_sem_criar_duplicata() -> (
    None
):
    existing = _fake_subagent_task(task_id="task-existing")
    existing.trigger_config = {
        "subagent_type": "coder",
        "correlation_id": "corr-1",
    }
    with (
        patch(
            "backend.tools.background.background_tasks.list_tasks",
            new=AsyncMock(return_value=[existing]),
        ),
        patch(
            "backend.tools.background.background_tasks.create_task",
            new=AsyncMock(),
        ) as mock_create,
    ):
        result = json.loads(
            await schedule_subagent_task(
                subagent_type="coder",
                description="corrigir bug de novo (retry)",
                when="em 30 minutos",
                correlation_id="corr-1",
                ctx=_ctx(),
            )
        )

    assert result["task_id"] == "task-existing"
    assert result.get("deduped") is True
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_subagent_task_correlation_id_ausente_nao_tenta_dedupar() -> (
    None
):
    with (
        patch(
            "backend.tools.background.background_tasks.list_tasks",
            new=AsyncMock(),
        ) as mock_list,
        patch(
            "backend.tools.background.background_tasks.create_task",
            new=AsyncMock(return_value=_fake_subagent_task()),
        ),
    ):
        await schedule_subagent_task(
            subagent_type="coder",
            description="sem correlation_id",
            when="em 30 minutos",
            ctx=_ctx(),
        )

    mock_list.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_task_status / get_task_result — capability token
# ---------------------------------------------------------------------------


def _fake_task_status(task_id: str, trigger_config: dict) -> Any:
    class _FakeTask:
        def __init__(self) -> None:
            self.id = task_id
            self.session_id = "t1"
            self.name = "n"
            self.kind = "routine"
            self.enabled = True
            self.last_run_at = None
            self.trigger_config = trigger_config

    return _FakeTask()


@pytest.mark.asyncio
async def test_get_task_status_sem_subagent_type_nao_exige_token() -> None:
    task = _fake_task_status("task-plain", {})
    with (
        patch(
            "backend.tools.background.background_tasks.get_task",
            new=AsyncMock(return_value=task),
        ),
        patch(
            "backend.tools.background.background_tasks.list_runs",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = json.loads(await get_task_status(task_id="task-plain", ctx=_ctx()))

    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_get_task_status_com_subagent_type_exige_token_valido() -> None:
    task = _fake_task_status("task-sub", {"subagent_type": "coder"})
    with (
        patch(
            "backend.tools.background.background_tasks.get_task",
            new=AsyncMock(return_value=task),
        ),
        patch(
            "backend.tools.background.background_tasks.list_runs",
            new=AsyncMock(return_value=[]),
        ),
    ):
        sem_token = json.loads(await get_task_status(task_id="task-sub", ctx=_ctx()))
        token_errado = json.loads(
            await get_task_status(
                task_id="task-sub", capability_token="errado", ctx=_ctx()
            )
        )
        token_certo = json.loads(
            await get_task_status(
                task_id="task-sub",
                capability_token=_capability_token("task-sub"),
                ctx=_ctx(),
            )
        )

    assert sem_token["status"] == "error"
    assert token_errado["status"] == "error"
    assert token_certo["status"] == "ok"


@pytest.mark.asyncio
async def test_get_task_status_token_de_outra_task_e_rejeitado() -> None:
    # Erro/borda: token válido pra uma task não pode autorizar OUTRA task —
    # não é um segredo global reusável.
    task = _fake_task_status("task-sub-b", {"subagent_type": "coder"})
    with (
        patch(
            "backend.tools.background.background_tasks.get_task",
            new=AsyncMock(return_value=task),
        ),
        patch(
            "backend.tools.background.background_tasks.list_runs",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = json.loads(
            await get_task_status(
                task_id="task-sub-b",
                capability_token=_capability_token("task-sub-a"),
                ctx=_ctx(),
            )
        )

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_get_task_result_sem_subagent_type_nao_exige_token() -> None:
    run = {
        "id": "run-1",
        "task_id": "task-plain",
        "status": "success",
        "summary": "ok",
        "run_thread_id": "rt-1",
    }
    task = _fake_task_status("task-plain", {})
    with (
        patch(
            "backend.tools.background.background_tasks._get_run",
            new=AsyncMock(return_value=run),
        ),
        patch(
            "backend.tools.background.background_tasks.get_task",
            new=AsyncMock(return_value=task),
        ),
    ):
        result = json.loads(await get_task_result(run_id="run-1", ctx=_ctx()))

    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_get_task_result_com_subagent_type_exige_token_valido() -> None:
    run = {
        "id": "run-2",
        "task_id": "task-sub",
        "status": "success",
        "summary": "ok",
        "run_thread_id": "rt-2",
    }
    task = _fake_task_status("task-sub", {"subagent_type": "coder"})
    with (
        patch(
            "backend.tools.background.background_tasks._get_run",
            new=AsyncMock(return_value=run),
        ),
        patch(
            "backend.tools.background.background_tasks.get_task",
            new=AsyncMock(return_value=task),
        ),
    ):
        sem_token = json.loads(await get_task_result(run_id="run-2", ctx=_ctx()))
        token_certo = json.loads(
            await get_task_result(
                run_id="run-2",
                capability_token=_capability_token("task-sub"),
                ctx=_ctx(),
            )
        )

    assert sem_token["status"] == "error"
    assert token_certo["status"] == "ok"


# ---------------------------------------------------------------------------
# toggle_background_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_desativa_tarefa() -> None:
    fake = _fake_task("Rotina X")
    fake.enabled = False
    with patch(
        "backend.tools.background.background_tasks.update_task",
        new=AsyncMock(return_value=fake),
    ) as mock_update:
        result = json.loads(
            await toggle_background_task(task_id="task-123", enabled=False)
        )

    assert result == {"status": "ok", "task_id": "task-123", "enabled": False}
    mock_update.assert_awaited_once_with("task-123", enabled=False)


@pytest.mark.asyncio
async def test_toggle_tarefa_inexistente_retorna_erro() -> None:
    with patch(
        "backend.tools.background.background_tasks.update_task",
        new=AsyncMock(return_value=None),
    ):
        result = json.loads(
            await toggle_background_task(task_id="nao-existe", enabled=True)
        )

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# delete_background_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_remove_tarefa() -> None:
    with patch(
        "backend.tools.background.background_tasks.delete_task",
        new=AsyncMock(return_value=True),
    ) as mock_delete:
        result = json.loads(await delete_background_task(task_id="task-123"))

    assert result == {"status": "ok", "task_id": "task-123"}
    mock_delete.assert_awaited_once_with("task-123")


@pytest.mark.asyncio
async def test_delete_e_idempotente_ja_removida_nao_lanca() -> None:
    # delete_task retorna False quando a tarefa já não existia — a tool
    # não trata isso como erro (deletar de novo não deveria quebrar o agente).
    with patch(
        "backend.tools.background.background_tasks.delete_task",
        new=AsyncMock(return_value=False),
    ):
        result = json.loads(await delete_background_task(task_id="ja-removida"))

    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# run_background_task_now
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_now_dispara_execucao_em_background() -> None:
    fake = _fake_task("Rotina Y")
    with (
        patch(
            "backend.tools.background.background_tasks.get_task",
            new=AsyncMock(return_value=fake),
        ),
        patch(
            "backend.tools.background.background_tasks.run_task",
            new=AsyncMock(),
        ) as mock_run,
    ):
        result = json.loads(
            await run_background_task_now(task_id="task-123", ctx=_ctx())
        )
        await asyncio.sleep(0)  # deixa o create_task agendado rodar

    assert result == {"status": "queued", "task_id": "task-123"}
    mock_run.assert_awaited_once_with(fake, "manual")


@pytest.mark.asyncio
async def test_run_now_tarefa_inexistente_retorna_erro_sem_disparar() -> None:
    with (
        patch(
            "backend.tools.background.background_tasks.get_task",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "backend.tools.background.background_tasks.run_task",
            new=AsyncMock(),
        ) as mock_run,
    ):
        result = json.loads(
            await run_background_task_now(task_id="nao-existe", ctx=_ctx())
        )

    assert result["status"] == "error"
    mock_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_now_recusa_tarefa_de_outra_sessao() -> None:
    fake = _fake_task("Rotina estrangeira")
    with (
        patch(
            "backend.tools.background.background_tasks.get_task",
            new=AsyncMock(return_value=fake),
        ),
        patch(
            "backend.tools.background.background_tasks.run_task",
            new=AsyncMock(),
        ) as mock_run,
    ):
        result = json.loads(
            await run_background_task_now(
                task_id="task-123", ctx=_ctx(thread_id="outra-sessao", user_id="u2")
            )
        )

    assert result["status"] == "error"
    assert "não pertence" in result["error"]
    mock_run.assert_not_awaited()
