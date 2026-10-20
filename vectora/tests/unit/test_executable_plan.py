"""Testes unitários para planos executáveis com step_id e quality gates."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.engine.conversation_loop import _parse_todos_result
from backend.persistence.native.agent_run import AgentRun
from backend.services.verification.engine import VerificationResult
from backend.tools.context import ToolContext
from backend.tools.planning import TodoItemArg, write_todos


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id="user1", thread_id="thread-plan-1")


@pytest.mark.asyncio
async def test_write_todos_includes_step_id_and_quality_gate(ctx):
    todos = [
        TodoItemArg(
            content="Instalar dependências",
            status="completed",
            step_id="step-1",
            quality_gate="gate-install",
        ),
        TodoItemArg(
            content="Rodar testes",
            status="in_progress",
            step_id="step-2",
        ),
        TodoItemArg(
            content="Deploy",
            status="pending",
        ),
    ]

    res = await write_todos(todos, ctx)
    parsed = json.loads(res)

    assert len(parsed) == 3
    assert parsed[0]["step_id"] == "step-1"
    assert parsed[0]["quality_gate"] == "gate-install"
    assert parsed[0]["status"] == "completed"

    assert parsed[1]["step_id"] == "step-2"
    assert "quality_gate" not in parsed[1]
    assert parsed[1]["status"] == "in_progress"

    assert "step_id" not in parsed[2]
    assert parsed[2]["status"] == "pending"


def test_parse_todos_result_with_step_id_and_quality_gate():
    raw_json = json.dumps(
        [
            {
                "content": "Compilar",
                "status": "completed",
                "step_id": "step-comp",
                "quality_gate": "gate-build",
            },
            {
                "content": "Validar",
                "status": "in_progress",
                "step_id": "step-val",
            },
        ]
    )

    items = _parse_todos_result(raw_json)
    assert items is not None
    assert len(items) == 2
    assert items[0].step_id == "step-comp"
    assert items[0].quality_gate == "gate-build"
    assert items[0].status == "completed"

    assert items[1].step_id == "step-val"
    assert items[1].quality_gate is None
    assert items[1].status == "in_progress"
