"""Contrato persistente e canônico de execuções do agente (AgentRun).

Consolida eventos, checkpoints, aprovações, tools, artefatos, evidências e métricas
em conformidade com contratos versionados do backend.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal, overload

from pydantic import BaseModel, Field, JsonValue

# ---------------------------------------------------------------------------
# Estados Canônicos de AgentRun
# ---------------------------------------------------------------------------

AgentRunStatus = Literal[
    "planned",
    "admitted",
    "running",
    "waiting_approval",
    "waiting_provider",
    "tool_running",
    "verifying",
    "passed",
    "degraded",
    "failed",
    "cancelled",
    "recovery_required",
]

VALID_AGENT_RUN_STATUSES: frozenset[str] = frozenset(
    {
        "planned",
        "admitted",
        "running",
        "waiting_approval",
        "waiting_provider",
        "tool_running",
        "verifying",
        "passed",
        "degraded",
        "failed",
        "cancelled",
        "recovery_required",
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
    # KEY="value with spaces" ou KEY='value' ou KEY=value
    re.compile(
        r"""(?ix)
        \b(?P<key>[a-z0-9_]*(?:api[_-]?key|secret|password|passwd|token|auth(?:orization)?|key)[a-z0-9_]*)
        \s*=\s*
        (?P<quote>["'])(?P<val>[^"'\r\n]+)(?P=quote)
        """
    ),
    re.compile(
        r"""(?ix)
        \b(?P<key>[a-z0-9_]*(?:api[_-]?key|secret|password|passwd|token|auth(?:orization)?|key)[a-z0-9_]*)
        \s*=\s*
        (?P<val>[^\s"';&|]+)
        """
    ),
)

_SENSITIVE_KEY_NAMES: frozenset[str] = frozenset(
    {
        "apikey",
        "secret",
        "secretkey",
        "password",
        "passwd",
        "token",
        "authtoken",
        "accesstoken",
        "refreshtoken",
        "authorization",
        "privatekey",
        "key",
        "openaikey",
    }
)


def _is_sensitive_key(key: str) -> bool:
    """Verifica se o nome da chave é sensível após normalização (remoção de hífens, underlines e prefixos)."""
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if normalized in _SENSITIVE_KEY_NAMES:
        return True
    return any(
        target in normalized
        for target in (
            "apikey",
            "secret",
            "password",
            "passwd",
            "token",
            "accesstoken",
            "refreshtoken",
            "privatekey",
            "authorization",
        )
    )


@overload
def redact_sensitive_data(value: str) -> str: ...


@overload
def redact_sensitive_data(value: dict[str, JsonValue]) -> dict[str, JsonValue]: ...


@overload
def redact_sensitive_data(value: list[JsonValue]) -> list[JsonValue]: ...


@overload
def redact_sensitive_data(value: tuple[JsonValue, ...]) -> tuple[JsonValue, ...]: ...


@overload
def redact_sensitive_data(value: Any) -> Any: ...


def redact_sensitive_data(value: Any) -> Any:
    """Aplica redação recursiva a segredos, tokens e chaves conhecidas.

    Usa `Any` justificadamente na implementação interna para acomodar estruturas
    heterogêneas, árvores arbitrárias de evidências e payloads de logs.
    """
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_PATTERNS:
            if "quote" in pattern.groupindex and "key" in pattern.groupindex:
                # Substitui apenas o valor mantendo a chave e aspas
                redacted = pattern.sub(
                    r"\g<key>=\g<quote>[REDACTED]\g<quote>", redacted
                )
            elif "val" in pattern.groupindex and "key" in pattern.groupindex:
                redacted = pattern.sub(r"\g<key>=[REDACTED]", redacted)
            else:
                redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for k, v in value.items():
            if _is_sensitive_key(str(k)):
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
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class AgentRun(BaseModel):
    """Contrato persistente único para execuções do agente."""

    run_id: str
    thread_id: str
    contract_version: int = 1
    turn_id: str | None = None
    parent_run_id: str | None = None
    correlation_id: str | None = None
    user_id: str = ""
    workspace_id: str | None = None
    model: str = ""
    provider: str = ""
    status: AgentRunStatus = "running"
    plan_step: str | None = None
    checkpoint_id: str | None = None
    tools_available: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    approvals: list[dict[str, JsonValue]] = Field(default_factory=list)
    files_touched: list[dict[str, JsonValue]] = Field(default_factory=list)
    commands_run: list[dict[str, JsonValue]] = Field(default_factory=list)
    tests_run: list[dict[str, JsonValue]] = Field(default_factory=list)
    artifacts: list[dict[str, JsonValue]] = Field(default_factory=list)
    metrics: AgentRunMetrics = Field(default_factory=AgentRunMetrics)
    evidence: list[dict[str, JsonValue]] = Field(default_factory=list)
    untrusted_content: bool = False
    detail: str | None = None
    error_code: str | None = None
    started_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    terminal_at: str | None = None


class AgentRunTimelineResponse(BaseModel):
    """Timeline completa de eventos correlacionados à execução."""

    run: AgentRun
    timeline: list[AgentRunTimelineEntry] = Field(default_factory=list)
