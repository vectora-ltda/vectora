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
    line = next(x for x in sse.splitlines() if x.startswith("data:"))
    return json.loads(line[len("data:") :].strip())


class TestErroDeExecucaoFicaLogado:
    async def test_run_error_persiste_desfecho_terminal_idempotente(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Store:
            def __init__(self) -> None:
                self.messages: list[dict[str, object]] = []

            async def get_message_id_by_turn_id(self, thread_id: str, turn_id: str):
                for message in self.messages:
                    if message["turn_id"] == turn_id:
                        return 1
                return None

            async def get_branch_head_id(self, thread_id: str):
                return 7

            async def append_message(self, thread_id: str, message, **kwargs):
                self.messages.append({"message": message, **kwargs})
                return 8

        store = _Store()
        from backend.services import agent_factory

        async def _get_store():
            return store

        monkeypatch.setattr(agent_factory, "get_session_store", _get_store)

        async def _run(on_event: EventSink) -> str:
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
    ):
        """Happy path: ``run`` que lança vira ``ErrorEvent`` no stream SSE.
        Erro/borda (o ponto real desta regressão): o `run_error` original
        precisa aparecer no log, com o traceback, não só a mensagem
        amigável classificada."""

        async def _run_que_falha(
            on_event: EventSink,
        ) -> str:
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
        async def _run_com_resposta_vazia(
            on_event: EventSink,
        ) -> str:
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
