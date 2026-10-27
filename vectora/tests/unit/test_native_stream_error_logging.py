"""Regressão: erro de execução do agente (``run_error`` em
``stream_engine_events``) precisa deixar rastro no log do backend.

Achado ao investigar um relato real de "chat não responde com nenhum
provider": o traceback nunca aparecia no log porque ``run_error`` era só
guardado numa variável e depois classificado (``classify_stream_error``) sem
nenhum ``logger.error``/``logger.exception`` — só o ``except`` mais externo
(que nem sempre é o caminho percorrido) logava de fato. Sem esse log, um erro
real de provider vira um beco sem saída pra diagnosticar.
"""

from __future__ import annotations

import json
import logging
from typing import cast

import pytest

from backend.api.native_stream import stream_engine_events
from backend.engine.stream_events import ErrorSignal, EventSink, MessageBreak
from backend.vtypes.message import VMessage


def _parse(sse: str) -> dict:
    """Executa a operação parse."""
    line = next(x for x in sse.splitlines() if x.startswith("data:"))
    return json.loads(line[len("data:") :].strip())


class TestErroDeExecucaoFicaLogado:
    async def test_run_error_persiste_desfecho_terminal_idempotente(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifica o comportamento de run error persiste desfecho terminal idempotente."""

        class _Store:
            def __init__(self) -> None:
                """Inicializa o estado da instância."""
                self.messages: list[dict[str, object]] = []
                self.ledger_events: dict[str, dict[str, object]] = {}

            async def get_message_id_by_turn_id(
                self, thread_id: str, turn_id: str
            ) -> int | None:
                """Executa a operação get message id by turn id."""
                for message in self.messages:
                    if message["turn_id"] == turn_id:
                        return 1
                return None

            async def get_branch_head_id(self, thread_id: str) -> int:
                """Executa a operação get branch head id."""
                return 7

            async def append_message(
                self, thread_id: str, message: VMessage, **kwargs: object
            ) -> int:
                """Executa a operação append message."""
                self.messages.append({"message": message, **kwargs})
                return 8

        store = _Store()
        from backend.services import agent_factory

        async def _get_store() -> _Store:
            """Executa a operação get store."""
            return store

        monkeypatch.setattr(agent_factory, "get_session_store", _get_store)

        async def _run(on_event: EventSink) -> str:
            """Executa a operação run."""
            raise RuntimeError("provider indisponível")

        first = [
            _parse(sse)
            async for sse in stream_engine_events(
                _run, thread_id="t-terminal", run_id="run-terminal"
            )
        ]
        second = [
            _parse(sse)
            async for sse in stream_engine_events(
                _run, thread_id="t-terminal", run_id="run-terminal"
            )
        ]

        assert any(event["type"] == "error" for event in first)
        assert any(event["type"] == "error" for event in second)
        assert len(store.messages) == 1
        persisted = store.messages[0]
        assert persisted["turn_id"] == "run-terminal:terminal"
        assert persisted["run_id"] == "run-terminal"
        assert cast("VMessage", persisted["message"]).is_error is True

    async def test_run_error_gera_error_event_e_loga_o_traceback_real(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Happy path: ``run`` que lança vira ``ErrorEvent`` no stream SSE.
        Erro/borda (o ponto real desta regressão): o `run_error` original
        precisa aparecer no log, com o traceback, não só a mensagem
        amigável classificada."""
        from backend.services import agent_factory

        store = await agent_factory.get_session_store()
        await store.create_session("t1", user_id="u1")

        async def _run_que_falha(
            on_event: EventSink,
        ) -> str:
            """Executa a operação run que falha."""
            raise RuntimeError("boom: provider explodiu de verdade")

        with caplog.at_level(logging.ERROR, logger="backend.api.native_stream"):
            events = [
                _parse(sse)
                async for sse in stream_engine_events(_run_que_falha, thread_id="t1")
            ]

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["message"]

        assert any(
            "native_stream: erro na execução do agente" in rec.message
            and rec.exc_info is not None
            and "boom: provider explodiu de verdade" in str(rec.exc_info[1])
            for rec in caplog.records
        )

    async def test_evento_de_resposta_vazia_fica_logado_com_codigo(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verifica o comportamento de evento de resposta vazia fica logado com codigo."""
        from backend.services import agent_factory

        store = await agent_factory.get_session_store()
        await store.create_session("t-empty", user_id="u1")

        async def _run_com_resposta_vazia(
            on_event: EventSink,
        ) -> str:
            """Executa a operação run com resposta vazia."""
            await on_event(
                ErrorSignal(
                    code="EMPTY_MODEL_RESPONSE",
                    message="O provedor não retornou texto.",
                )
            )
            await on_event(MessageBreak())
            return "stop"

        with caplog.at_level("WARNING", logger="backend.api.native_stream"):
            events = [
                _parse(sse)
                async for sse in stream_engine_events(
                    _run_com_resposta_vazia, thread_id="t-empty"
                )
            ]

        assert any(
            event["type"] == "error" and event["code"] == "EMPTY_MODEL_RESPONSE"
            for event in events
        )
        record = next(
            record
            for record in caplog.records
            if record.message == "native_stream_event_error"
        )
        assert getattr(record, "code", None) == "EMPTY_MODEL_RESPONSE"
        assert getattr(record, "message_length", 0) > 0

    async def test_empty_model_response_persiste_run_com_falha(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifica o comportamento de empty model response persiste run com falha."""
        updated_statuses: list[dict[str, object]] = []

        class _Store:
            def __init__(self) -> None:
                """Inicializa o estado da instância."""
                self.messages: list[dict[str, object]] = []
                self.ledger_events: dict[str, dict[str, object]] = {}

            async def get_message_id_by_turn_id(
                self, thread_id: str, turn_id: str
            ) -> None:
                """Executa a operação get message id by turn id."""
                return

            async def get_branch_head_id(self, thread_id: str) -> int:
                """Executa a operação get branch head id."""
                return 1

            async def append_message(
                self, thread_id: str, message: VMessage, **kwargs: object
            ) -> int:
                """Executa a operação append message."""
                self.messages.append({"message": message, **kwargs})
                return 2

            async def update_run(self, run_id: str, **kwargs: object) -> None:
                """Executa a operação update run."""
                updated_statuses.append({"type": "legacy", "run_id": run_id, **kwargs})

            async def update_agent_run_status(
                self, run_id: str, **kwargs: object
            ) -> None:
                """Executa a operação update agent run status."""
                updated_statuses.append(
                    {"type": "agent_run", "run_id": run_id, **kwargs}
                )

            async def append_ledger_event(
                self, thread_id: str, **kwargs: object
            ) -> None:
                """Simula a chave única de idempotência do ledger."""
                key = str(kwargs["event_key"])
                self.ledger_events.setdefault(key, {"thread_id": thread_id, **kwargs})

        store = _Store()
        from backend.services import agent_factory

        async def _get_store() -> _Store:
            """Executa a operação get store."""
            return store

        monkeypatch.setattr(agent_factory, "get_session_store", _get_store)

        async def _run_empty(on_event: EventSink) -> str:
            """Executa a operação run empty."""
            await on_event(
                ErrorSignal(
                    code="EMPTY_MODEL_RESPONSE",
                    message="O provedor não retornou texto.",
                )
            )
            await on_event(MessageBreak())
            return "stop"

        _ = [
            _parse(sse)
            async for sse in stream_engine_events(
                _run_empty, thread_id="t-empty-fail", run_id="run-empty-fail"
            )
        ]

        _ = [
            _parse(sse)
            async for sse in stream_engine_events(
                _run_empty, thread_id="t-empty-fail", run_id="run-empty-fail"
            )
        ]

        assert any(
            u.get("type") == "agent_run" and u.get("status") == "failed"
            for u in updated_statuses
        )
        assert not any(
            u.get("type") == "agent_run" and u.get("status") == "passed"
            for u in updated_statuses
        )
        assert not any(
            u.get("type") == "legacy" and u.get("status") == "completed"
            for u in updated_statuses
        )
        assert list(store.ledger_events) == ["run_failed:run-empty-fail"]
