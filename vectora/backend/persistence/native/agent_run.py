"""Contrato persistente e canônico de execuções do agente (AgentRun).

Consolida eventos, checkpoints, aprovações, tools, artefatos, evidências e métricas
em conformidade com a issue #368 e contratos versionados do backend.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Estados Canônicos de AgentRun
# ---------------------------------------------------------------------------

AgentRunStatus = Literal[
    "planned",
    "running",
    "waiting_approval",
    "verifying",
    "passed",
    "degraded",
    "failed",
    "cancelled",
]

VALID_AGENT_RUN_STATUSES: frozenset[str] = frozenset(
    {
        "planned",
        "running",
        "waiting_approval",
        "verifying",
        "passed",
        "degraded",
        "failed",
        "cancelled",
    }
)

TERMINAL_AGENT_RUN_STATUSES: frozenset[str] = frozenset(
    {
        "passed",
        "degraded",
        "failed",
        "cancelled",
    }
)

# ---------------------------------------------------------------------------
# Redação de Dados Sensíveis
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),  # OpenAI/Anthropic keys
    re.compile(r"ghp_[A-Za-z0-9]{36}"),  # GitHub PATs
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access keys
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}"),  # Bearer tokens
)

_SENSITIVE_KEY_NAMES: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "secret_key",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "private_key",
    }
)


def redact_sensitive_data(value: Any) -> Any:
    """Aplica redação recursiva a segredos, tokens e chaves conhecidas."""
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for k, v in value.items():
            if str(k).lower() in _SENSITIVE_KEY_NAMES:
                result[k] = "[REDACTED]"
            else:
                result[k] = redact_sensitive_data(v)
        return result
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Modelos Estruturados de Métricas e Run
# ---------------------------------------------------------------------------


class AgentRunMetrics(BaseModel):
    """Métricas consolidadas de consumo e tempo de execução."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    duration_ms: int = 0
    cost_cents: float = 0.0
    attempts: int = 1
    retries: int = 0


class AgentRunTimelineEntry(BaseModel):
    """Entrada da linha do tempo da execução."""

    seq: int = 0
    event_key: str = ""
    type: str
    created_at: str = Field(default_factory=_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentRun(BaseModel):
    """Contrato persistente único para execuções do agente."""

    run_id: str
    thread_id: str
    turn_id: str | None = None
    user_id: str = ""
    workspace_id: str | None = None
    model: str = ""
    provider: str = ""
    status: AgentRunStatus = "running"
    plan_step: str | None = None
    tools_available: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    files_touched: list[dict[str, Any]] = Field(default_factory=list)
    commands_run: list[dict[str, Any]] = Field(default_factory=list)
    tests_run: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    metrics: AgentRunMetrics = Field(default_factory=AgentRunMetrics)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    detail: str | None = None
    error_code: str | None = None
    started_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    terminal_at: str | None = None


class AgentRunTimelineResponse(BaseModel):
    """Timeline completa de eventos correlacionados à execução."""

    run: AgentRun
    timeline: list[AgentRunTimelineEntry] = Field(default_factory=list)
