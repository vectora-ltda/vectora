"""``PostgresSessionStore`` — persistência simplificada de sessões/mensagens
sobre ``asyncpg``.

Mesma semântica/contrato de ``SessionStore``
(``backend/persistence/native/session_store.py``) — só os tipos de coluna e
a sintaxe SQL mudam (``$1``/``$2`` de placeholder, ``BIGSERIAL``,
``ON CONFLICT``). Ver a docstring de ``session_store.py`` pra fork via
``parent_message_id`` e o invariante de HITL sobrevivendo a restart via
``pending_approvals``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.vtypes.message import VMessage

if TYPE_CHECKING:
    import asyncpg

_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS vectora_native_sessions (
    thread_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    workspace_id TEXT,
    parent_thread_id TEXT,
    mode TEXT NOT NULL,
    permission_mode TEXT NOT NULL DEFAULT 'ask',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vectora_native_messages (
    id BIGSERIAL PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES vectora_native_sessions(thread_id),
    parent_message_id BIGINT,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL,
    tool_calls_json TEXT,
    tool_call_id TEXT,
    name TEXT,
    turn_id TEXT,
    is_branch_head BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_vectora_native_messages_thread
    ON vectora_native_messages(thread_id, id);
CREATE INDEX IF NOT EXISTS ix_vectora_native_messages_thread_parent
    ON vectora_native_messages(thread_id, parent_message_id);
CREATE TABLE IF NOT EXISTS vectora_native_pending_approvals (
    thread_id TEXT PRIMARY KEY REFERENCES vectora_native_sessions(thread_id),
    interrupt_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    args_json TEXT NOT NULL,
    reasoning TEXT,
    options_json TEXT NOT NULL DEFAULT '[]',
    priority INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vectora_native_approval_decisions (
    id BIGSERIAL PRIMARY KEY,
    thread_id TEXT NOT NULL,
    interrupt_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    decision TEXT NOT NULL,
    selection TEXT,
    decided_by TEXT,
    decided_at TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '[]',
    priority INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_vectora_native_approval_decisions_thread
    ON vectora_native_approval_decisions(thread_id, decided_at);
"""


# Teto de segurança pra reconstrução recursiva da cadeia de mensagens
# (get_history) — nenhuma conversa legítima chega perto disso; existe só
# pra um `parent_message_id` corrompido/cíclico não fazer a CTE recursar
# sem fim. Mesmo valor/motivo de `session_store.py::_CHAIN_DEPTH_CAP`.
_CHAIN_DEPTH_CAP = 100_000


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _message_to_row(
    msg: VMessage,
) -> tuple[str, str, str | None, str | None, str | None]:
    data = msg.to_dict()
    for block in data["content"]:
        if block.get("kind") == "image_url":
            block["image_url"] = None
    content_json = json.dumps(data["content"], ensure_ascii=False)
    tool_calls_json = (
        json.dumps(data["tool_calls"], ensure_ascii=False)
        if data["tool_calls"]
        else None
    )
    return (
        data["role"],
        content_json,
        tool_calls_json,
        data["tool_call_id"],
        data["name"],
    )


def _row_to_message(row: Any) -> VMessage:
    data = {
        "role": row["role"],
        "content": json.loads(row["content_json"]),
        "tool_calls": (
            json.loads(row["tool_calls_json"]) if row["tool_calls_json"] else []
        ),
        "tool_call_id": row["tool_call_id"],
        "name": row["name"],
        "finish_reason": None,
        "is_error": False,
    }
    return VMessage.from_dict(data)


class PostgresSessionStore:
    """Persistência async sobre um ``asyncpg.Pool`` compartilhado (mesmo pool
    de ``backend.storage.factory.get_pg_pool()``)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._is_setup = False

    async def setup(self) -> None:
        if self._is_setup:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(_SETUP_SQL)
            for statement in (
                (
                    "ALTER TABLE vectora_native_pending_approvals "
                    "ADD COLUMN IF NOT EXISTS options_json TEXT NOT NULL DEFAULT '[]'"
                ),
                (
                    "ALTER TABLE vectora_native_pending_approvals "
                    "ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0"
                ),
                (
                    "ALTER TABLE vectora_native_pending_approvals "
                    "ADD COLUMN IF NOT EXISTS expires_at TEXT"
                ),
            ):
                await conn.execute(statement)
            await conn.execute(
                "ALTER TABLE vectora_native_messages ADD COLUMN IF NOT EXISTS turn_id TEXT"
            )
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_vectora_native_messages_thread_turn ON vectora_native_messages(thread_id, turn_id) WHERE turn_id IS NOT NULL"
            )
        self._is_setup = True

    async def create_session(
        self,
        thread_id: str,
        *,
        user_id: str,
        workspace_id: str | None = None,
        parent_thread_id: str | None = None,
        mode: str = "chat",
        permission_mode: str = "ask",
    ) -> None:
        await self.setup()
        agora = _now()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO vectora_native_sessions (thread_id, user_id, workspace_id, "
                "parent_thread_id, mode, permission_mode, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
                "ON CONFLICT (thread_id) DO NOTHING",
                thread_id,
                user_id,
                workspace_id,
                parent_thread_id,
                mode,
                permission_mode,
                agora,
                agora,
            )

    async def append_message(
        self,
        thread_id: str,
        msg: VMessage,
        *,
        parent_message_id: int | None = None,
        turn_id: str | None = None,
    ) -> int:
        """Persiste `msg` e devolve o `id` gerado — mesmo invariante de fork
        de `SessionStore.append_message` (a mensagem nova vira a ponta ativa
        da branch, sem apagar nenhuma mensagem existente)."""
        await self.setup()
        agora = _now()
        role, content_json, tool_calls_json, tool_call_id, name = _message_to_row(msg)
        async with self._pool.acquire() as conn, conn.transaction():
            # Lock de linha na sessão serializa `append_message` concorrente
            # na mesma thread — sem isso, dois INSERTs em paralelo sob READ
            # COMMITTED não enxergam a linha um do outro e o UPDATE seguinte
            # (`is_branch_head = FALSE WHERE id != new_id`) deixa as duas
            # mensagens marcadas como ponta ativa.
            await conn.execute(
                "SELECT 1 FROM vectora_native_sessions WHERE thread_id = $1 FOR UPDATE",
                thread_id,
            )
            if turn_id is not None:
                existing_id = await conn.fetchval(
                    "SELECT id FROM vectora_native_messages "
                    "WHERE thread_id = $1 AND turn_id = $2",
                    thread_id,
                    turn_id,
                )
                if existing_id is not None:
                    return int(existing_id)
            new_id = await conn.fetchval(
                "INSERT INTO vectora_native_messages (thread_id, parent_message_id, role, "
                "content_json, tool_calls_json, tool_call_id, name, turn_id, "
                "is_branch_head, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE, $9) "
                "RETURNING id",
                thread_id,
                parent_message_id,
                role,
                content_json,
                tool_calls_json,
                tool_call_id,
                name,
                turn_id,
                agora,
            )
            await conn.execute(
                "UPDATE vectora_native_messages SET is_branch_head = FALSE "
                "WHERE thread_id = $1 AND id != $2",
                thread_id,
                new_id,
            )
            await conn.execute(
                "UPDATE vectora_native_sessions SET updated_at = $1 WHERE thread_id = $2",
                agora,
                thread_id,
            )
        return int(new_id)

    async def get_message_id_by_turn_id(
        self, thread_id: str, turn_id: str
    ) -> int | None:
        """Returns the persisted message for an idempotent turn."""
        await self.setup()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM vectora_native_messages WHERE thread_id = $1 AND turn_id = $2",
                thread_id,
                turn_id,
            )
        return int(row["id"]) if row is not None else None

    async def get_branch_head_id(self, thread_id: str) -> int | None:
        """`id` da ponta ativa da branch, ou `None` se a thread ainda não
        tem mensagem — mesmo papel de `SessionStore.get_branch_head_id`."""
        await self.setup()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM vectora_native_messages WHERE thread_id = $1 "
                "AND is_branch_head = TRUE ORDER BY id DESC LIMIT 1",
                thread_id,
            )
        return row["id"] if row is not None else None

    async def get_history(
        self, thread_id: str, *, up_to_message_id: int | None = None
    ) -> list[VMessage]:
        """Reconstrói a cadeia seguindo `parent_message_id` — mesmo
        invariante de `SessionStore.get_history` (reload/resume por
        reconstrução, nunca por estado só em memória).

        Reconstrói via uma única query recursiva (`WITH RECURSIVE`) em vez
        de uma leitura por mensagem — mesma otimização e mesmo motivo de
        `session_store.py::get_history_with_ids` (o loop antigo fazia N
        round-trips sequenciais pra uma thread de N mensagens)."""
        await self.setup()
        async with self._pool.acquire() as conn:
            if up_to_message_id is not None:
                start_id: int | None = up_to_message_id
            else:
                row = await conn.fetchrow(
                    "SELECT id FROM vectora_native_messages WHERE thread_id = $1 "
                    "AND is_branch_head = TRUE ORDER BY id DESC LIMIT 1",
                    thread_id,
                )
                start_id = row["id"] if row is not None else None

            if start_id is None:
                return []

            rows = await conn.fetch(
                "WITH RECURSIVE chain(id, parent_message_id, role, "
                "content_json, tool_calls_json, tool_call_id, name, depth) AS ("
                "  SELECT id, parent_message_id, role, content_json, "
                "    tool_calls_json, tool_call_id, name, 0"
                "  FROM vectora_native_messages WHERE thread_id = $1 AND id = $2"
                "  UNION ALL"
                "  SELECT m.id, m.parent_message_id, m.role, "
                "    m.content_json, m.tool_calls_json, m.tool_call_id, "
                "    m.name, chain.depth + 1"
                "  FROM vectora_native_messages m "
                "    JOIN chain ON m.id = chain.parent_message_id"
                "  WHERE m.thread_id = $1 AND chain.depth < $3"
                ")"
                "SELECT id, parent_message_id, role, content_json, "
                "tool_calls_json, tool_call_id, name "
                "FROM chain ORDER BY depth DESC",
                thread_id,
                start_id,
                _CHAIN_DEPTH_CAP - 1,
            )
            cadeia = list(rows)

        if len(cadeia) >= _CHAIN_DEPTH_CAP:
            erro = (
                f"ciclo detectado em parent_message_id da thread "
                f"'{thread_id}' (cadeia excedeu {_CHAIN_DEPTH_CAP} elos)"
            )
            raise RuntimeError(erro)

        return [_row_to_message(row) for row in cadeia]

    async def get_history_with_ids(
        self, thread_id: str, *, up_to_message_id: int | None = None
    ) -> list[tuple[int, VMessage]]:
        """Reconstrói uma cadeia e preserva os IDs de mensagem."""
        await self.setup()
        async with self._pool.acquire() as conn:
            if up_to_message_id is None:
                row = await conn.fetchrow(
                    "SELECT id FROM vectora_native_messages WHERE thread_id = $1 "
                    "AND is_branch_head = TRUE ORDER BY id DESC LIMIT 1",
                    thread_id,
                )
                start_id = row["id"] if row is not None else None
            else:
                start_id = up_to_message_id
            if start_id is None:
                return []
            rows = await conn.fetch(
                "WITH RECURSIVE chain(id, parent_message_id, role, content_json, "
                "tool_calls_json, tool_call_id, name, depth) AS ("
                "SELECT id, parent_message_id, role, content_json, tool_calls_json, "
                "tool_call_id, name, 0 FROM vectora_native_messages "
                "WHERE thread_id = $1 AND id = $2 UNION ALL "
                "SELECT m.id, m.parent_message_id, m.role, m.content_json, "
                "m.tool_calls_json, m.tool_call_id, m.name, chain.depth + 1 "
                "FROM vectora_native_messages m JOIN chain ON m.id = chain.parent_message_id "
                "WHERE m.thread_id = $1 AND chain.depth < $3) "
                "SELECT id, parent_message_id, role, content_json, tool_calls_json, "
                "tool_call_id, name FROM chain ORDER BY depth DESC",
                thread_id,
                start_id,
                _CHAIN_DEPTH_CAP - 1,
            )
        if len(rows) >= _CHAIN_DEPTH_CAP:
            raise RuntimeError(f"cadeia excedeu {_CHAIN_DEPTH_CAP} elos")
        return [(int(row["id"]), _row_to_message(row)) for row in rows]

    async def set_branch_head(
        self, thread_id: str, message_id: int, *, allow_internal: bool = False
    ) -> None:
        """Marca `message_id` como a ponta ativa da thread.

        `message_id` precisa pertencer a `thread_id`; caso contrário a
        thread ficaria sem nenhuma ponta ativa (histórico "sumiria")."""
        await self.setup()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.fetchval(
                "SELECT 1 FROM vectora_native_sessions WHERE thread_id = $1 FOR UPDATE",
                thread_id,
            )
            if allow_internal:
                query = (
                    "SELECT 1 FROM vectora_native_messages "
                    "WHERE thread_id = $1 AND id = $2"
                )
            else:
                query = (
                    "SELECT 1 FROM vectora_native_messages m "
                    "WHERE m.thread_id = $1 AND m.id = $2 "
                    "AND NOT EXISTS (SELECT 1 FROM vectora_native_messages d "
                    "WHERE d.thread_id = m.thread_id AND d.parent_message_id = m.id)"
                )
            exists = await conn.fetchval(query, thread_id, message_id)
            if exists is None:
                erro = f"mensagem {message_id} não pertence à thread '{thread_id}'"
                raise ValueError(erro)
            await conn.execute(
                "UPDATE vectora_native_messages SET is_branch_head = FALSE "
                "WHERE thread_id = $1",
                thread_id,
            )
            await conn.execute(
                "UPDATE vectora_native_messages SET is_branch_head = TRUE "
                "WHERE thread_id = $1 AND id = $2",
                thread_id,
                message_id,
            )
            await conn.execute(
                "UPDATE vectora_native_sessions SET updated_at = NOW() WHERE thread_id = $1",
                thread_id,
            )

    async def list_branch_heads(
        self, thread_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Lista folhas da conversa, incluindo branches antigas."""
        await self.setup()
        bounded_limit = max(1, min(limit, 500))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT m.id, m.created_at, m.is_branch_head "
                "FROM vectora_native_messages m "
                "WHERE m.thread_id = $1 AND NOT EXISTS ("
                "SELECT 1 FROM vectora_native_messages d "
                "WHERE d.thread_id = m.thread_id AND d.parent_message_id = m.id) "
                "ORDER BY m.created_at DESC, m.id DESC LIMIT $2",
                thread_id,
                bounded_limit,
            )
        result: list[dict[str, Any]] = []
        for row in rows:
            head_id = int(row["id"])
            history = await self.get_history_with_ids(
                thread_id, up_to_message_id=head_id
            )
            result.append(
                {
                    "head_message_id": head_id,
                    "created_at": str(row["created_at"]),
                    "active": bool(row["is_branch_head"]),
                    "message_count": len(history),
                }
            )
        return result

    async def compare_branches(
        self, thread_id: str, selected_head_id: int
    ) -> dict[str, Any]:
        """Compara a ponta ativa com outra ponta da mesma thread."""
        await self.setup()
        async with self._pool.acquire() as conn:
            is_leaf = await conn.fetchval(
                "SELECT 1 FROM vectora_native_messages m WHERE m.thread_id = $1 "
                "AND m.id = $2 AND NOT EXISTS (SELECT 1 FROM vectora_native_messages d "
                "WHERE d.thread_id = m.thread_id AND d.parent_message_id = m.id)",
                thread_id,
                selected_head_id,
            )
        if is_leaf is None:
            raise ValueError(f"mensagem {selected_head_id} não é uma ponta")
        selected = await self.get_history_with_ids(
            thread_id, up_to_message_id=selected_head_id
        )
        if not selected or selected[-1][0] != selected_head_id:
            raise ValueError(
                f"mensagem {selected_head_id} não pertence à thread '{thread_id}'"
            )
        active_head = await self.get_branch_head_id(thread_id)
        active = (
            await self.get_history_with_ids(thread_id, up_to_message_id=active_head)
            if active_head is not None
            else []
        )
        active_ids = [item[0] for item in active]
        selected_ids = [item[0] for item in selected]
        common = 0
        for left, right in zip(active_ids, selected_ids, strict=False):
            if left != right:
                break
            common += 1
        result = {
            "active_head_message_id": active_head,
            "selected_head_message_id": selected_head_id,
            "common_message_ids": active_ids[:common],
            "active_divergent_message_ids": active_ids[common:],
            "selected_divergent_message_ids": selected_ids[common:],
        }
        max_items = 2000
        if len(active_ids) + len(selected_ids) > max_items:
            raise ValueError("comparação de branches excede o limite permitido")
        return result

    async def get_pending_approval(self, thread_id: str) -> dict[str, Any] | None:
        await self.setup()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT interrupt_id, tool_name, tool_call_id, args_json, "
                "reasoning, options_json, priority, expires_at, created_at FROM vectora_native_pending_approvals "
                "WHERE thread_id = $1",
                thread_id,
            )
        if row is None:
            return None
        if row["expires_at"] and row["expires_at"] <= _now():
            await self.clear_pending_approval(thread_id)
            return None
        return {
            "interrupt_id": row["interrupt_id"],
            "tool_name": row["tool_name"],
            "tool_call_id": row["tool_call_id"],
            "args": json.loads(row["args_json"]),
            "reasoning": row["reasoning"],
            "options": json.loads(row["options_json"] or "[]"),
            "priority": int(row["priority"]),
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
        }

    async def claim_pending_approval(
        self, thread_id: str, *, interrupt_id: str
    ) -> dict[str, Any] | None:
        """Atomically consume a matching pending approval for one resumer."""
        await self.setup()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "DELETE FROM vectora_native_pending_approvals "
                "WHERE thread_id = $1 AND interrupt_id = $2 "
                "RETURNING interrupt_id, tool_name, tool_call_id, args_json, reasoning, "
                "options_json, priority, expires_at, created_at",
                thread_id,
                interrupt_id,
            )
        if row is None or (row["expires_at"] and row["expires_at"] <= _now()):
            return None
        return {
            "interrupt_id": row["interrupt_id"],
            "tool_name": row["tool_name"],
            "tool_call_id": row["tool_call_id"],
            "args": json.loads(row["args_json"]),
            "reasoning": row["reasoning"],
            "options": json.loads(row["options_json"] or "[]"),
            "priority": int(row["priority"]),
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
        }

    async def put_pending_approval(
        self,
        thread_id: str,
        *,
        interrupt_id: str,
        tool_name: str,
        tool_call_id: str,
        args: dict[str, Any],
        reasoning: str | None = None,
        options: list[dict[str, str]] | None = None,
        priority: int = 0,
        expires_at: str | None = None,
    ) -> None:
        await self.setup()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO vectora_native_pending_approvals (thread_id, interrupt_id, "
                "tool_name, tool_call_id, args_json, reasoning, options_json, priority, expires_at, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
                "ON CONFLICT (thread_id) DO UPDATE SET "
                "interrupt_id = EXCLUDED.interrupt_id, "
                "tool_name = EXCLUDED.tool_name, "
                "tool_call_id = EXCLUDED.tool_call_id, "
                "args_json = EXCLUDED.args_json, "
                "reasoning = EXCLUDED.reasoning, "
                "options_json = EXCLUDED.options_json, "
                "priority = EXCLUDED.priority, "
                "expires_at = EXCLUDED.expires_at, "
                "created_at = EXCLUDED.created_at",
                thread_id,
                interrupt_id,
                tool_name,
                tool_call_id,
                json.dumps(args, ensure_ascii=False),
                reasoning,
                json.dumps(options or [], ensure_ascii=False),
                priority,
                expires_at,
                _now(),
            )

    async def clear_pending_approval(self, thread_id: str) -> None:
        await self.setup()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM vectora_native_pending_approvals WHERE thread_id = $1",
                thread_id,
            )

    async def record_approval_decision(
        self,
        thread_id: str,
        *,
        interrupt_id: str,
        tool_name: str,
        decision: str,
        selection: str | None,
        decided_by: str | None,
        options: list[dict[str, str]],
        priority: int,
        expires_at: str | None,
    ) -> None:
        """Persist the final HITL decision before clearing its pending state."""
        await self.setup()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO vectora_native_approval_decisions "
                "(thread_id, interrupt_id, tool_name, decision, selection, decided_by, "
                "decided_at, options_json, priority, expires_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                thread_id,
                interrupt_id,
                tool_name,
                decision,
                selection,
                decided_by,
                _now(),
                json.dumps(options, ensure_ascii=False),
                priority,
                expires_at,
            )
