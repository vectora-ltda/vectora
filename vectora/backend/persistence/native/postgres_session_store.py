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
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from backend.persistence.native.agent_run import (
    TERMINAL_AGENT_RUN_STATUSES,
    VALID_AGENT_RUN_STATUSES,
    AgentRun,
    AgentRunMetrics,
    AgentRunStatus,
    AgentRunTimelineEntry,
    redact_sensitive_data,
)
from backend.persistence.native.ledger import (
    NOTIFIABLE_EVENT_TYPES,
    LedgerEventType,
    LedgerEventTypeLiteral,
    RunDraft,
    ThreadLastMessage,
    ThreadLedgerEvent,
    ThreadSummary,
    make_approval_requested_key,
    make_approval_resolved_key,
    make_message_created_key,
)
from backend.persistence.native.schema import setup_postgres_session_schema
from backend.vtypes.contracts import ToolOption
from backend.vtypes.message import VMessage

if TYPE_CHECKING:
    import asyncpg


# Teto de segurança pra reconstrução recursiva da cadeia de mensagens
# (get_history) — nenhuma conversa legítima chega perto disso; existe só
# pra um `parent_message_id` corrompido/cíclico não fazer a CTE recursar
# sem fim. Mesmo valor/motivo de `session_store.py::_CHAIN_DEPTH_CAP`.
_CHAIN_DEPTH_CAP = 100_000

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _message_to_row(
    msg: VMessage,
) -> tuple[str, str, str | None, str | None, str | None]:
    data = msg.to_dict()
    for block in data["content"]:
        if block.get("kind") == "image_url" and (
            block.get("asset_id")
            or block.get("attachment_name")
            or str(block.get("image_url") or "").casefold().startswith("data:")
        ):
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
        await setup_postgres_session_schema(self._pool)
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
                "ON CONFLICT (thread_id) DO UPDATE SET "
                "workspace_id = CASE WHEN EXCLUDED.mode = 'chat' "
                "THEN EXCLUDED.workspace_id ELSE COALESCE(EXCLUDED.workspace_id, vectora_native_sessions.workspace_id) END, "
                "mode = EXCLUDED.mode, permission_mode = EXCLUDED.permission_mode, "
                "updated_at = EXCLUDED.updated_at",
                thread_id,
                user_id,
                workspace_id,
                parent_thread_id,
                mode,
                permission_mode,
                agora,
                agora,
            )
            await conn.execute(
                "INSERT INTO vectora_sessions (thread_id, user_id, mode, permission_mode) "
                "VALUES ($1, $2, $3, $4) ON CONFLICT (thread_id) DO UPDATE SET "
                "user_id = EXCLUDED.user_id, mode = EXCLUDED.mode, "
                "permission_mode = EXCLUDED.permission_mode, last_activity = now()",
                thread_id,
                user_id,
                mode,
                permission_mode,
            )

    async def get_session(
        self, thread_id: str, *, user_id: str | None = None
    ) -> dict[str, Any] | None:
        """Retorna metadados nativos para reidratar um HITL após restart."""
        await self.setup()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT thread_id, user_id, workspace_id, parent_thread_id, mode, "
                "permission_mode, created_at, updated_at "
                "FROM vectora_native_sessions WHERE thread_id = $1",
                thread_id,
            )
        if row is None or (user_id is not None and row["user_id"] != user_id):
            return None
        return {
            "thread_id": row["thread_id"],
            "user_id": row["user_id"],
            "workspace_id": row["workspace_id"],
            "parent_thread_id": row["parent_thread_id"],
            "mode": row["mode"],
            "permission_mode": row["permission_mode"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def append_message(
        self,
        thread_id: str,
        msg: VMessage,
        *,
        parent_message_id: int | None = None,
        turn_id: str | None = None,
        run_id: str | None = None,
    ) -> int:
        """Persiste `msg` e devolve o `id` gerado, delegando para `append_message_recorded`."""
        msg_id, _ = await self.append_message_recorded(
            thread_id,
            msg,
            parent_message_id=parent_message_id,
            turn_id=turn_id,
            run_id=run_id,
        )
        return msg_id

    async def append_message_recorded(
        self,
        thread_id: str,
        msg: VMessage,
        *,
        parent_message_id: int | None = None,
        turn_id: str | None = None,
        run_id: str | None = None,
        extra_events: list[ThreadLedgerEvent] | None = None,
    ) -> tuple[int, int]:
        """Persiste `msg` e o evento `message_created` atomicamente no ledger Postgres,
        devolvendo `(message_id, seq)`."""
        await self.setup()
        agora = _now()
        role, content_json, tool_calls_json, tool_call_id, name = _message_to_row(msg)
        async with self._pool.acquire() as conn, conn.transaction():
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
                    row_seq = await conn.fetchval(
                        "SELECT seq FROM vectora_native_thread_events WHERE thread_id = $1 AND message_id = $2",
                        thread_id,
                        int(existing_id),
                    )
                    seq = int(row_seq) if row_seq is not None else 0
                    return int(existing_id), seq

            new_id = await conn.fetchval(
                "INSERT INTO vectora_native_messages (thread_id, parent_message_id, role, "
                "content_json, tool_calls_json, tool_call_id, name, turn_id, run_id, "
                "edited_files_json, is_branch_head, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, TRUE, $11) "
                "RETURNING id",
                thread_id,
                parent_message_id,
                role,
                content_json,
                tool_calls_json,
                tool_call_id,
                name,
                turn_id,
                None,
                run_id,
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

            # Obter próximo seq do ledger
            max_seq = await conn.fetchval(
                "SELECT COALESCE(MAX(seq), 0) FROM vectora_native_thread_events WHERE thread_id = $1",
                thread_id,
            )
            current_seq = int(max_seq or 0)

            if extra_events:
                for extra in extra_events:
                    current_seq += 1
                    await conn.execute(
                        "INSERT INTO vectora_native_thread_events (thread_id, seq, event_key, type, "
                        "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
                        "ON CONFLICT (thread_id, event_key) DO NOTHING",
                        thread_id,
                        current_seq,
                        extra.event_key,
                        extra.type,
                        extra.turn_id,
                        extra.run_id,
                        extra.message_id,
                        extra.tool_call_id,
                        extra.interrupt_id,
                        json.dumps(extra.payload, ensure_ascii=False),
                        extra.created_at or agora,
                    )

            current_seq += 1
            msg_event_key = make_message_created_key(new_id)
            msg_payload = {
                "role": role,
                "content": msg.text(),
                "is_error": bool(msg.is_error),
            }
            await conn.execute(
                "INSERT INTO vectora_native_thread_events (thread_id, seq, event_key, type, "
                "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
                "ON CONFLICT (thread_id, event_key) DO NOTHING",
                thread_id,
                current_seq,
                msg_event_key,
                "message_created",
                turn_id,
                run_id,
                new_id,
                tool_call_id,
                None,
                json.dumps(msg_payload, ensure_ascii=False),
                agora,
            )

            # Exclusão atômica de rascunho de run no commit do assistente
            if run_id is not None and role == "assistant" and not msg.tool_calls:
                await conn.execute(
                    "DELETE FROM vectora_native_run_drafts WHERE thread_id = $1 AND run_id = $2",
                    thread_id,
                    run_id,
                )

        return int(new_id), current_seq

    async def attach_edited_files(
        self, thread_id: str, run_id: str, files: list[dict[str, object]]
    ) -> None:
        """Persiste os arquivos finais associados a um run de resposta."""
        await self.setup()
        payload = json.dumps(files, ensure_ascii=False)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE vectora_native_messages SET edited_files_json = $1 "
                "WHERE id = (SELECT MAX(id) FROM vectora_native_messages "
                "WHERE thread_id = $2 AND run_id = $3 AND role = 'assistant')",
                payload,
                thread_id,
                run_id,
            )

    async def get_edited_files_for_thread(
        self, thread_id: str
    ) -> dict[int, list[dict[str, object]]]:
        """Retorna os payloads de arquivos persistidos por mensagem."""
        await self.setup()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, edited_files_json FROM vectora_native_messages "
                "WHERE thread_id = $1 AND edited_files_json IS NOT NULL",
                thread_id,
            )
        result: dict[int, list[dict[str, object]]] = {}
        for row in rows:
            try:
                value = json.loads(row["edited_files_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(value, list):
                result[int(row["id"])] = [
                    item for item in value if isinstance(item, dict)
                ]
        return result

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
                "SELECT interrupt_id, tool_name, tool_call_id, run_id, args_json, "
                "reasoning, options_json, priority, expires_at, created_at FROM vectora_native_pending_approvals "
                "WHERE thread_id = $1",
                thread_id,
            )
        if row is None:
            return None
        if row["expires_at"] and row["expires_at"] <= _now():
            agora = _now()
            try:
                async with self._pool.acquire() as conn, conn.transaction():
                    await conn.execute(
                        "SELECT 1 FROM vectora_native_sessions WHERE thread_id = $1 FOR UPDATE",
                        thread_id,
                    )
                    deleted = await conn.fetchval(
                        "DELETE FROM vectora_native_pending_approvals "
                        "WHERE thread_id = $1 AND interrupt_id = $2 "
                        "AND expires_at IS NOT NULL AND expires_at <= $3 "
                        "RETURNING 1",
                        thread_id,
                        row["interrupt_id"],
                        agora,
                    )
                    if not deleted:
                        return None
                    max_seq = await conn.fetchval(
                        "SELECT COALESCE(MAX(seq), 0) FROM vectora_native_thread_events WHERE thread_id = $1",
                        thread_id,
                    )
                    current_seq = int(max_seq or 0) + 1
                    resolved_key = make_approval_resolved_key(row["interrupt_id"])
                    payload = {
                        "tool_name": row["tool_name"],
                        "tool_call_id": row["tool_call_id"],
                        "interrupt_id": row["interrupt_id"],
                        "expired": True,
                    }
                    await conn.execute(
                        "INSERT INTO vectora_native_thread_events (thread_id, seq, event_key, type, "
                        "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
                        "ON CONFLICT (thread_id, event_key) DO NOTHING",
                        thread_id,
                        current_seq,
                        resolved_key,
                        "approval_resolved",
                        None,
                        None,
                        None,
                        row["tool_call_id"],
                        row["interrupt_id"],
                        json.dumps(payload, ensure_ascii=False),
                        agora,
                    )
            except Exception:
                logger.exception(
                    "postgres_session_store: falha ao expirar pending approval para thread %s",
                    thread_id,
                )
            return None
        return {
            "interrupt_id": row["interrupt_id"],
            "tool_name": row["tool_name"],
            "tool_call_id": row["tool_call_id"],
            "run_id": row["run_id"],
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
        """Atomically consume a matching pending approval for one resumer,
        recording an approval_resolved event in the ledger on the same transaction."""
        await self.setup()
        agora = _now()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT 1 FROM vectora_native_sessions WHERE thread_id = $1 FOR UPDATE",
                thread_id,
            )
            row = await conn.fetchrow(
                "DELETE FROM vectora_native_pending_approvals "
                "WHERE thread_id = $1 AND interrupt_id = $2 "
                "RETURNING interrupt_id, tool_name, tool_call_id, run_id, args_json, reasoning, "
                "options_json, priority, expires_at, created_at",
                thread_id,
                interrupt_id,
            )
            if row is None:
                return None
            is_expired = bool(row["expires_at"] and row["expires_at"] <= _now())

            max_seq = await conn.fetchval(
                "SELECT COALESCE(MAX(seq), 0) FROM vectora_native_thread_events WHERE thread_id = $1",
                thread_id,
            )
            current_seq = int(max_seq or 0) + 1
            resolved_key = make_approval_resolved_key(row["interrupt_id"])
            payload = {
                "tool_name": row["tool_name"],
                "tool_call_id": row["tool_call_id"],
                "interrupt_id": row["interrupt_id"],
                "expired": is_expired,
            }
            await conn.execute(
                "INSERT INTO vectora_native_thread_events (thread_id, seq, event_key, type, "
                "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
                "ON CONFLICT (thread_id, event_key) DO NOTHING",
                thread_id,
                current_seq,
                resolved_key,
                "approval_resolved",
                None,
                None,
                None,
                row["tool_call_id"],
                row["interrupt_id"],
                json.dumps(payload, ensure_ascii=False),
                agora,
            )

        if is_expired:
            return None

        return {
            "interrupt_id": row["interrupt_id"],
            "tool_name": row["tool_name"],
            "tool_call_id": row["tool_call_id"],
            "run_id": row["run_id"],
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
        run_id: str | None = None,
        args: dict[str, Any],
        reasoning: str | None = None,
        options: list[ToolOption] | None = None,
        priority: int = 0,
        expires_at: str | None = None,
    ) -> None:
        await self.setup()
        agora = _now()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT 1 FROM vectora_native_sessions WHERE thread_id = $1 FOR UPDATE",
                thread_id,
            )
            await conn.execute(
                "INSERT INTO vectora_native_pending_approvals (thread_id, interrupt_id, "
                "tool_name, tool_call_id, run_id, args_json, reasoning, options_json, priority, expires_at, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
                "ON CONFLICT (thread_id) DO UPDATE SET "
                "interrupt_id = EXCLUDED.interrupt_id, "
                "tool_name = EXCLUDED.tool_name, "
                "tool_call_id = EXCLUDED.tool_call_id, "
                "run_id = EXCLUDED.run_id, "
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
                run_id,
                json.dumps(args, ensure_ascii=False),
                reasoning,
                json.dumps(options or [], ensure_ascii=False),
                priority,
                expires_at,
                agora,
            )
            max_seq = await conn.fetchval(
                "SELECT COALESCE(MAX(seq), 0) FROM vectora_native_thread_events WHERE thread_id = $1",
                thread_id,
            )
            current_seq = int(max_seq or 0) + 1
            req_key = make_approval_requested_key(interrupt_id)
            payload = {
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "run_id": run_id,
                "args": args,
                "reasoning": reasoning,
                "priority": priority,
            }
            await conn.execute(
                "INSERT INTO vectora_native_thread_events (thread_id, seq, event_key, type, "
                "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
                "ON CONFLICT (thread_id, event_key) DO NOTHING",
                thread_id,
                current_seq,
                req_key,
                "approval_requested",
                None,
                run_id,
                None,
                tool_call_id,
                interrupt_id,
                json.dumps(payload, ensure_ascii=False),
                agora,
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
        options: list[ToolOption],
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

    async def delete_session(self, thread_id: str) -> None:
        """Apaga `thread_id` e todos os seus registros de mensagens, eventos, cursores e rascunhos."""
        await self.setup()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM vectora_native_pending_approvals WHERE thread_id = $1",
                thread_id,
            )
            await conn.execute(
                "DELETE FROM vectora_native_messages WHERE thread_id = $1",
                thread_id,
            )
            await conn.execute(
                "DELETE FROM vectora_native_thread_events WHERE thread_id = $1",
                thread_id,
            )
            await conn.execute(
                "DELETE FROM vectora_native_thread_event_cursors WHERE thread_id = $1",
                thread_id,
            )
            await conn.execute(
                "DELETE FROM vectora_native_run_drafts WHERE thread_id = $1",
                thread_id,
            )
            await conn.execute(
                "DELETE FROM vectora_native_agent_runs WHERE thread_id = $1",
                thread_id,
            )
            await conn.execute(
                "DELETE FROM vectora_native_ledger_backfill WHERE thread_id = $1",
                thread_id,
            )
            await conn.execute(
                "DELETE FROM vectora_native_sessions WHERE thread_id = $1",
                thread_id,
            )

    # -----------------------------------------------------------------------
    # Ledger de Eventos, Cursores de Leitura e Rascunhos (Postgres)
    # -----------------------------------------------------------------------

    async def append_event(
        self, thread_id: str, draft: ThreadLedgerEvent
    ) -> ThreadLedgerEvent:
        """Gera seq monótono e insere o evento no ledger Postgres."""
        await self.setup()
        agora = _now()
        created_at = draft.created_at or agora
        payload_str = json.dumps(draft.payload, ensure_ascii=False)
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT 1 FROM vectora_native_sessions WHERE thread_id = $1 FOR UPDATE",
                thread_id,
            )
            cur_existing = await conn.fetchrow(
                "SELECT thread_id, seq, event_key, type, turn_id, run_id, "
                "message_id, tool_call_id, interrupt_id, payload_json, created_at "
                "FROM vectora_native_thread_events WHERE thread_id = $1 AND event_key = $2",
                thread_id,
                draft.event_key,
            )
            if cur_existing is not None:
                return ThreadLedgerEvent(
                    thread_id=cur_existing["thread_id"],
                    seq=cur_existing["seq"],
                    event_key=cur_existing["event_key"],
                    type=cur_existing["type"],
                    turn_id=cur_existing["turn_id"],
                    run_id=cur_existing["run_id"],
                    message_id=cur_existing["message_id"],
                    tool_call_id=cur_existing["tool_call_id"],
                    interrupt_id=cur_existing["interrupt_id"],
                    payload=json.loads(cur_existing["payload_json"] or "{}"),
                    created_at=cur_existing["created_at"],
                )

            max_seq = await conn.fetchval(
                "SELECT COALESCE(MAX(seq), 0) FROM vectora_native_thread_events WHERE thread_id = $1",
                thread_id,
            )
            next_seq = int(max_seq or 0) + 1

            await conn.execute(
                "INSERT INTO vectora_native_thread_events (thread_id, seq, event_key, type, "
                "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
                thread_id,
                next_seq,
                draft.event_key,
                draft.type,
                draft.turn_id,
                draft.run_id,
                draft.message_id,
                draft.tool_call_id,
                draft.interrupt_id,
                payload_str,
                created_at,
            )

        return ThreadLedgerEvent(
            thread_id=thread_id,
            seq=next_seq,
            event_key=draft.event_key,
            type=draft.type,
            turn_id=draft.turn_id,
            run_id=draft.run_id,
            message_id=draft.message_id,
            tool_call_id=draft.tool_call_id,
            interrupt_id=draft.interrupt_id,
            payload=draft.payload,
            created_at=created_at,
        )

    async def append_ledger_event(
        self,
        thread_id: str,
        *,
        event_key: str,
        event_type: LedgerEventType | str,
        run_id: str | None = None,
        turn_id: str | None = None,
        message_id: int | None = None,
        tool_call_id: str | None = None,
        interrupt_id: str | None = None,
        payload: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> ThreadLedgerEvent:
        """Helper para registrar eventos no ledger Postgres usando argumentos nomeados."""
        event = ThreadLedgerEvent(
            thread_id=thread_id,
            event_key=event_key,
            type=cast("LedgerEventTypeLiteral", event_type),
            run_id=run_id,
            turn_id=turn_id,
            message_id=message_id,
            tool_call_id=tool_call_id,
            interrupt_id=interrupt_id,
            payload=payload or {},
            created_at=created_at or _now(),
        )
        return await self.append_event(thread_id, event)

    async def list_events(
        self, thread_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[ThreadLedgerEvent]:
        """Lista eventos do ledger ordenados por seq no Postgres."""
        await self.setup()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT e.thread_id, e.seq, e.event_key, e.type, e.turn_id, e.run_id, "
                "e.message_id, e.tool_call_id, e.interrupt_id, e.payload_json, e.created_at, "
                "m.role, m.content_json "
                "FROM vectora_native_thread_events e "
                "LEFT JOIN vectora_native_messages m ON e.message_id = m.id "
                "WHERE e.thread_id = $1 AND e.seq > $2 "
                "ORDER BY e.seq ASC LIMIT $3",
                thread_id,
                after_seq,
                limit,
            )

        events: list[ThreadLedgerEvent] = []
        for r in rows:
            payload = json.loads(r["payload_json"] or "{}")
            if r["type"] == "message_created" and r["role"] is not None:
                if not payload.get("role"):
                    payload["role"] = r["role"]
                if "content" not in payload and r["content_json"]:
                    try:
                        blocks = json.loads(r["content_json"])
                        text_val = "".join(
                            b.get("text", "") for b in blocks if isinstance(b, dict)
                        )
                        payload["content"] = text_val
                    except Exception:
                        pass

            events.append(
                ThreadLedgerEvent(
                    thread_id=r["thread_id"],
                    seq=r["seq"],
                    event_key=r["event_key"],
                    type=r["type"],
                    turn_id=r["turn_id"],
                    run_id=r["run_id"],
                    message_id=r["message_id"],
                    tool_call_id=r["tool_call_id"],
                    interrupt_id=r["interrupt_id"],
                    payload=payload,
                    created_at=r["created_at"],
                )
            )
        return events

    async def get_event_seq_by_message_id(
        self, thread_id: str, message_id: int | str
    ) -> int | None:
        """Localiza o seq do evento message_created correspondente a message_id."""
        await self.setup()
        mid_int = int(message_id) if str(message_id).isdigit() else None
        if mid_int is None:
            return None
        async with self._pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT seq FROM vectora_native_thread_events WHERE thread_id = $1 AND type = 'message_created' "
                "AND message_id = $2 ORDER BY seq DESC LIMIT 1",
                thread_id,
                mid_int,
            )
            return int(val) if val is not None else None

    async def mark_events_seen(
        self, thread_id: str, user_id: str, device_id: str, seen_seq: int
    ) -> dict[str, int]:
        """Atualiza cursor no Postgres limitado ao MAX(seq)."""
        await self.setup()
        now = _now()
        async with self._pool.acquire() as conn:
            max_seq = await conn.fetchval(
                "SELECT COALESCE(MAX(seq), 0) FROM vectora_native_thread_events WHERE thread_id = $1",
                thread_id,
            )
            clamped_seq = max(0, min(seen_seq, int(max_seq or 0)))

            await conn.execute(
                "INSERT INTO vectora_native_thread_event_cursors "
                "(thread_id, user_id, device_id, last_seen_seq, updated_at) "
                "VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (thread_id, user_id, device_id) DO UPDATE SET "
                "last_seen_seq = GREATEST(vectora_native_thread_event_cursors.last_seen_seq, EXCLUDED.last_seen_seq), "
                "updated_at = EXCLUDED.updated_at",
                thread_id,
                user_id,
                device_id,
                clamped_seq,
                now,
            )

        summaries = await self.get_thread_summaries([thread_id], user_id=user_id)
        if summaries:
            return {
                "read_seq": summaries[0].read_seq,
                "unread_count": summaries[0].unread_count,
            }
        return {"read_seq": clamped_seq, "unread_count": 0}

    async def get_thread_summaries(
        self, thread_ids: list[str], *, user_id: str
    ) -> list[ThreadSummary]:
        """Resumos derivados no Postgres."""
        if not thread_ids:
            return []
        await self.setup()
        async with self._pool.acquire() as conn:
            rows_max = await conn.fetch(
                "SELECT thread_id, COALESCE(MAX(seq), 0) as last_seq "
                "FROM vectora_native_thread_events "
                "WHERE thread_id = ANY($1::text[]) "
                "GROUP BY thread_id",
                thread_ids,
            )
            max_seqs = {r["thread_id"]: r["last_seq"] for r in rows_max}

            status_rows = await conn.fetch(
                "SELECT thread_id, type, payload_json FROM ("
                "  SELECT thread_id, type, payload_json, "
                "  ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY seq DESC) AS rn "
                "  FROM vectora_native_thread_events "
                "  WHERE thread_id = ANY($1::text[]) "
                "  AND type = ANY($2::text[])"
                ") latest WHERE rn = 1",
                thread_ids,
                [
                    "run_completed",
                    "agent_turn_finished",
                    "run_failed",
                    "approval_resolved",
                    "approval_requested",
                    "agent_turn_started",
                ],
            )
            cursor_rows = await conn.fetch(
                "WITH requested AS (SELECT UNNEST($2::text[]) AS thread_id), "
                "cursors AS (SELECT thread_id, COALESCE(MAX(last_seen_seq), 0) AS read_seq "
                "FROM vectora_native_thread_event_cursors WHERE user_id = $1 "
                "AND thread_id = ANY($2::text[]) GROUP BY thread_id) "
                "SELECT r.thread_id, COALESCE(c.read_seq, 0) AS read_seq, "
                "COALESCE(SUM(CASE WHEN e.seq > COALESCE(c.read_seq, 0) AND "
                "(e.type = ANY($3::text[]) OR (e.type = 'message_created' AND "
                "e.payload_json::jsonb->>'role' = 'assistant')) THEN 1 ELSE 0 END), 0) "
                "AS unread_count FROM requested r "
                "LEFT JOIN cursors c ON c.thread_id = r.thread_id "
                "LEFT JOIN vectora_native_thread_events e ON e.thread_id = r.thread_id "
                "GROUP BY r.thread_id, c.read_seq",
                user_id,
                thread_ids,
                sorted(
                    event_type
                    for event_type in NOTIFIABLE_EVENT_TYPES
                    if event_type != "message_created"
                ),
            )
            cursors = {r["thread_id"]: int(r["read_seq"]) for r in cursor_rows}
            unread_counts = {
                str(r["thread_id"]): int(r["unread_count"]) for r in cursor_rows
            }

            rows_last_msg = await conn.fetch(
                "SELECT m.thread_id, m.id, m.role, m.content_json, m.created_at "
                "FROM vectora_native_messages m "
                "INNER JOIN ("
                "  SELECT thread_id, MAX(id) as max_id FROM vectora_native_messages "
                "  WHERE thread_id = ANY($1::text[]) GROUP BY thread_id"
                ") latest ON m.thread_id = latest.thread_id AND m.id = latest.max_id",
                thread_ids,
            )
            last_msgs = {}
            for r in rows_last_msg:
                try:
                    blocks = json.loads(r["content_json"] or "[]")
                    preview = "".join(
                        b.get("text", "") for b in blocks if isinstance(b, dict)
                    )
                except Exception:
                    preview = ""
                last_msgs[r["thread_id"]] = ThreadLastMessage(
                    role=r["role"],
                    preview=preview,
                    message_id=r["id"],
                    created_at=r["created_at"],
                )

        status_by_thread = {
            str(r["thread_id"]): (str(r["type"]), r["payload_json"])
            for r in status_rows
        }

        summaries: list[ThreadSummary] = []
        for tid in thread_ids:
            read_seq = int(cursors.get(tid, 0))
            last_seq = int(max_seqs.get(tid, 0))
            status = "idle"
            status_row = status_by_thread.get(tid)
            if status_row:
                event_type, payload_json = status_row
                if event_type == "run_failed":
                    status = "failed"
                elif event_type == "approval_requested":
                    status = "awaiting_approval"
                elif event_type == "agent_turn_started":
                    status = "running"
                elif event_type == "approval_resolved":
                    payload = json.loads(payload_json or "{}")
                    status = "idle" if payload.get("expired", False) else "running"

            unread_count = unread_counts.get(tid, 0)

            summaries.append(
                ThreadSummary(
                    thread_id=tid,
                    last_seq=last_seq,
                    last_message=last_msgs.get(tid),
                    status=status,
                    read_seq=read_seq,
                    unread_count=unread_count,
                )
            )
        return summaries

    async def upsert_run_draft(
        self,
        thread_id: str,
        run_id: str,
        *,
        content: str = "",
        tool_calls_json: str | None = None,
    ) -> None:
        await self.setup()
        now = _now()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO vectora_native_run_drafts (thread_id, run_id, content, tool_calls_json, updated_at) "
                "VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (thread_id, run_id) DO UPDATE SET "
                "content = EXCLUDED.content, tool_calls_json = EXCLUDED.tool_calls_json, "
                "updated_at = EXCLUDED.updated_at",
                thread_id,
                run_id,
                content,
                tool_calls_json,
                now,
            )

    async def get_run_draft(self, thread_id: str, run_id: str) -> RunDraft | None:
        await self.setup()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT thread_id, run_id, content, tool_calls_json, updated_at "
                "FROM vectora_native_run_drafts WHERE thread_id = $1 AND run_id = $2",
                thread_id,
                run_id,
            )
        if row is None:
            return None
        return RunDraft(
            thread_id=row["thread_id"],
            run_id=row["run_id"],
            content=row["content"],
            tool_calls_json=row["tool_calls_json"],
            updated_at=row["updated_at"],
        )

    async def delete_run_draft(self, thread_id: str, run_id: str) -> None:
        await self.setup()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM vectora_native_run_drafts WHERE thread_id = $1 AND run_id = $2",
                thread_id,
                run_id,
            )

    async def find_orphan_runs(self) -> list[tuple[str, str]]:
        await self.setup()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT e1.thread_id, e1.run_id FROM vectora_native_thread_events e1 "
                "WHERE e1.type = 'agent_turn_started' AND e1.run_id IS NOT NULL "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM vectora_native_thread_events e2 "
                "  WHERE e2.thread_id = e1.thread_id AND e2.run_id = e1.run_id "
                "  AND e2.type IN ('run_completed', 'run_failed', 'agent_turn_finished')"
                ")"
            )
        return [(r["thread_id"], r["run_id"]) for r in rows]

    async def _upsert_agent_run_on_conn(
        self, conn: asyncpg.Connection, run: AgentRun
    ) -> None:
        """Persiste ou atualiza uma execução do agente na conexão PostgreSQL fornecida."""
        now = _now()
        terminal_at = (
            (run.terminal_at or now)
            if run.status in TERMINAL_AGENT_RUN_STATUSES
            else run.terminal_at
        )
        redacted_plan_step = (
            cast("str | None", redact_sensitive_data(run.plan_step))
            if run.plan_step is not None
            else None
        )
        redacted_detail = (
            cast("str | None", redact_sensitive_data(run.detail))
            if run.detail is not None
            else None
        )
        redacted_error_code = (
            cast("str | None", redact_sensitive_data(run.error_code))
            if run.error_code is not None
            else None
        )
        tools_available = json.dumps(
            redact_sensitive_data(run.tools_available), ensure_ascii=False
        )
        tools_used = json.dumps(
            redact_sensitive_data(run.tools_used), ensure_ascii=False
        )
        approvals = json.dumps(redact_sensitive_data(run.approvals), ensure_ascii=False)
        files_touched = json.dumps(
            redact_sensitive_data(run.files_touched), ensure_ascii=False
        )
        commands_run = json.dumps(
            redact_sensitive_data(run.commands_run), ensure_ascii=False
        )
        tests_run = json.dumps(redact_sensitive_data(run.tests_run), ensure_ascii=False)
        artifacts = json.dumps(redact_sensitive_data(run.artifacts), ensure_ascii=False)
        metrics = json.dumps(run.metrics.model_dump(), ensure_ascii=False)
        evidence = json.dumps(redact_sensitive_data(run.evidence), ensure_ascii=False)

        await conn.execute(
            "INSERT INTO vectora_native_agent_runs ("
            "run_id, thread_id, contract_version, turn_id, user_id, workspace_id, model, provider, "
            "status, plan_step, checkpoint_id, tools_available_json, tools_used_json, approvals_json, "
            "files_touched_json, commands_run_json, tests_run_json, artifacts_json, "
            "metrics_json, evidence_json, detail, error_code, started_at, updated_at, terminal_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25) "
            "ON CONFLICT(run_id) DO UPDATE SET "
            "contract_version = EXCLUDED.contract_version, "
            "status = CASE WHEN vectora_native_agent_runs.status IN ('passed', 'degraded', 'failed', 'cancelled') "
            "THEN vectora_native_agent_runs.status ELSE EXCLUDED.status END, "
            "plan_step = COALESCE(EXCLUDED.plan_step, vectora_native_agent_runs.plan_step), "
            "checkpoint_id = COALESCE(EXCLUDED.checkpoint_id, vectora_native_agent_runs.checkpoint_id), "
            "tools_available_json = EXCLUDED.tools_available_json, "
            "tools_used_json = EXCLUDED.tools_used_json, "
            "approvals_json = EXCLUDED.approvals_json, "
            "files_touched_json = EXCLUDED.files_touched_json, "
            "commands_run_json = EXCLUDED.commands_run_json, "
            "tests_run_json = EXCLUDED.tests_run_json, "
            "artifacts_json = EXCLUDED.artifacts_json, "
            "metrics_json = EXCLUDED.metrics_json, "
            "evidence_json = EXCLUDED.evidence_json, "
            "detail = COALESCE(EXCLUDED.detail, vectora_native_agent_runs.detail), "
            "error_code = COALESCE(EXCLUDED.error_code, vectora_native_agent_runs.error_code), "
            "updated_at = CASE WHEN vectora_native_agent_runs.status IN ('passed', 'degraded', 'failed', 'cancelled') "
            "THEN vectora_native_agent_runs.updated_at ELSE EXCLUDED.updated_at END, "
            "terminal_at = COALESCE(vectora_native_agent_runs.terminal_at, EXCLUDED.terminal_at)",
            run.run_id,
            run.thread_id,
            run.contract_version,
            run.turn_id,
            run.user_id,
            run.workspace_id,
            run.model,
            run.provider,
            run.status,
            redacted_plan_step,
            run.checkpoint_id,
            tools_available,
            tools_used,
            approvals,
            files_touched,
            commands_run,
            tests_run,
            artifacts,
            metrics,
            evidence,
            redacted_detail,
            redacted_error_code,
            run.started_at,
            now,
            terminal_at,
        )

    async def upsert_agent_run(
        self,
        run: AgentRun,
    ) -> None:
        """Cria ou atualiza uma execução do agente no PostgreSQL com redação de segredos."""
        await self.setup()
        async with self._pool.acquire() as conn:
            await self._upsert_agent_run_on_conn(conn, run)

    async def update_agent_run_status(
        self,
        run_id: str,
        *,
        status: AgentRunStatus,
        plan_step: str | None = None,
        detail: str | None = None,
        error_code: str | None = None,
        metrics: AgentRunMetrics | None = None,
    ) -> None:
        """Transiciona o status de uma run atomicamente."""
        if status not in VALID_AGENT_RUN_STATUSES:
            raise ValueError(f"status de AgentRun inválido: {status!r}")
        await self.setup()
        now = _now()
        terminal_at = now if status in TERMINAL_AGENT_RUN_STATUSES else None
        redacted_plan_step = (
            cast("str | None", redact_sensitive_data(plan_step))
            if plan_step is not None
            else None
        )
        redacted_detail = (
            cast("str | None", redact_sensitive_data(detail))
            if detail is not None
            else None
        )
        redacted_error_code = (
            cast("str | None", redact_sensitive_data(error_code))
            if error_code is not None
            else None
        )
        metrics_json = (
            json.dumps(metrics.model_dump(), ensure_ascii=False) if metrics else None
        )

        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE vectora_native_agent_runs SET "
                "status = $1, "
                "plan_step = COALESCE($2, plan_step), "
                "detail = COALESCE($3, detail), "
                "error_code = COALESCE($4, error_code), "
                "metrics_json = COALESCE($5, metrics_json), "
                "updated_at = $6, "
                "terminal_at = COALESCE(terminal_at, $7) "
                "WHERE run_id = $8 "
                "AND status NOT IN ('passed', 'degraded', 'failed', 'cancelled')",
                status,
                redacted_plan_step,
                redacted_detail,
                redacted_error_code,
                metrics_json,
                now,
                terminal_at,
                run_id,
            )

    async def _get_agent_run_on_conn(
        self, conn: asyncpg.Connection, run_id: str, *, for_update: bool = False
    ) -> AgentRun | None:
        """Carrega e desserializa um AgentRun na conexão fornecida."""
        query = (
            "SELECT run_id, thread_id, contract_version, turn_id, user_id, workspace_id, "
            "model, provider, status, plan_step, checkpoint_id, tools_available_json, "
            "tools_used_json, approvals_json, files_touched_json, commands_run_json, "
            "tests_run_json, artifacts_json, metrics_json, evidence_json, "
            "detail, error_code, started_at, updated_at, terminal_at "
            "FROM vectora_native_agent_runs WHERE run_id = $1"
        )
        if for_update:
            query += " FOR UPDATE"
        row = await conn.fetchrow(query, run_id)
        if row is None:
            return None
        return AgentRun(
            run_id=str(row["run_id"]),
            thread_id=str(row["thread_id"]),
            contract_version=int(row["contract_version"] or 1),
            turn_id=str(row["turn_id"]) if row["turn_id"] else None,
            user_id=str(row["user_id"] or ""),
            workspace_id=str(row["workspace_id"]) if row["workspace_id"] else None,
            model=str(row["model"] or ""),
            provider=str(row["provider"] or ""),
            status=cast("AgentRunStatus", row["status"]),
            plan_step=str(row["plan_step"]) if row["plan_step"] else None,
            checkpoint_id=str(row["checkpoint_id"]) if row["checkpoint_id"] else None,
            tools_available=json.loads(row["tools_available_json"] or "[]"),
            tools_used=json.loads(row["tools_used_json"] or "[]"),
            approvals=json.loads(row["approvals_json"] or "[]"),
            files_touched=json.loads(row["files_touched_json"] or "[]"),
            commands_run=json.loads(row["commands_run_json"] or "[]"),
            tests_run=json.loads(row["tests_run_json"] or "[]"),
            artifacts=json.loads(row["artifacts_json"] or "[]"),
            metrics=AgentRunMetrics(**json.loads(row["metrics_json"] or "{}")),
            evidence=json.loads(row["evidence_json"] or "[]"),
            detail=str(row["detail"]) if row["detail"] else None,
            error_code=str(row["error_code"]) if row["error_code"] else None,
            started_at=str(row["started_at"]),
            updated_at=str(row["updated_at"]),
            terminal_at=str(row["terminal_at"]) if row["terminal_at"] else None,
        )

    async def record_run_evidence(
        self,
        run_id: str,
        *,
        evidence: dict[str, Any] | None = None,
        artifact: dict[str, Any] | None = None,
        command: dict[str, Any] | None = None,
        test_result: dict[str, Any] | None = None,
        tool_used: str | None = None,
        approval: dict[str, Any] | None = None,
        file_touched: dict[str, Any] | None = None,
    ) -> None:
        """Registra evidências incrementais no AgentRun de forma atômica."""
        await self.setup()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                run = await self._get_agent_run_on_conn(conn, run_id, for_update=True)
                if run is None:
                    return
                if evidence:
                    run.evidence.append(redact_sensitive_data(evidence))
                if artifact:
                    run.artifacts.append(redact_sensitive_data(artifact))
                if command:
                    run.commands_run.append(redact_sensitive_data(command))
                if test_result:
                    run.tests_run.append(redact_sensitive_data(test_result))
                if tool_used and tool_used not in run.tools_used:
                    run.tools_used.append(tool_used)
                if approval:
                    run.approvals.append(redact_sensitive_data(approval))
                if file_touched:
                    run.files_touched.append(redact_sensitive_data(file_touched))
                await self._upsert_agent_run_on_conn(conn, run)

    async def get_agent_run(self, run_id: str) -> AgentRun | None:
        """Obtém uma execução persistida por run_id."""
        await self.setup()
        async with self._pool.acquire() as conn:
            return await self._get_agent_run_on_conn(conn, run_id)

    async def list_agent_runs(
        self, thread_id: str, *, limit: int = 50
    ) -> list[AgentRun]:
        """Lista todas as execuções da thread ordenadas pelas mais recentes primeiro."""
        await self.setup()
        safe_limit = max(1, min(500, int(limit)))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT run_id, thread_id, contract_version, turn_id, user_id, workspace_id, "
                "model, provider, status, plan_step, checkpoint_id, tools_available_json, "
                "tools_used_json, approvals_json, files_touched_json, commands_run_json, "
                "tests_run_json, artifacts_json, metrics_json, evidence_json, "
                "detail, error_code, started_at, updated_at, terminal_at "
                "FROM vectora_native_agent_runs WHERE thread_id = $1 "
                "ORDER BY started_at DESC LIMIT $2",
                thread_id,
                safe_limit,
            )
            return [
                AgentRun(
                    run_id=str(r["run_id"]),
                    thread_id=str(r["thread_id"]),
                    contract_version=int(r["contract_version"] or 1),
                    turn_id=str(r["turn_id"]) if r["turn_id"] else None,
                    user_id=str(r["user_id"] or ""),
                    workspace_id=str(r["workspace_id"]) if r["workspace_id"] else None,
                    model=str(r["model"] or ""),
                    provider=str(r["provider"] or ""),
                    status=cast("AgentRunStatus", r["status"]),
                    plan_step=str(r["plan_step"]) if r["plan_step"] else None,
                    checkpoint_id=str(r["checkpoint_id"])
                    if r["checkpoint_id"]
                    else None,
                    tools_available=json.loads(r["tools_available_json"] or "[]"),
                    tools_used=json.loads(r["tools_used_json"] or "[]"),
                    approvals=json.loads(r["approvals_json"] or "[]"),
                    files_touched=json.loads(r["files_touched_json"] or "[]"),
                    commands_run=json.loads(r["commands_run_json"] or "[]"),
                    tests_run=json.loads(r["tests_run_json"] or "[]"),
                    artifacts=json.loads(r["artifacts_json"] or "[]"),
                    metrics=AgentRunMetrics(**json.loads(r["metrics_json"] or "{}")),
                    evidence=json.loads(r["evidence_json"] or "[]"),
                    detail=str(r["detail"]) if r["detail"] else None,
                    error_code=str(r["error_code"]) if r["error_code"] else None,
                    started_at=str(r["started_at"]),
                    updated_at=str(r["updated_at"]),
                    terminal_at=str(r["terminal_at"]) if r["terminal_at"] else None,
                )
                for r in rows
            ]

    async def get_agent_run_timeline(
        self, run_id: str, *, limit: int = 500, offset: int = 0
    ) -> list[AgentRunTimelineEntry]:
        """Recupera uma página ordenada dos eventos correlacionados ao run."""
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        await self.setup()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT seq, event_key, type, created_at, payload_json "
                "FROM vectora_native_thread_events WHERE run_id = $1 "
                "ORDER BY seq ASC LIMIT $2 OFFSET $3",
                run_id,
                limit,
                offset,
            )
            return [
                AgentRunTimelineEntry(
                    seq=r["seq"],
                    event_key=str(r["event_key"]),
                    type=str(r["type"]),
                    created_at=str(r["created_at"]),
                    payload=json.loads(r["payload_json"] or "{}"),
                )
                for r in rows
            ]
