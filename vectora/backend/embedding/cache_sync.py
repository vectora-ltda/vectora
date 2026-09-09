"""Sincronização de caches entre réplicas.

Cada réplica mantém caches in-memory (L1): tools MCP resolvidas, LLM bindado,
política de tools e workspace ativo. Quando uma réplica muda algo, publica no
KV (Redis pub/sub em modo complete); as demais aplicam a mudança localmente.

Também é o bootstrap único de pub/sub do KV pro processo inteiro — o backend
(`get_kv()`) só inicia o reader (`kv.start()`) na 1ª chamada; canais
inscritos depois disso são ignorados silenciosamente. Por isso o bridge de
SSE cross-réplica (`backend/api/handlers/webhooks.py::CHANNEL_SSE`) também
se registra aqui, não no próprio módulo de webhooks.

Canais:
    vectora:tools      {"user_id", "version"}  → plugins + llm_tools
    vectora:policy     {"user_id", "version"}  → tool_policy + llm_tools
    vectora:ws-active  {"user_id", "workspace_id"} → workspace_registry
    vectora:mcp-policy {"version"} → allowlist MCP e caches
    vectora:sse        WebhookEvent serializado → SSE de background_tasks/RAG

Em modo lite (MemoryKV) o publish entrega no próprio processo — inofensivo.
"""

from __future__ import annotations

import json
import logging
from typing import Literal, cast

from backend.persistence.kv import get_kv, kv_initialized

logger = logging.getLogger(__name__)

CHANNEL_TOOLS = "vectora:tools"
CHANNEL_POLICY = "vectora:policy"
CHANNEL_WS_ACTIVE = "vectora:ws-active"
CHANNEL_MCP_POLICY = "vectora:mcp-policy"


def _parse(payload: str) -> dict:
    try:
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _on_tools_changed(payload: str) -> None:
    data = _parse(payload)
    user_id = str(data.get("user_id", ""))
    version = int(data.get("version", 0))
    scope = cast(
        "Literal['user', 'workspace', 'project', 'runtime']",
        str(data.get("scope", "user")),
    )
    target = data.get("target")
    if not user_id:
        return
    from backend.workspace import plugins

    plugins.apply_remote_version(
        user_id, version, scope, str(target) if target else None
    )


def _on_policy_changed(payload: str) -> None:
    data = _parse(payload)
    user_id = str(data.get("user_id", ""))
    version = int(data.get("version", 0))
    if not user_id:
        return
    from backend.rbac import tool_policy

    tool_policy.apply_remote_version(user_id, version)


def _on_mcp_policy_changed(payload: str) -> None:
    data = _parse(payload)
    version = int(data.get("version", 0))
    if version <= 0:
        return
    from backend.services import mcp_policy

    rules = data.get("rules")
    snapshot = rules if isinstance(rules, list) else None
    mcp_policy.apply_remote_version(version, snapshot)


def _on_ws_active_changed(payload: str) -> None:
    data = _parse(payload)
    user_id = str(data.get("user_id", ""))
    workspace_id = str(data.get("workspace_id", ""))
    if not user_id or not workspace_id:
        return
    from backend.workspace.workspace import workspace_registry

    workspace_registry.apply_remote_active(workspace_id, user_id)


async def start_cache_sync() -> None:
    """Registra os subscribers e inicia o reader (chamado no lifespan)."""
    from backend.api.handlers.webhooks import CHANNEL_SSE, on_remote_sse_event

    kv = await get_kv()
    kv.subscribe(CHANNEL_TOOLS, _on_tools_changed)
    kv.subscribe(CHANNEL_POLICY, _on_policy_changed)
    kv.subscribe(CHANNEL_MCP_POLICY, _on_mcp_policy_changed)
    kv.subscribe(CHANNEL_WS_ACTIVE, _on_ws_active_changed)
    kv.subscribe(CHANNEL_SSE, on_remote_sse_event)
    await kv.start()
    logger.info("cache_sync: subscribers registrados (%s)", type(kv).__name__)


async def stop_cache_sync() -> None:
    """Fecha o KV só se `start_cache_sync` (ou outro caller) já o inicializou
    — chamar `get_kv()` incondicionalmente no shutdown subiria o sidecar
    NATS do zero (se ainda não tinha subido nesta sessão) só pra fechá-lo
    em seguida."""
    if not kv_initialized():
        return
    kv = await get_kv()
    await kv.close()
