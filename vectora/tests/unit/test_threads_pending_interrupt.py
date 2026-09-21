"""Testes para GET /threads/{thread_id}/pending-interrupt.

O endpoint reidrata o HITLPanel após reload de página: o interrupt pendente
sobrevive a um restart do backend (checkpointer real), então a rota consulta
o checkpoint diretamente em vez de depender do evento de streaming.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Request

from backend.api.handlers.threads import thread_pending_interrupt


class TestThreadPendingInterrupt:
    @pytest.mark.asyncio
    async def test_no_pending_interrupt_returns_none(self):
        with patch(
            "backend.services.agent_factory.aget_thread_pending_interrupt",
            AsyncMock(return_value=None),
        ):
            result = await thread_pending_interrupt("thread-1")

        assert result.interrupt is None

    @pytest.mark.asyncio
    async def test_pending_interrupt_returns_hitl_event(self):
        pending = {
            "tool_name": "terminal",
            "args": {"command": "rm -rf /tmp/x"},
            "interrupt_id": "intr-1",
        }
        with (
            patch(
                "backend.services.agent_factory.aget_thread_pending_interrupt",
                AsyncMock(return_value=pending),
            ),
            patch(
                "backend.services.smart_approval.evaluate_command",
                AsyncMock(return_value=False),
            ),
        ):
            result = await thread_pending_interrupt("thread-2")

        assert result.interrupt is not None
        assert result.interrupt.tool_name == "terminal"
        assert result.interrupt.interrupt_id == "intr-1"
        assert '"command"' in result.interrupt.args_json

    @pytest.mark.asyncio
    async def test_smart_approval_failure_degrades_to_not_pre_approved(self):
        """Erro/borda: `evaluate_command` indisponível não pode impedir a
        reidratação do card em si — só a anotação de pré-aprovação some."""
        pending = {"tool_name": "git_status", "args": {}, "interrupt_id": "intr-2"}
        with (
            patch(
                "backend.services.agent_factory.aget_thread_pending_interrupt",
                AsyncMock(return_value=pending),
            ),
            patch(
                "backend.services.smart_approval.evaluate_command",
                AsyncMock(side_effect=RuntimeError("indisponível")),
            ),
        ):
            result = await thread_pending_interrupt("thread-3")

        assert result.interrupt is not None
        assert result.interrupt.pre_approved is False

    @pytest.mark.asyncio
    async def test_owner_can_read_pending_interrupt_with_request(self) -> None:
        pending = {
            "tool_name": "terminal",
            "args": {"command": "echo ok"},
            "interrupt_id": "intr-owner",
        }
        request = cast(
            "Request",
            SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id="owner"))),
        )
        with (
            patch(
                "backend.api.handlers.threads._assert_existing_thread_ownership",
                new=AsyncMock(),
            ) as ownership,
            patch(
                "backend.services.agent_factory.aget_thread_pending_interrupt",
                new=AsyncMock(return_value=pending),
            ) as pending_lookup,
            patch(
                "backend.services.smart_approval.evaluate_command",
                new=AsyncMock(return_value=False),
            ),
        ):
            result = await thread_pending_interrupt("thread-owner", request)

        ownership.assert_awaited_once_with("thread-owner", request)
        pending_lookup.assert_awaited_once()
        assert result.interrupt is not None

    @pytest.mark.asyncio
    async def test_outro_usuario_nao_le_pending_interrupt(self) -> None:
        request = cast(
            "Request",
            SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id="intruder"))),
        )
        pending_lookup = AsyncMock()
        with (
            patch(
                "backend.api.handlers.threads._assert_existing_thread_ownership",
                new=AsyncMock(
                    side_effect=HTTPException(
                        status_code=404, detail="Thread não encontrada"
                    )
                ),
            ),
            patch(
                "backend.services.agent_factory.aget_thread_pending_interrupt",
                new=pending_lookup,
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await thread_pending_interrupt("thread-owner", request)

        assert exc_info.value.status_code == 404
        pending_lookup.assert_not_awaited()
