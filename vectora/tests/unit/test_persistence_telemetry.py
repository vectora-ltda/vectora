"""Testes para ``backend.persistence.telemetry`` — telemetria nativa:
eventos de tool call (sucesso/erro) e o startup que liga a telemetria em
``api/server.py``."""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.persistence.telemetry import (
    TelemetryJSONFormatter,
    VectoraTelemetry,
    _summarize_args,
    _truncate,
    telemetry,
)


def _field(record: logging.LogRecord, name: str) -> Any:
    """Lê um campo dinâmico gravado via ``extra=`` (não capturado pelo
    type-checker estático, que só conhece os atributos fixos de LogRecord)."""
    return getattr(record, name)


class _ListHandler(logging.Handler):
    """Captura os LogRecord emitidos, para inspecionar os campos ``extra``."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def captured() -> Generator[_ListHandler]:
    handler = _ListHandler()
    target_logger = logging.getLogger("backend.telemetry")
    previous_level = target_logger.level
    target_logger.addHandler(handler)
    target_logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        target_logger.removeHandler(handler)
        target_logger.setLevel(previous_level)


class TestRecordToolCall:
    def test_record_media_quota_descarta_payload_sensivel(
        self, captured: _ListHandler
    ) -> None:
        VectoraTelemetry().record_media_quota(
            "hitl_decision",
            operation="generate_image",
            provider="openai",
            model="gpt-image-1",
            units=1,
            prompt="segredo",
            api_key="chave",
            path="C:/segredo.png",
        )

        record = captured.records[0]
        assert _field(record, "telemetry_event") == "media_quota.hitl_decision"
        assert not hasattr(record, "prompt")
        assert not hasattr(record, "api_key")
        assert not hasattr(record, "path")

    async def test_success_and_error_events_never_raise(
        self, captured: _ListHandler
    ) -> None:
        """Uma chamada de tool bem-sucedida grava um evento ``tool_call`` com
        ``success=True``; uma chamada que falhou grava o mesmo tipo de evento
        com ``success=False`` e o tipo/mensagem do erro — sem propagar a
        exceção recebida para quem chamou ``record_tool_call``."""
        telemetry = VectoraTelemetry()

        await telemetry.record_tool_call(
            "web_search",
            {"query": "vectora telemetry"},
            duration_ms=42.5,
            success=True,
        )

        boom = ValueError("timeout ao consultar provider")
        try:
            await telemetry.record_tool_call(
                "fetch_url",
                {"url": "https://example.com"},
                duration_ms=1000.0,
                success=False,
                error=boom,
            )
        except ValueError:
            pytest.fail(
                "record_tool_call propagou a exceção — telemetria deve ser best-effort"
            )

        assert len(captured.records) == 2

        ok_record, err_record = captured.records
        assert _field(ok_record, "telemetry_event") == "tool_call"
        assert _field(ok_record, "tool_name") == "web_search"
        assert _field(ok_record, "success") is True
        assert _field(ok_record, "args_summary") == {"query": "vectora telemetry"}
        assert not hasattr(ok_record, "error_type")

        assert _field(err_record, "telemetry_event") == "tool_call"
        assert _field(err_record, "tool_name") == "fetch_url"
        assert _field(err_record, "success") is False
        assert _field(err_record, "error_type") == "ValueError"
        assert "timeout" in _field(err_record, "error_message")

    async def test_disabled_telemetry_is_noop(self, captured: _ListHandler) -> None:
        """Com ``telemetry_enabled=False`` nenhum evento é gravado — sem erro."""
        telemetry = VectoraTelemetry()

        with patch("backend.settings.settings") as mock_settings:
            mock_settings.telemetry_enabled = False
            await telemetry.record_tool_call(
                "web_search", {"query": "x"}, duration_ms=1.0, success=True
            )

        assert captured.records == []

    def test_record_error_does_not_raise(self, captured: _ListHandler) -> None:
        """``record_error`` é síncrono e best-effort — grava o evento sem
        propagar a exceção original recebida."""
        telemetry = VectoraTelemetry()

        try:
            telemetry.record_error(
                "agent_turn", RuntimeError("falha inesperada no turno")
            )
        except Exception:
            pytest.fail("record_error propagou exceção — deve ser best-effort")

        assert len(captured.records) == 1
        assert _field(captured.records[0], "telemetry_event") == "unhandled_error"
        assert _field(captured.records[0], "source") == "agent_turn"
        assert _field(captured.records[0], "error_type") == "RuntimeError"


class TestTurnEvents:
    async def test_turn_start_and_end_record_status_and_duration(
        self, captured: _ListHandler
    ) -> None:
        telemetry = VectoraTelemetry()

        await telemetry.turn_start(session_id=7, turn_id="turn-1")
        await telemetry.turn_end(
            session_id=7, turn_id="turn-1", status="ok", duration_ms=980.123
        )

        assert len(captured.records) == 2
        start, end = captured.records
        assert _field(start, "telemetry_event") == "turn_start"
        assert _field(start, "session_id") == 7
        assert _field(end, "telemetry_event") == "turn_end"
        assert _field(end, "status") == "ok"
        assert _field(end, "duration_ms") == 980.12


class TestServerStartupWiresTelemetry:
    def test_lifespan_calls_telemetry_configure(self) -> None:
        """O startup do FastAPI (``_lifespan``) liga a telemetria nativa."""
        import os

        os.environ["VECTORA_AUTH_REQUIRED"] = "false"
        from backend.api.server import create_app
        from backend.persistence import telemetry as telemetry_module

        app = create_app()

        called = {"value": False}
        original_configure = telemetry_module.telemetry.configure

        def _tracking_configure() -> bool:
            called["value"] = True
            return original_configure()

        with patch.object(
            telemetry_module.telemetry, "configure", side_effect=_tracking_configure
        ):
            with TestClient(app, raise_server_exceptions=False):
                pass

        assert called["value"], (
            "_lifespan não chamou telemetry.configure() — a telemetria nativa "
            "não seria habilitada no startup do backend."
        )


class TestTruncate:
    def test_short_text_kept_as_is_long_text_gets_ellipsis(self) -> None:
        """Texto até 200 chars não é alterado; acima disso, corta em 200 e
        adiciona reticências — nunca grava payload completo."""
        short = "a" * 200
        long = "a" * 201

        assert _truncate(short) == short
        truncated = _truncate(long)
        assert truncated == "a" * 200 + "…"
        assert len(truncated) == 201


class TestSummarizeArgs:
    def test_empty_and_none_args_return_empty_dict(self) -> None:
        assert _summarize_args(None) == {}
        assert _summarize_args({}) == {}

    def test_values_are_stringified_and_truncated(self) -> None:
        """Cada valor vira string curta — nunca o objeto original — e valores
        longos são truncados; valor cujo __str__ falha vira '<unrepr>' em vez
        de propagar a exceção."""

        class _Boom:
            def __str__(self) -> str:
                raise RuntimeError("__str__ quebrado")

        result = _summarize_args(
            {
                "query": "vectora",
                "payload": "x" * 250,
                "broken": _Boom(),
                "n": 42,
            }
        )

        assert result["query"] == "vectora"
        assert result["payload"] == "x" * 200 + "…"
        assert result["broken"] == "<unrepr>"
        assert result["n"] == "42"


class TestTelemetryJSONFormatter:
    def test_format_produces_valid_json_with_present_fields_only(self) -> None:
        """Campos ausentes (None) não aparecem no JSON final; campos
        presentes batem com o que foi passado via ``extra=``."""
        record = logging.LogRecord(
            name="backend.telemetry",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="tool_call",
            args=(),
            exc_info=None,
        )
        record.telemetry_event = "tool_call"
        record.tool_name = "web_search"
        record.success = True

        line = TelemetryJSONFormatter().format(record)
        payload = json.loads(line)

        assert payload["event"] == "tool_call"
        assert payload["tool_name"] == "web_search"
        assert payload["success"] is True
        assert "duration_ms" not in payload
        assert "error_type" not in payload

    def test_format_falls_back_to_message_when_no_telemetry_event(self) -> None:
        record = logging.LogRecord(
            name="backend.telemetry",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="mensagem crua",
            args=(),
            exc_info=None,
        )
        payload = json.loads(TelemetryJSONFormatter().format(record))
        assert payload["event"] == "mensagem crua"


class TestConfigure:
    def test_disabled_sets_logger_disabled_and_returns_false(self) -> None:
        instance = VectoraTelemetry()
        with patch("backend.settings.settings") as mock_settings:
            mock_settings.telemetry_enabled = False
            enabled = instance.configure()

        assert enabled is False
        assert instance._logger.disabled is True

    def test_enabled_without_output_path_propagates_to_root_logger(self) -> None:
        instance = VectoraTelemetry()
        with patch("backend.settings.settings") as mock_settings:
            mock_settings.telemetry_enabled = True
            mock_settings.telemetry_output_path = None
            enabled = instance.configure()

        assert enabled is True
        assert instance._logger.disabled is False
        assert instance._logger.propagate is True

    def test_output_path_creates_dedicated_file_handler(self, tmp_path: Path) -> None:
        instance = VectoraTelemetry()
        target = tmp_path / "nested" / "telemetry.jsonl"
        with patch("backend.settings.settings") as mock_settings:
            mock_settings.telemetry_enabled = True
            mock_settings.telemetry_output_path = str(target)
            instance.configure()

        assert target.parent.is_dir()
        assert len(instance._logger.handlers) == 1
        assert isinstance(instance._logger.handlers[0], logging.FileHandler)
        assert instance._logger.propagate is False

        for handler in list(instance._logger.handlers):
            instance._logger.removeHandler(handler)
            handler.close()

    def test_repeated_configure_with_same_path_does_not_duplicate_handlers(
        self, tmp_path: Path
    ) -> None:
        """Idempotente: chamar configure() de novo com o mesmo
        ``telemetry_output_path`` não deve acumular um FileHandler por
        chamada — senão cada evento seria gravado N vezes."""
        instance = VectoraTelemetry()
        target = tmp_path / "telemetry.jsonl"
        with patch("backend.settings.settings") as mock_settings:
            mock_settings.telemetry_enabled = True
            mock_settings.telemetry_output_path = str(target)
            instance.configure()
            instance.configure()
            instance.configure()

        assert len(instance._logger.handlers) == 1

        for handler in list(instance._logger.handlers):
            instance._logger.removeHandler(handler)
            handler.close()

    def test_settings_unavailable_falls_back_to_enabled_true(self) -> None:
        """Se ``backend.settings`` não puder ser importado, o padrão seguro é
        manter a telemetria ligada (nunca falhar o startup por causa disso)."""
        instance = VectoraTelemetry()
        with patch.dict("sys.modules", {"backend.settings": None}):
            enabled = instance.configure()

        assert enabled is True


class TestRecordToolCallEdgeCases:
    async def test_none_args_produce_empty_summary(
        self, captured: _ListHandler
    ) -> None:
        instance = VectoraTelemetry()
        await instance.record_tool_call("list_dir", None, duration_ms=3.0, success=True)
        assert _field(captured.records[0], "args_summary") == {}

    async def test_failure_without_explicit_error_object_still_logs_at_error_level(
        self, captured: _ListHandler
    ) -> None:
        """``success=False`` sem passar ``error=`` ainda grava o evento em
        nível ERROR e sem os campos error_type/error_message (não inventa
        um erro que não existe)."""
        instance = VectoraTelemetry()
        await instance.record_tool_call(
            "terminal", {"cmd": "ls"}, duration_ms=5.0, success=False
        )

        record = captured.records[0]
        assert record.levelno == logging.ERROR
        assert not hasattr(record, "error_type")

    async def test_session_id_defaults_to_none_when_not_provided(
        self, captured: _ListHandler
    ) -> None:
        instance = VectoraTelemetry()
        await instance.record_tool_call(
            "web_search", {"q": "x"}, duration_ms=1.0, success=True
        )
        assert _field(captured.records[0], "session_id") is None


class TestRecordErrorEdgeCases:
    def test_extra_fields_are_included_in_the_event(
        self, captured: _ListHandler
    ) -> None:
        telemetry_instance = VectoraTelemetry()
        telemetry_instance.record_error(
            "agent_turn", ValueError("payload inválido"), turn_id="turn-9"
        )

        record = captured.records[0]
        assert _field(record, "turn_id") == "turn-9"
        assert "payload" in _field(record, "error_message")

    def test_long_error_message_is_truncated(self, captured: _ListHandler) -> None:
        telemetry_instance = VectoraTelemetry()
        telemetry_instance.record_error("agent_turn", RuntimeError("x" * 300))

        message = _field(captured.records[0], "error_message")
        assert len(message) == 201
        assert message.endswith("…")


class TestEmitNeverRaises:
    async def test_internal_logging_failure_is_swallowed(
        self, captured: _ListHandler
    ) -> None:
        """Se o próprio logger falhar ao emitir (ex: handler quebrado), o
        chamador nunca recebe exceção — telemetria é sempre best-effort."""
        instance = VectoraTelemetry()

        assert captured.records == []
        with patch.object(
            instance._logger, "log", side_effect=RuntimeError("handler quebrado")
        ):
            try:
                await instance.turn_start(session_id=1, turn_id="t1")
            except Exception:
                pytest.fail("_emit propagou falha interna do logger")


class TestSingleton:
    def test_module_level_telemetry_is_a_vectora_telemetry_instance(self) -> None:
        assert isinstance(telemetry, VectoraTelemetry)
