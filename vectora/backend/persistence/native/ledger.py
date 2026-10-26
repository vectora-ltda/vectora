"""Ledger de eventos tipado e determinístico por thread.

Formaliza os 9 tipos canônicos de eventos, as funções puras de derivação de chave
de idempotência (`event_key`) e os tipos notificáveis para badges e notificações.
Também define os modelos de resumo da thread e do rascunho de execução.
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from backend.storage.sqlite.pool import AsyncConnectionPool


class LedgerEventType:
    """Tipos canônicos de eventos registrados no ledger."""

    MESSAGE_CREATED = "message_created"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    AGENT_TURN_STARTED = "agent_turn_started"
    AGENT_TURN_FINISHED = "agent_turn_finished"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    RUN_FAILED = "run_failed"
    RUN_COMPLETED = "run_completed"


type LedgerEventTypeLiteral = Literal[
    "message_created",
    "tool_started",
    "tool_finished",
    "agent_turn_started",
    "agent_turn_finished",
    "approval_requested",
    "approval_resolved",
    "run_failed",
    "run_completed",
]

# Eventos que impactam contadores de badge / não lidos para o usuário
# Conforme a especificação: apenas mensagens do assistente, pedidos de aprovação HITL e falhas de run.
NOTIFIABLE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "message_created",  # filtrado adicionalmente por role == 'assistant'
        "approval_requested",
        "run_failed",
    }
)


def _now() -> str:
    """Executa a operação now."""
    return datetime.now(UTC).isoformat()


class ThreadLedgerEvent(BaseModel):
    """Evento canônico registrado no ledger durável da thread."""

    thread_id: str
    seq: int = Field(default=0, description="Sequência monótona crescente por thread")
    event_key: str = Field(description="Chave única de idempotência do evento")
    type: LedgerEventTypeLiteral
    turn_id: str | None = None
    run_id: str | None = None
    message_id: int | None = None
    tool_call_id: str | None = None
    interrupt_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)


class ThreadLastMessage(BaseModel):
    """Prévia da última mensagem no resumo da thread."""

    role: str
    preview: str
    message_id: int
    created_at: str


class ThreadSummary(BaseModel):
    """Resumo derivado diretamente do ledger e da sessão."""

    thread_id: str
    last_seq: int = 0
    last_message: ThreadLastMessage | None = None
    status: Literal["running", "awaiting_approval", "failed", "idle"] = "idle"
    read_seq: int = 0
    unread_count: int = 0


class RunDraft(BaseModel):
    """Rascunho persistido de um run em andamento."""

    thread_id: str
    run_id: str
    content: str = ""
    tool_calls_json: str | None = None
    updated_at: str = Field(default_factory=_now)


# --- Funções puras de derivação de event_key idempotente ---


def make_message_created_key(message_id: int | str) -> str:
    """Executa a operação make message created key."""
    return f"message_created:{message_id}"


def make_tool_started_key(run_id: str, tool_call_id: str) -> str:
    """Executa a operação make tool started key."""
    return f"tool_started:{run_id}:{tool_call_id}"


def make_tool_finished_key(run_id: str, tool_call_id: str) -> str:
    """Executa a operação make tool finished key."""
    return f"tool_finished:{run_id}:{tool_call_id}"


def make_agent_turn_started_key(run_id: str, turn_id: str | None = None) -> str:
    """Executa a operação make agent turn started key."""
    if turn_id:
        return f"agent_turn_started:{run_id}:{turn_id}"
    return f"agent_turn_started:{run_id}"


def make_agent_turn_finished_key(run_id: str, turn_id: str | None = None) -> str:
    """Executa a operação make agent turn finished key."""
    if turn_id:
        return f"agent_turn_finished:{run_id}:{turn_id}"
    return f"agent_turn_finished:{run_id}"


def make_approval_requested_key(interrupt_id: str) -> str:
    """Executa a operação make approval requested key."""
    return f"approval_requested:{interrupt_id}"


def make_approval_resolved_key(interrupt_id: str) -> str:
    """Executa a operação make approval resolved key."""
    return f"approval_resolved:{interrupt_id}"


def make_run_failed_key(run_id: str) -> str:
    """Executa a operação make run failed key."""
    return f"run_failed:{run_id}"


def make_run_completed_key(run_id: str) -> str:
    """Executa a operação make run completed key."""
    return f"run_completed:{run_id}"


def is_event_notifiable(event: ThreadLedgerEvent) -> bool:
    """Retorna True se o evento deve incrementar contador de badge não-lido."""
    if event.type not in NOTIFIABLE_EVENT_TYPES:
        return False
    if event.type == "message_created":
        role = event.payload.get("role")
        return role == "assistant"
    return True


async def backfill_thread_ledger(
    pool: AsyncConnectionPool,
    thread_id: str,
    *,
    legacy_read_count: int = 0,
    user_id: str = "",
) -> tuple[int, int]:
    """Executa a migração/backfill idempotente de uma thread legada para o ledger.

    - Popula `thread_events` com `message_created` para cada mensagem da tabela `messages`
      em ordem crescente de `id`.
    - Converte `legacy_read_count` para `last_seen_seq` na N-ésima mensagem de assistente.
    - Grava o status e estatísticas em `ledger_backfill`.
    - Retorna `(migrated_messages, last_seq)`.
    """
    now = _now()
    async with pool.acquire() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cur_session = await conn.execute(
                "SELECT 1 FROM sessions WHERE thread_id = ? LIMIT 1",
                (thread_id,),
            )
            if await cur_session.fetchone() is None:
                await conn.rollback()
                return 0, 0

            # 1. Verificar se já migrou
            cur_bf = await conn.execute(
                "SELECT status, migrated_messages, last_seq FROM ledger_backfill WHERE thread_id = ?",
                (thread_id,),
            )
            row_bf = await cur_bf.fetchone()
            if row_bf and row_bf[0] == "done":
                await conn.rollback()
                return int(row_bf[1]), int(row_bf[2])

            # Marcar como pending
            await conn.execute(
                "INSERT INTO ledger_backfill (thread_id, status, started_at) "
                "VALUES (?, 'pending', ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET status='pending', started_at=excluded.started_at",
                (thread_id, now),
            )

            # 2. Ler todas as mensagens da thread em ordem de id
            cur_msgs = await conn.execute(
                "SELECT id, role, content_json, tool_call_id, is_error, turn_id, run_id, created_at "
                "FROM messages WHERE thread_id = ? ORDER BY id ASC",
                (thread_id,),
            )
            msgs = await cur_msgs.fetchall()

            migrated_count = 0
            assistant_seq_list: list[int] = []
            cur_events = await conn.execute(
                "SELECT event_key, seq, created_at FROM thread_events "
                "WHERE thread_id = ? ORDER BY seq ASC",
                (thread_id,),
            )
            existing_events = list(await cur_events.fetchall())
            missing: list[tuple[tuple[object, ...], dict[str, object]]] = []
            for m in msgs:
                m_id, role, c_json, tc_id, is_err, turn_id, run_id, created_at = m
                event_key = make_message_created_key(m_id)

                # Verificar se já existe evento pra essa mensagem
                cur_ev = await conn.execute(
                    "SELECT seq FROM thread_events WHERE thread_id = ? AND event_key = ?",
                    (thread_id, event_key),
                )
                row_ev = await cur_ev.fetchone()
                if row_ev is None:
                    try:
                        blocks = json.loads(c_json or "[]")
                        text_val = "".join(
                            b.get("text", "") for b in blocks if isinstance(b, dict)
                        )
                    except Exception:
                        text_val = ""

                    payload = {
                        "role": role,
                        "content": text_val,
                        "is_error": bool(is_err),
                    }
                    missing.append(
                        (
                            (str(created_at or now), 0, int(m_id)),
                            {
                                "event_key": event_key,
                                "role": role,
                                "turn_id": turn_id,
                                "run_id": run_id,
                                "message_id": m_id,
                                "tool_call_id": tc_id,
                                "payload_json": json.dumps(payload, ensure_ascii=False),
                                "created_at": created_at or now,
                            },
                        )
                    )
                    migrated_count += 1

            if missing:
                # A lazy backfill can run after live events were appended.  Rebuild
                # the per-thread sequence atomically so legacy messages are ordered
                # by their original timestamp instead of being appended after newer
                # events.  Cursors are remapped by their old sequence below.
                current_seq = len(existing_events) + len(missing)
                descriptors: list[tuple[tuple[object, ...], str, dict[str, object]]] = [
                    ((str(row[2] or now), 1, int(row[1])), str(row[0]), {})
                    for row in existing_events
                ]
                descriptors.extend(
                    (sort_key, str(data["event_key"]), data)
                    for sort_key, data in missing
                )
                descriptors.sort(key=lambda item: item[0])
                remap = {
                    int(row[1]): index
                    for index, (_, key, _) in enumerate(descriptors, start=1)
                    for row in existing_events
                    if str(row[0]) == key
                }
                if existing_events:
                    await conn.execute(
                        "UPDATE thread_events SET seq = -1000000000 - seq "
                        "WHERE thread_id = ?",
                        (thread_id,),
                    )
                for index, (_, key, data) in enumerate(descriptors, start=1):
                    if data:
                        await conn.execute(
                            "INSERT INTO thread_events (thread_id, seq, event_key, type, "
                            "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                            "VALUES (?, ?, ?, 'message_created', ?, ?, ?, ?, NULL, ?, ?)",
                            (
                                thread_id,
                                -1000000000 - index,
                                key,
                                data["turn_id"],
                                data["run_id"],
                                data["message_id"],
                                data["tool_call_id"],
                                data["payload_json"],
                                data["created_at"],
                            ),
                        )
                    await conn.execute(
                        "UPDATE thread_events SET seq = ? WHERE thread_id = ? AND event_key = ?",
                        (index, thread_id, key),
                    )
                for old_seq, new_seq in remap.items():
                    await conn.execute(
                        "UPDATE thread_event_cursors SET last_seen_seq = ? "
                        "WHERE thread_id = ? AND last_seen_seq = ?",
                        (new_seq, thread_id, old_seq),
                    )

            current_seq = max(
                (
                    int(row[0])
                    for row in await (
                        await conn.execute(
                            "SELECT seq FROM thread_events WHERE thread_id = ?",
                            (thread_id,),
                        )
                    ).fetchall()
                ),
                default=0,
            )
            cur_ordered = await conn.execute(
                "SELECT seq, payload_json FROM thread_events WHERE thread_id = ? "
                "AND type = 'message_created' ORDER BY seq ASC",
                (thread_id,),
            )
            for row in await cur_ordered.fetchall():
                payload = json.loads(row[1] or "{}")
                if payload.get("role") == "assistant":
                    assistant_seq_list.append(int(row[0]))

            # 3. Converter legacy_read_count para last_seen_seq
            if user_id:
                if legacy_read_count <= 0:
                    cursor_seq = 0
                elif legacy_read_count <= len(assistant_seq_list):
                    cursor_seq = assistant_seq_list[legacy_read_count - 1]
                else:
                    cursor_seq = current_seq

                if cursor_seq > 0:
                    await conn.execute(
                        "INSERT INTO thread_event_cursors (thread_id, user_id, device_id, last_seen_seq, updated_at) "
                        "VALUES (?, ?, '', ?, ?) "
                        "ON CONFLICT(thread_id, user_id, device_id) DO UPDATE SET "
                        "last_seen_seq = MAX(thread_event_cursors.last_seen_seq, excluded.last_seen_seq), "
                        "updated_at = excluded.updated_at",
                        (thread_id, user_id, cursor_seq, now),
                    )

            # 4. Finalizar backfill
            await conn.execute(
                "UPDATE ledger_backfill SET status = 'done', migrated_messages = ?, "
                "last_seq = ?, completed_at = ? WHERE thread_id = ?",
                (migrated_count, current_seq, now, thread_id),
            )
            await conn.commit()
            return migrated_count, current_seq
        except Exception as exc:
            await conn.rollback()
            try:
                await conn.execute(
                    "INSERT INTO ledger_backfill (thread_id, status, error, started_at, completed_at) "
                    "VALUES (?, 'failed', ?, ?, ?) "
                    "ON CONFLICT(thread_id) DO UPDATE SET status='failed', error=excluded.error, completed_at=excluded.completed_at",
                    (thread_id, str(exc), now, _now()),
                )
                await conn.commit()
            except Exception:
                # A failure while recording the diagnostic state must not hide
                # the original backfill error. Roll back the reused connection
                # and keep the original exception as the one raised to the
                # caller.
                with contextlib.suppress(Exception):
                    await conn.rollback()
                logger.exception(
                    "ledger backfill: failed to persist failure state for %s",
                    thread_id,
                )
            raise


async def ensure_thread_backfilled(
    pool: AsyncConnectionPool,
    thread_id: str,
    *,
    user_id: str = "",
    legacy_read_count: int = 0,
) -> bool:
    """Garante de forma preguiçosa que o ledger da thread está populado."""
    async with pool.acquire() as conn:
        # A FK de ``ledger_backfill`` aponta para ``sessions``.  Verificar a
        # sessão antes de delegar ao backfill evita retornar sucesso para IDs
        # inexistentes e preserva o erro original quando o chamador tenta
        # gravar uma thread que não foi criada.
        cur_session = await conn.execute(
            "SELECT 1 FROM sessions WHERE thread_id = ? LIMIT 1", (thread_id,)
        )
        if await cur_session.fetchone() is None:
            return False
        cur = await conn.execute(
            "SELECT status FROM ledger_backfill WHERE thread_id = ?", (thread_id,)
        )
        row = await cur.fetchone()
        if row and row[0] == "done":
            return True

    await backfill_thread_ledger(
        pool,
        thread_id,
        legacy_read_count=legacy_read_count,
        user_id=user_id,
    )
    return True
