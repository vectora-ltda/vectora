"""Perfis de agente customizados — preset reutilizável (instrução, escopo de
tools, modelo, budget) que uma task do Kanban pode referenciar em vez de
rodar sempre com a personalidade genérica do orchestrator.

Inspirado no conceito de "agente como membro da equipe" do Paperclip, mas
sem a hierarquia organizacional (Vectora é local-first single-tenant, não há
"diretoria" pra aprovar agente novo) — é só um preset editável, com o mesmo
princípio de auditoria (toda mudança de config fica logada).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({"draft", "active", "paused"})


@dataclass
class AgentProfile:
    id: str
    user_id: str
    name: str
    title: str = ""
    icon: str = ""
    color: str = ""
    instruction_path: str | None = None
    tool_scope: list[str] = field(default_factory=list)
    model_override: str | None = None
    budget_cents: int | None = None
    status: str = "active"
    created_at: str | None = None
    updated_at: str | None = None


def _row_to_profile(row: dict[str, Any]) -> AgentProfile:
    scope_raw = row.get("tool_scope") or "[]"
    try:
        scope = json.loads(scope_raw) if isinstance(scope_raw, str) else list(scope_raw)
    except (ValueError, TypeError):
        scope = []
    return AgentProfile(
        id=row["id"],
        user_id=str(row["user_id"]),
        name=row["name"],
        title=row.get("title") or "",
        icon=row.get("icon") or "",
        color=row.get("color") or "",
        instruction_path=row.get("instruction_path"),
        tool_scope=scope,
        model_override=row.get("model_override"),
        budget_cents=row.get("budget_cents"),
        status=row.get("status") or "active",
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


async def _get_db() -> Any:
    """Conexão aiosqlite com row_factory de dict (injetável em testes) —
    mesmo padrão de ``backend/scheduling/background_tasks.py::_get_db``."""
    import aiosqlite

    from backend.settings import settings

    db_path = settings.db_dsn or ":memory:"
    conn: Any = await aiosqlite.connect(db_path)
    conn.row_factory = lambda c, r: dict(
        zip([col[0] for col in c.description], r, strict=False)
    )
    return conn


def _validate(
    name: str, status: str, budget_cents: int | None, tool_scope: list[str]
) -> None:
    if not name.strip():
        raise ValueError("name não pode ser vazio")
    if status not in VALID_STATUSES:
        msg = f"status inválido: {status!r}. Válidos: {sorted(VALID_STATUSES)}"
        raise ValueError(msg)
    if budget_cents is not None and budget_cents < 0:
        raise ValueError("budget_cents não pode ser negativo")

    from backend.nodes.tools import ALL_TOOL_NAMES

    unknown = [t for t in tool_scope if t not in ALL_TOOL_NAMES]
    if unknown:
        raise ValueError(f"tool_scope contém tools inexistentes: {sorted(unknown)}")


async def create_profile(
    user_id: str,
    name: str,
    *,
    title: str = "",
    icon: str = "",
    color: str = "",
    instruction_path: str | None = None,
    tool_scope: list[str] | None = None,
    model_override: str | None = None,
    budget_cents: int | None = None,
    status: str = "active",
) -> AgentProfile:
    scope = tool_scope or []
    _validate(name, status, budget_cents, scope)

    conn = await _get_db()
    profile_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO vectora_agent_profiles
            (id, user_id, name, title, icon, color, instruction_path,
             tool_scope, model_override, budget_cents, status,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            profile_id,
            user_id,
            name,
            title,
            icon,
            color,
            instruction_path,
            json.dumps(scope),
            model_override,
            budget_cents,
            status,
            now,
            now,
        ),
    )
    await conn.commit()
    logger.info(
        "agent_profiles: perfil criado",
        extra={
            "profile_id": profile_id,
            "user_id": user_id,
            "profile_name": name,
        },
    )
    return AgentProfile(
        id=profile_id,
        user_id=user_id,
        name=name,
        title=title,
        icon=icon,
        color=color,
        instruction_path=instruction_path,
        tool_scope=scope,
        model_override=model_override,
        budget_cents=budget_cents,
        status=status,
        created_at=now,
        updated_at=now,
    )


async def list_profiles(user_id: str) -> list[AgentProfile]:
    conn = await _get_db()
    cur = await conn.execute(
        "SELECT * FROM vectora_agent_profiles WHERE user_id = ? "
        "ORDER BY created_at DESC",
        (user_id,),
    )
    rows = await cur.fetchall()
    return [_row_to_profile(r) for r in rows]


async def get_profile(profile_id: str) -> AgentProfile | None:
    conn = await _get_db()
    cur = await conn.execute(
        "SELECT * FROM vectora_agent_profiles WHERE id = ?", (profile_id,)
    )
    row = await cur.fetchone()
    return _row_to_profile(row) if row else None


async def update_profile(profile_id: str, **changes: Any) -> AgentProfile | None:
    """Atualiza os campos passados; devolve ``None`` se o perfil não existir.

    Loga os campos alterados (auditoria mínima — não é a revisão versionada
    completa do Paperclip, mas dá visibilidade de quem mudou o quê).
    """
    existing = await get_profile(profile_id)
    if existing is None:
        return None

    name = changes.get("name", existing.name)
    status = changes.get("status", existing.status)
    budget_cents = changes.get("budget_cents", existing.budget_cents)
    tool_scope = changes.get("tool_scope", existing.tool_scope)
    _validate(name, status, budget_cents, tool_scope)

    fields_map = {
        "name": name,
        "title": changes.get("title", existing.title),
        "icon": changes.get("icon", existing.icon),
        "color": changes.get("color", existing.color),
        "instruction_path": changes.get("instruction_path", existing.instruction_path),
        "tool_scope": json.dumps(tool_scope),
        "model_override": changes.get("model_override", existing.model_override),
        "budget_cents": budget_cents,
        "status": status,
    }
    now = datetime.now(UTC).isoformat()

    conn = await _get_db()
    await conn.execute(
        """
        UPDATE vectora_agent_profiles
        SET name = ?, title = ?, icon = ?, color = ?, instruction_path = ?,
            tool_scope = ?, model_override = ?, budget_cents = ?,
            status = ?, updated_at = ?
        WHERE id = ?
        """,
        (*fields_map.values(), now, profile_id),
    )
    await conn.commit()
    logger.info(
        "agent_profiles: perfil atualizado",
        extra={"profile_id": profile_id, "changed_fields": sorted(changes)},
    )

    return await get_profile(profile_id)


async def delete_profile(profile_id: str) -> bool:
    """Devolve ``True`` se o perfil existia e foi apagado."""
    conn = await _get_db()
    cur = await conn.execute(
        "DELETE FROM vectora_agent_profiles WHERE id = ?", (profile_id,)
    )
    await conn.commit()
    deleted = cur.rowcount > 0
    if deleted:
        logger.info("agent_profiles: perfil apagado", extra={"profile_id": profile_id})
    return deleted
