"""Ledger de eventos tipado e determinístico por thread.

Formaliza os 9 tipos canônicos de eventos, as funções puras de derivação de chave
de idempotência (`event_key`) e os tipos notificáveis para badges e notificações.
Também define os modelos de resumo da thread e do rascunho de execução.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from backend.storage.sqlite.pool import AsyncConnectionPool

LedgerEventType = Literal[
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
    return datetime.now(UTC).isoformat()


class ThreadLedgerEvent(BaseModel):
    """Evento canônico registrado no ledger durável da thread."""

    thread_id: str
    seq: int = Field(default=0, description="Sequência monótona crescente por thread")
    event_key: str = Field(description="Chave única de idempotência do evento")
    type: LedgerEventType
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
    return f"message_created:{message_id}"


def make_tool_started_key(run_id: str, tool_call_id: str) -> str:
    return f"tool_started:{run_id}:{tool_call_id}"


def make_tool_finished_key(run_id: str, tool_call_id: str) -> str:
    return f"tool_finished:{run_id}:{tool_call_id}"


def make_agent_turn_started_key(run_id: str, turn_id: str | None = None) -> str:
    if turn_id:
        return f"agent_turn_started:{run_id}:{turn_id}"
    return f"agent_turn_started:{run_id}"


def make_agent_turn_finished_key(run_id: str, turn_id: str | None = None) -> str:
    if turn_id:
        return f"agent_turn_finished:{run_id}:{turn_id}"
    return f"agent_turn_finished:{run_id}"


def make_approval_requested_key(interrupt_id: str) -> str:
    return f"approval_requested:{interrupt_id}"


def make_approval_resolved_key(interrupt_id: str) -> str:
    return f"approval_resolved:{interrupt_id}"


def make_run_failed_key(run_id: str) -> str:
    return f"run_failed:{run_id}"


def make_run_completed_key(run_id: str) -> str:
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
            # 1. Verificar se a thread existe em sessions; se não, registrar como sessão legada para satisfazer FK
            cur_sess = await conn.execute(
                "SELECT thread_id FROM sessions WHERE thread_id = ?", (thread_id,)
            )
            if not await cur_sess.fetchone():
                await conn.execute(
                    "INSERT INTO sessions (thread_id, user_id, mode, permission_mode, created_at, updated_at) "
                    "VALUES (?, ?, 'chat', 'ask', ?, ?) ON CONFLICT(thread_id) DO NOTHING",
                    (thread_id, user_id or "local", now, now),
                )

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

            # Obter max_seq existente no ledger
            cur_max = await conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM thread_events WHERE thread_id = ?",
                (thread_id,),
            )
            row_max = await cur_max.fetchone()
            current_seq = int(row_max[0]) if row_max else 0

            migrated_count = 0
            assistant_seq_list: list[int] = []

            for m in msgs:
                m_id, role, c_json, tc_id, is_err, turn_id, run_id, created_at = m
                event_key = make_message_created_key(m_id)

                # Verificar se já existe evento pra essa mensagem
                cur_ev = await conn.execute(
                    "SELECT seq FROM thread_events WHERE thread_id = ? AND event_key = ?",
                    (thread_id, event_key),
                )
                row_ev = await cur_ev.fetchone()
                if row_ev is not None:
                    event_seq = int(row_ev[0])
                else:
                    current_seq += 1
                    event_seq = current_seq
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
                    await conn.execute(
                        "INSERT INTO thread_events (thread_id, seq, event_key, type, "
                        "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                        "VALUES (?, ?, ?, 'message_created', ?, ?, ?, ?, NULL, ?, ?)",
                        (
                            thread_id,
                            event_seq,
                            event_key,
                            turn_id,
                            run_id,
                            m_id,
                            tc_id,
                            json.dumps(payload, ensure_ascii=False),
                            created_at or now,
                        ),
                    )
                    migrated_count += 1

                if role == "assistant":
                    assistant_seq_list.append(event_seq)

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
            async with pool.acquire() as conn_err:
                await conn_err.execute(
                    "INSERT INTO ledger_backfill (thread_id, status, error, started_at, completed_at) "
                    "VALUES (?, 'failed', ?, ?, ?) "
                    "ON CONFLICT(thread_id) DO UPDATE SET status='failed', error=excluded.error, completed_at=excluded.completed_at",
                    (thread_id, str(exc), now, _now()),
                )
                await conn_err.commit()
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
