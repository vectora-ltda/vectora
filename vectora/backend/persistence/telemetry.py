"""VectoraTelemetry — observabilidade nativa de execução do agente.

Logging estruturado próprio: grava início/fim de turno, cada tool call
(nome, args resumidos, duração, sucesso/erro) e erros não tratados como
eventos JSON, um por linha.

Os eventos são emitidos pelo logger dedicado ``backend.telemetry``. Por
padrão eles propagam para o root logger e caem no mesmo
``~/.vectora/logs/backend.jsonl`` já configurado por
``backend/services/log_setup.py``. Se ``settings.telemetry_output_path``
estiver definido, um ``FileHandler`` próprio (sem propagação) grava só os
eventos de telemetria nesse arquivo separado.

Uso:
    from backend.persistence.telemetry import telemetry

    await telemetry.turn_start(session_id=42, turn_id="abc123")
    await telemetry.record_tool_call(
        "web_search", {"query": q}, duration_ms=120.4, success=True
    )
    await telemetry.turn_end(session_id=42, turn_id="abc123", status="ok", duration_ms=980.1)
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TELEMETRY_LOGGER_NAME = "backend.telemetry"
_MAX_FIELD_LEN = 200

_RECORD_FIELDS = (
    "session_id",
    "turn_id",
    "tool_name",
    "args_summary",
    "duration_ms",
    "success",
    "status",
    "error_type",
    "error_message",
    "source",
    "operation",
    "provider",
    "model",
    "estimate_version",
    "billable_unit",
    "currency",
    "units",
    "state",
    "previous_state",
    "new_state",
    "result",
    "idempotency_key",
    "query_hash",
    "collection_count",
    "result_count",
    "retrieval_method",
    "reranker",
    "reranker_status",
    "storage_backend",
    "namespace_hash",
    "bytes_written",
)


def _truncate(text: str) -> str:
    """Executa a operação  truncate."""
    return text if len(text) <= _MAX_FIELD_LEN else text[:_MAX_FIELD_LEN] + "…"


def _summarize_args(args: dict[str, Any] | None) -> dict[str, str]:
    """Reduz args de tool call a strings curtas — nunca grava payload completo."""
    if not args:
        return {}
    summary: dict[str, str] = {}
    for key, value in args.items():
        try:
            text = str(value)
        except Exception:
            text = "<unrepr>"
        summary[key] = _truncate(text)
    return summary


class TelemetryJSONFormatter(logging.Formatter):
    """Serializa um evento de telemetria como uma linha JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Executa a operação format."""
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "event": getattr(record, "telemetry_event", record.getMessage()),
        }
        for field_name in _RECORD_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value
        return json.dumps(payload, ensure_ascii=False)


class VectoraTelemetry:
    """Telemetria nativa de execução: turnos, tool calls e erros não tratados.

    Toda gravação é best-effort — uma falha ao emitir um evento nunca
    propaga para o chamador (agente/tool). Controlada por
    ``settings.telemetry_enabled``; desabilitada, os métodos viram no-op.
    """

    def __init__(self) -> None:
        """Inicializa o estado da instância."""
        self._logger = logging.getLogger(_TELEMETRY_LOGGER_NAME)
        self._configured_path: str | None = None

    def _enabled(self) -> bool:
        """Executa a operação  enabled."""
        try:
            from backend.settings import settings

            return bool(settings.telemetry_enabled)
        except Exception:
            return True

    def configure(self) -> bool:
        """Prepara o logger de telemetria a partir das settings atuais.

        Idempotente — chamadas repetidas com o mesmo ``telemetry_output_path``
        não duplicam handlers.

        Returns:
            True se a telemetria está habilitada.
        """
        try:
            from backend.settings import settings
        except Exception:
            logger.warning("telemetry: settings indisponíveis, usando defaults")
            return True

        if not settings.telemetry_enabled:
            self._logger.disabled = True
            return False

        self._logger.disabled = False
        output_path = settings.telemetry_output_path

        if output_path and output_path != self._configured_path:
            for handler in list(self._logger.handlers):
                self._logger.removeHandler(handler)
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(path, mode="a", encoding="utf-8")
            handler.setFormatter(TelemetryJSONFormatter())
            self._logger.addHandler(handler)
            self._logger.propagate = False
            self._configured_path = output_path
            logger.info("telemetry: eventos nativos habilitados em %s", output_path)
        elif not output_path:
            self._logger.propagate = True
            logger.info("telemetry: eventos nativos habilitados (log padrão)")

        return True

    def _emit(self, level: int, event: str, **fields: Any) -> None:
        """Executa a operação  emit."""
        if not self._enabled():
            return
        try:
            self._logger.log(level, event, extra={"telemetry_event": event, **fields})
        except Exception:
            logger.debug("telemetry: falha ao gravar evento (ignorado)", exc_info=True)

    async def turn_start(self, session_id: int | None, turn_id: str) -> None:
        """Marca o início de um turno de agente."""
        self._emit(logging.INFO, "turn_start", session_id=session_id, turn_id=turn_id)

    async def turn_end(
        self,
        session_id: int | None,
        turn_id: str,
        status: str,
        duration_ms: float,
    ) -> None:
        """Marca o fim de um turno de agente (status: ok/error)."""
        self._emit(
            logging.INFO,
            "turn_end",
            session_id=session_id,
            turn_id=turn_id,
            status=status,
            duration_ms=round(duration_ms, 2),
        )

    async def record_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any] | None,
        duration_ms: float,
        success: bool,
        error: BaseException | None = None,
        session_id: int | None = None,
    ) -> None:
        """Grava uma chamada de tool: nome, args resumidos, duração, sucesso/erro.

        Nunca propaga exceção — nem a recebida em ``error`` (apenas registrada),
        nem falhas internas de gravação.
        """
        fields: dict[str, Any] = {
            "tool_name": tool_name,
            "args_summary": _summarize_args(args),
            "duration_ms": round(duration_ms, 2),
            "success": success,
            "session_id": session_id,
        }
        if error is not None:
            fields["error_type"] = type(error).__name__
            fields["error_message"] = _truncate(str(error))
        self._emit(logging.INFO if success else logging.ERROR, "tool_call", **fields)

    def record_error(self, source: str, exc: BaseException, **extra: Any) -> None:
        """Grava um erro não tratado. Síncrono — chamável de qualquer bloco except."""
        fields: dict[str, Any] = {
            "source": source,
            "error_type": type(exc).__name__,
            "error_message": _truncate(str(exc)),
            **extra,
        }
        self._emit(logging.ERROR, "unhandled_error", **fields)

    def record_media_quota(self, event: str, **fields: Any) -> None:
        """Emite um evento de quota sem aceitar conteúdo de mídia.

        O contrato limita os campos a identificadores e valores agregados;
        prompts, textos, caminhos e payloads de provider são descartados.
        """
        allowed = {
            "operation",
            "provider",
            "model",
            "estimate_version",
            "billable_unit",
            "currency",
            "units",
            "state",
            "previous_state",
            "new_state",
            "result",
        }
        self._emit(
            logging.INFO,
            f"media_quota.{event}",
            **{key: value for key, value in fields.items() if key in allowed},
        )

    async def record_retrieval(
        self,
        *,
        query: str,
        collections: list[str],
        result_count: int,
        retrieval_method: str,
        reranker: str | None = None,
        reranker_status: str = "not_configured",
    ) -> None:
        """Registra uma busca sem persistir o texto da consulta.

        O hash permite correlacionar uma busca com logs posteriores sem expor
        prompts potencialmente sensíveis no arquivo de telemetria.
        """
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        self._emit(
            logging.INFO,
            "retrieval",
            query_hash=query_hash,
            collection_count=len(collections),
            result_count=result_count,
            retrieval_method=retrieval_method,
            reranker=reranker,
            reranker_status=reranker_status,
            operation="vector_search",
        )

    async def record_storage_operation(
        self,
        *,
        backend: str,
        operation: str,
        namespace: tuple[str, ...],
        duration_ms: float,
        bytes_written: int = 0,
        result_count: int | None = None,
    ) -> None:
        """Registra custo agregado de uma operação do store.

        O namespace é reduzido a hash para permitir correlação sem gravar
        nomes de workspace ou conteúdo potencialmente sensível.
        """
        namespace_hash = hashlib.sha256("\x1e".join(namespace).encode()).hexdigest()
        self._emit(
            logging.INFO,
            "storage_operation",
            operation=operation,
            storage_backend=backend,
            namespace_hash=namespace_hash,
            duration_ms=round(duration_ms, 2),
            bytes_written=max(0, bytes_written),
            result_count=result_count,
        )


#: Instância singleton — importar diretamente:
#: ``from backend.persistence.telemetry import telemetry``
telemetry = VectoraTelemetry()

__all__ = ["TelemetryJSONFormatter", "VectoraTelemetry", "telemetry"]
