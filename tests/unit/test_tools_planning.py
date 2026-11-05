"""``write_todos`` (backend/tools/planning.py) — tool nativa de
planejamento. Testa a tool diretamente (via TOOL_REGISTRY.ainvoke,
validação de args incluída) e a tradução do resultado em TodosUpdated
feita por backend/engine/conversation_loop.py.
"""

from __future__ import annotations

import json

import pytest

from backend.tools.context import ToolContext
from backend.tools.planning import write_todos
from backend.tools.registry import TOOL_REGISTRY


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id="alice", thread_id="thread-1")


class TestWriteTodosRegistrado:
    def test_esta_no_tool_registry(self) -> None:
        spec = TOOL_REGISTRY.get("write_todos")
        assert spec is not None
        assert spec.extras.category == "planning"


class TestWriteTodosChamadaDireta:
    @pytest.mark.asyncio
    async def test_lista_completa_vira_json_com_content_e_status(
        self, ctx: ToolContext
    ) -> None:
        spec = TOOL_REGISTRY.get("write_todos")
        assert spec is not None

        resultado = await spec.ainvoke(
            {
                "todos": [
                    {"content": "ler arquivo", "status": "completed"},
                    {"content": "escrever teste", "status": "in_progress"},
                    {"content": "rodar suíte", "status": "pending"},
                ]
            },
            ctx,
        )

        payload = json.loads(resultado)
        assert payload == [
            {"content": "ler arquivo", "status": "completed"},
            {"content": "escrever teste", "status": "in_progress"},
            {"content": "rodar suíte", "status": "pending"},
        ]

    @pytest.mark.asyncio
    async def test_lista_vazia_e_valida(self, ctx: ToolContext) -> None:
        spec = TOOL_REGISTRY.get("write_todos")
        assert spec is not None

        resultado = await spec.ainvoke({"todos": []}, ctx)
        assert json.loads(resultado) == []

    @pytest.mark.asyncio
    async def test_status_fora_da_taxonomia_retorna_erro_tipado_sem_lancar(
        self, ctx: ToolContext
    ) -> None:
        spec = TOOL_REGISTRY.get("write_todos")
        assert spec is not None

        resultado = await spec.ainvoke(
            {"todos": [{"content": "x", "status": "done"}]}, ctx
        )
        assert resultado.startswith("Error: argumentos inválidos")

    @pytest.mark.asyncio
    async def test_funcao_chamada_diretamente_devolve_o_mesmo_json(
        self, ctx: ToolContext
    ) -> None:
        from backend.tools.planning import TodoItemArg

        resultado = await write_todos(
            todos=[TodoItemArg(content="a", status="pending")], ctx=ctx
        )
        assert json.loads(resultado) == [{"content": "a", "status": "pending"}]
