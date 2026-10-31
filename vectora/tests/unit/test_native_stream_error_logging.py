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
from pathlib import Path
from typing import cast

import pytest

from backend.api.native_stream import stream_engine_events
from backend.engine.stream_events import ErrorSignal, EventSink, MessageBreak
from backend.persistence.native.ledger import LedgerEventTypeLiteral, ThreadLedgerEvent
from backend.vtypes.message import MessageRole, VMessage, text_message


def _parse(sse: str) -> dict:
    """Executa a operação parse."""
    line = next(x for x in sse.splitlines() if x.startswith("data:"))
    return json.loads(line[len("data:") :].strip())


class TestErroDeExecucaoFicaLogado:
    async def test_fallback_repara_evento_apos_falha_parcial_do_ledger(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Uma falha no segundo write deixa um estado recuperável no retry."""

        class _LegacyStore:
            def __init__(self) -> None:
                self.messages: list[dict[str, object]] = []
                self.ledger_events: dict[str, ThreadLedgerEvent] = {}
                self.fail_event_once = True

            async def get_message_id_by_turn_id(
                self, thread_id: str, turn_id: str
            ) -> int | None:
                return 1 if self.messages else None

            async def get_branch_head_id(self, thread_id: str) -> int:
                return 0

            async def list_events(
                self, thread_id: str, *, limit: int = 500
            ) -> list[ThreadLedgerEvent]:
                return list(self.ledger_events.values())[:limit]

            async def append_message(
                self, thread_id: str, message: VMessage, **kwargs: object
            ) -> int:
                self.messages.append({"message": message, **kwargs})
                return len(self.messages)

            async def append_ledger_event(
                self, thread_id: str, **kwargs: object
            ) -> None:
                if self.fail_event_once:
                    self.fail_event_once = False
                    raise RuntimeError("falha transitória do ledger")
                event = ThreadLedgerEvent(
                    thread_id=thread_id,
                    event_key=str(kwargs["event_key"]),
                    type=cast("LedgerEventTypeLiteral", str(kwargs["event_type"])),
                    run_id=str(kwargs["run_id"]),
                    payload=cast("dict[str, object]", kwargs["payload"]),
                )
                self.ledger_events[event.event_key] = event

        store = _LegacyStore()
        from backend.services import agent_factory

        async def _get_store() -> _LegacyStore:
            return store

        monkeypatch.setattr(agent_factory, "get_session_store", _get_store)

        async def _run(on_event: EventSink) -> str:
            raise RuntimeError("provider indisponível")

        for _ in range(2):
            _ = [
                _parse(sse)
                async for sse in stream_engine_events(
                    _run, thread_id="t-legacy-recovery", run_id="run-legacy-recovery"
                )
            ]

        assert len(store.messages) == 1
        assert list(store.ledger_events) == ["run_failed:run-legacy-recovery"]
        event = store.ledger_events["run_failed:run-legacy-recovery"]
        assert event.type == "run_failed"
        assert event.run_id == "run-legacy-recovery"
        assert event.thread_id == "t-legacy-recovery"
        assert str(event.payload["detail"]).startswith("STREAM_ERROR:")

    async def test_run_failed_persiste_evento_completo_no_store_real(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Duas execuções deixam um único evento RUN_FAILED completo no ledger."""
        from backend.persistence.native.session_store import SessionStore
        from backend.storage.sqlite.pool import AsyncConnectionPool

        db_path = Path(str(tmp_path)) / "sessions.db"
        pool = AsyncConnectionPool(str(db_path), min_size=1, max_size=2)
        await pool.open()
        store = SessionStore(pool)
        await store.setup()
        await store.create_session("t-real-ledger", user_id="u1")
        from backend.services import agent_factory

        async def _get_store() -> SessionStore:
            return store

        monkeypatch.setattr(agent_factory, "get_session_store", _get_store)

        async def _run(on_event: EventSink) -> str:
            raise RuntimeError("provider indisponível")

        try:
            for _ in range(2):
                _ = [
                    _parse(sse)
                    async for sse in stream_engine_events(
                        _run, thread_id="t-real-ledger", run_id="run-real-ledger"
                    )
                ]

            events = await store.list_events("t-real-ledger")
        finally:
            await pool.close()

        failed = [
            event
            for event in events
            if event.type == "run_failed" and event.run_id == "run-real-ledger"
        ]
        assert len(failed) == 1
        assert failed[0].event_key == "run_failed:run-real-ledger"
        assert str(failed[0].payload["detail"]).startswith("STREAM_ERROR:")
        assert failed[0].seq == 1

    async def test_mensagem_terminal_orfa_recebe_status_e_evento_no_store_real(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Uma mensagem terminal gravada sem o evento nem o status final é
        reparada pela repetição, sem duplicar a mensagem nem o evento."""
        from backend.persistence.native.session_store import SessionStore
        from backend.services import agent_factory
        from backend.storage.sqlite.pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            str(tmp_path / "sessions.db"), min_size=1, max_size=2
        )
        await pool.open()
        store = SessionStore(pool)
        await store.setup()
        thread_id, run_id = "t-orfa", "run-orfa"
        await store.create_session(thread_id, user_id="u1")
        await store.start_run(thread_id, run_id, turn_id="turn-orfa")
        await store.append_message_recorded(
            thread_id,
            text_message(MessageRole.ASSISTANT, "[falha] provider indisponível"),
            turn_id=f"{run_id}:terminal",
            run_id=run_id,
        )

        async def _get_store() -> SessionStore:
            return store

        monkeypatch.setattr(agent_factory, "get_session_store", _get_store)

        async def _run(on_event: EventSink) -> str:
            raise RuntimeError("provider indisponível")

        try:
            for _ in range(2):
                _ = [
                    _parse(sse)
                    async for sse in stream_engine_events(
                        _run, thread_id=thread_id, run_id=run_id
                    )
                ]
            events = await store.list_events(thread_id)
            run = await store.get_agent_run(run_id)
            history = await store.get_history(thread_id)
        finally:
            await pool.close()

        assert [e.type for e in events if e.run_id == run_id].count("run_failed") == 1
        assert run is not None and run.status == "failed"
        assert sum(1 for m in history if m.role == MessageRole.ASSISTANT) == 1

    async def test_run_error_persiste_desfecho_terminal_idempotente(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifica o comportamento de run error persiste desfecho terminal idempotente."""

        class _Store:
            def __init__(self) -> None:
                """Inicializa o estado da instância."""
                self.messages: list[dict[str, object]] = []
                self.ledger_events: dict[str, dict[str, object]] = {}
                self.fail_message_once = True

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
                if self.fail_message_once:
                    self.fail_message_once = False
                    raise RuntimeError("falha transitória de persistência")
                self.messages.append({"message": message, **kwargs})
                return 8

            async def append_message_recorded(
                self,
                thread_id: str,
                message: VMessage,
                *,
                extra_events: list[ThreadLedgerEvent] | None = None,
                **kwargs: object,
            ) -> tuple[int, int]:
                """Imita a transação nativa de mensagem + eventos extras."""
                message_id = await self.append_message(thread_id, message, **kwargs)
                for extra in extra_events or []:
                    event_key = str(extra.event_key)
                    self.ledger_events.setdefault(
                        event_key,
                        {
                            "thread_id": thread_id,
                            "event_type": str(extra.type),
                            "run_id": extra.run_id,
                            "payload": extra.payload,
                        },
                    )
                return message_id, 1

            async def append_ledger_event(
                self, thread_id: str, **kwargs: object
            ) -> None:
                """Record ledger events emitted by the stream under test."""
                key = str(kwargs["event_key"])
                self.ledger_events.setdefault(key, {"thread_id": thread_id, **kwargs})

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
        assert list(store.ledger_events) == ["run_failed:run-terminal"]
        event = store.ledger_events["run_failed:run-terminal"]
        assert event["event_type"] == "run_failed"
        assert event["run_id"] == "run-terminal"
        assert isinstance(event["payload"], dict)
        assert str(event["payload"]["detail"]).startswith("STREAM_ERROR:")

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
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Duas execuções persistem um único ``run_failed`` completo no ledger real."""
        from backend.persistence.native.session_store import SessionStore
        from backend.storage.sqlite.pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            str(tmp_path / "sessions.db"), min_size=1, max_size=2
        )
        await pool.open()
        store = SessionStore(pool)
        await store.setup()
        await store.create_session("t-empty-fail", user_id="u1")
        from backend.services import agent_factory

        async def _get_store() -> SessionStore:
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

        try:
            for _ in range(2):
                _ = [
                    _parse(sse)
                    async for sse in stream_engine_events(
                        _run_empty, thread_id="t-empty-fail", run_id="run-empty-fail"
                    )
                ]
            events = await store.list_events("t-empty-fail")
        finally:
            await pool.close()

        failed = [
            event
            for event in events
            if event.type == "run_failed" and event.run_id == "run-empty-fail"
        ]
        assert len(failed) == 1
        assert failed[0].event_key == "run_failed:run-empty-fail"
        assert failed[0].payload == {
            "detail": "O provedor LLM encerrou o stream sem retornar texto ou ferramenta."
        }
        assert failed[0].seq == 1
