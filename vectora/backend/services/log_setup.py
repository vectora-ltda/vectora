"""Logging Configuration and Initialization.

Sets up structured JSON logging with correlation IDs for production observability.
Supports multiple log levels, file rotation, and console output.
"""

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_LOG_EXTRA_FIELDS: frozenset[str] = frozenset(
    {
        "tool",
        "command",
        "url",
        "endpoint",
        "operation",
        "repo",
        "owner",
        "pr_number",
        "issue_number",
        "workspace_id",
        "thread_id",
        "tab_id",
        "transport",
        "exit_code",
        "output_length",
        "status",
        "passed",
        "argument_keys",
        "provider",
        "topic",
        "query",
        "error",
    }
)
_SECRET_KEY_RE = re.compile(
    r"(?:token|secret|password|passwd|authorization|api[_-]?key|private[_-]?key|signature)",
    re.IGNORECASE,
)
_COMMAND_SECRET_RE = re.compile(
    r"(?i)(--?(?:token|password|api[-_]?key|secret))(?:\s+|=)([^\s'\"]+)"
    r"|(\b(?:GH_TOKEN|GITHUB_TOKEN|OPENAI_API_KEY|TAVILY_API_KEY)\s*=)([^\s'\"]+)"
    r"|(Authorization:\s*Bearer\s+)([^\s'\"]+)"
)


def _redact_url(value: str) -> str:
    """Remove credential-like query parameters while preserving the URL target."""
    try:
        parts = urlsplit(value)
        query = [
            (key, "[REDACTED]" if _SECRET_KEY_RE.search(key) else item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
        ]
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )
    except ValueError:
        return value


def _safe_log_value(key: str, value: Any) -> Any:
    """Return an allowlisted, bounded log value without credentials."""
    if value is None:
        return None
    if _SECRET_KEY_RE.search(key):
        return "[REDACTED]"
    if isinstance(value, str):
        result = _redact_url(value) if key in {"url", "endpoint"} else value
        result = _COMMAND_SECRET_RE.sub(
            lambda match: (
                (match.group(1) or match.group(3) or match.group(5) or "")
                + "[REDACTED]"
            ),
            result,
        )
        return result[:300]
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_safe_log_value(key, item) for item in list(value)[:20]]
    return str(value)[:300]


def _structured_extras(record: logging.LogRecord) -> dict[str, Any]:
    """Select safe structured context; arbitrary LogRecord extras are excluded."""
    return {
        key: _safe_log_value(key, getattr(record, key))
        for key in _LOG_EXTRA_FIELDS
        if hasattr(record, key)
    }


class JSONFormatter(logging.Formatter):
    """Format logs as JSON for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        if hasattr(record, "thread_id"):
            log_obj["thread_id"] = record.thread_id
        if hasattr(record, "user_type"):
            log_obj["user_type"] = record.user_type
        if hasattr(record, "model"):
            log_obj["model"] = record.model

        if hasattr(record, "retrieval_source"):
            log_obj["retrieval_source"] = record.retrieval_source
        if hasattr(record, "reranking_applied"):
            log_obj["reranking_applied"] = record.reranking_applied
        if hasattr(record, "routing_decision"):
            log_obj["routing_decision"] = record.routing_decision

        log_obj.update(_structured_extras(record))

        return json.dumps(log_obj, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Format logs as readable text for development."""

    def format(self, record: logging.LogRecord) -> str:
        prefix = f"[{record.levelname:8}] {record.name:20} | "

        if hasattr(record, "thread_id"):
            prefix += f"thread={record.thread_id:>3} | "

        msg = record.getMessage()
        extras = _structured_extras(record)
        if extras:
            rendered = " ".join(
                f"{key}={json.dumps(value, ensure_ascii=False)}"
                for key, value in sorted(extras.items())
            )
            msg += f" | {rendered}"
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)

        return prefix + msg


class _BackgroundConsoleFilter(logging.Filter):
    """Bloqueia loggers de background do console em QUIET_MODE.

    Esses loggers operam em paralelo com o Rich e inundam o terminal,
    corrompendo o prompt de input. Os registros ainda chegam ao handler
    de arquivo JSON para auditoria — apenas o console é filtrado.

    Loggers bloqueados no console (WARNING+ ainda passam):
    - vectora.services.background  (worker de embedding)
    - vectora.services.queue       (fila de embedding)
    - vectora.graph                (build/compile do grafo — repete por request)
    - vectora.services.agent_factory (session_context + decisão de routing)
    - vectora.tools                (tools initialized)
    - vectora.api.handlers.chat    (graph inicializado)
    """

    _CONSOLE_NOISY: frozenset[str] = frozenset(
        {
            "backend.embedding.background",
            "backend.embedding.queue",
            "backend.services.agent_factory",
            "backend.tools",
            "backend.api.handlers.chat",
        }
    )

    def filter(self, record: logging.LogRecord) -> bool:
        # Deixa WARNING+ sempre passar (erros devem ser visíveis)
        if record.levelno >= logging.WARNING:
            return True
        return record.name not in self._CONSOLE_NOISY


def setup_logging(
    json_output: bool | None = None,
    log_level: str | None = None,
    log_file: str | None = None,
) -> None:
    """Configure logging com saída dupla: texto (console) + JSON (arquivo).

    O arquivo JSON é sempre gravado em ~/.vectora/logs/vectora.jsonl,
    independente de LOG_JSON. Isso garante que a sessão inteira fique
    auditável em tempo real sem nenhuma variável de ambiente extra.

    Args:
        json_output: Ignorado (mantido por compatibilidade). O arquivo JSON
            é sempre habilitado.
        log_level: Nível de logging (DEBUG, INFO, WARNING, etc). Padrão: INFO.
        log_file: Caminho do arquivo JSON. Se None, usa ~/.vectora/logs/vectora.jsonl.

    Environment Variables:
        LOG_LEVEL: Nível de logging (padrão: INFO)
        LOG_FILE: Caminho alternativo para o arquivo de log
        QUIET_MODE: "true" silencia libs externas (padrão: true)
    """
    if log_level is None:
        log_level = os.getenv("LOG_LEVEL", "INFO")

    # Resolve o caminho do arquivo de log — sempre em ~/.vectora/logs/
    if log_file is None:
        log_file = os.getenv("LOG_FILE") or str(
            Path.home() / ".vectora" / "logs" / "backend.jsonl"
        )

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level))

    if root_logger.handlers:
        return

    formatter_text = TextFormatter(
        fmt="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    handler_console = logging.StreamHandler()
    handler_console.setFormatter(formatter_text)
    handler_console.setLevel(getattr(logging, log_level))

    # Silencia libs externas verbosas por padrão (QUIET_MODE)
    quiet_mode = os.getenv("QUIET_MODE", "true").lower() == "true"
    if quiet_mode:
        # Loggers de background bloqueados no console (ainda vão para o arquivo JSON).
        # Usamos Filter no handler — NÃO setLevel() no logger — para preservar
        # auditabilidade no arquivo sem inundar o terminal do Rich.
        handler_console.addFilter(_BackgroundConsoleFilter())

        silent_loggers = [
            "google",
            "google.genai",
            "google.genai._api_client",
            "google.genai._api_client.BaseApiClient",
            "google_genai",
            "google_genai._api_client",
            "httpx",
            "urllib3",
            "requests",
            "asyncio",
            "cohere",
            "cohere.client",
            "cohere.base_client",
            "aiosqlite",
            "huggingface_hub",
            "huggingface_hub.utils",
            "huggingface_hub.utils._http",
            "uvicorn.access",
            "fastapi",
        ]
        for logger_name in silent_loggers:
            logging.getLogger(logger_name).setLevel(logging.CRITICAL)

    root_logger.addHandler(handler_console)

    # Arquivo JSON — sempre habilitado, garante auditabilidade da sessão
    log_file_path = Path(log_file)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    formatter_json = JSONFormatter()
    handler_file = logging.FileHandler(log_file_path, mode="a", encoding="utf-8")
    handler_file.setFormatter(formatter_json)
    handler_file.setLevel(getattr(logging, log_level))
    root_logger.addHandler(handler_file)
