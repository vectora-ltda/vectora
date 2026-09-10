"""``SessionStore`` — persistência de sessões/mensagens do motor de
conversa nativo sobre ``aiosqlite``: schema append-only ``sessions`` +
``messages`` + ``pending_approvals``.

- **Fork de conversa** (editar mensagem/regenerar) via ``parent_message_id``
  — a mensagem nova aponta pro ponto da cadeia de onde diverge; mensagens
  da branch anterior nunca são apagadas, só deixam de ser ``is_branch_head``.
- **HITL sobrevivendo a restart** via ``pending_approvals`` — persistido
  IMEDIATA e SINCRONAMENTE antes de qualquer espera, nunca só em memória
  de processo.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict

from backend.vtypes.message import VMessage

if TYPE_CHECKING:
    from backend.storage.sqlite.pool import AsyncConnectionPool

_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    workspace_id TEXT,
    parent_thread_id TEXT,
    mode TEXT NOT NULL,
    permission_mode TEXT NOT NULL DEFAULT 'ask',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL REFERENCES sessions(thread_id),
    parent_message_id INTEGER,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL,
    tool_calls_json TEXT,
    tool_call_id TEXT,
    name TEXT,
    is_error INTEGER NOT NULL DEFAULT 0,
    turn_id TEXT,
    is_branch_head INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_thread ON messages(thread_id, id);
CREATE INDEX IF NOT EXISTS ix_messages_thread_parent ON messages(thread_id, parent_message_id);
CREATE TABLE IF NOT EXISTS pending_approvals (
    thread_id TEXT PRIMARY KEY REFERENCES sessions(thread_id),
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
CREATE TABLE IF NOT EXISTS approval_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
CREATE INDEX IF NOT EXISTS ix_approval_decisions_thread
    ON approval_decisions(thread_id, decided_at);
"""


# Teto de segurança pra reconstrução recursiva da cadeia de mensagens
# (get_history_with_ids) — nenhuma conversa legítima chega perto disso;
# existe só pra um `parent_message_id` corrompido/cíclico não fazer a CTE
# recursar sem fim.
_CHAIN_DEPTH_CAP = 100_000


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _message_to_row(
    msg: VMessage,
) -> tuple[str, str, str | None, str | None, str | None, int]:
    data = msg.to_dict()
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
        int(data["is_error"]),
    )


def _row_to_message(row: Any) -> VMessage:
    _id, _parent, role, content_json, tool_calls_json, tool_call_id, name, is_error = (
        row
    )
    data = {
        "role": role,
        "content": json.loads(content_json),
        "tool_calls": json.loads(tool_calls_json) if tool_calls_json else [],
        "tool_call_id": tool_call_id,
        "name": name,
        "finish_reason": None,
        "is_error": bool(is_error),
    }
    return VMessage.from_dict(data)


class SessionSummary(TypedDict):
    """Uma linha de `SessionStore.list_all_sessions` — metadados da thread
    em `sessions` mais a contagem real de mensagens em `messages`."""

    thread_id: str
    user_id: str
    workspace_id: str | None
    mode: str
    created_at: str
    updated_at: str
    message_count: int


class SessionStore:
    """Persistência async sobre um ``AsyncConnectionPool`` (aiosqlite) com os
    PRAGMAs de hardening já aplicados por conexão."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool
        self._is_setup = False

    async def setup(self) -> None:
        """Cria as tabelas se não existirem. Idempotente — chamado
        automaticamente por todo método público antes de qualquer query."""
        if self._is_setup:
            return
        async with self._pool.acquire() as conn:
            await conn.executescript(_SETUP_SQL)
            # Upgrade databases created before structured HITL decisions.
            for statement in (
                "ALTER TABLE pending_approvals ADD COLUMN options_json TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE pending_approvals ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE pending_approvals ADD COLUMN expires_at TEXT",
            ):
                try:
                    await conn.execute(statement)
                except Exception as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            columns = await conn.execute_fetchall("PRAGMA table_info(messages)")
            if not any(str(column[1]) == "turn_id" for column in columns):
                await conn.execute("ALTER TABLE messages ADD COLUMN turn_id TEXT")
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_messages_thread_turn "
                "ON messages(thread_id, turn_id) WHERE turn_id IS NOT NULL"
            )
            await conn.commit()
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
            try:
                await conn.execute(
                    "INSERT OR IGNORE INTO sessions (thread_id, user_id, workspace_id, "
                    "parent_thread_id, mode, permission_mode, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        thread_id,
                        user_id,
                        workspace_id,
                        parent_thread_id,
                        mode,
                        permission_mode,
                        agora,
                        agora,
                    ),
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def append_message(
        self,
        thread_id: str,
        msg: VMessage,
        *,
        parent_message_id: int | None = None,
        turn_id: str | None = None,
    ) -> int:
        """Persiste `msg` e devolve o `id` gerado. A mensagem nova vira a
        ponta ativa da branch (`is_branch_head`); se `parent_message_id`
        aponta pra um nó no meio da cadeia (fork — editar/regenerar), a
        branch divergente anterior nunca é apagada, só deixa de ser a
        ponta ativa dessa thread."""
        await self.setup()
        agora = _now()
        role, content_json, tool_calls_json, tool_call_id, name, is_error = (
            _message_to_row(msg)
        )
        async with self._pool.acquire() as conn:
            try:
                if turn_id is not None:
                    cur_existing = await conn.execute(
                        "SELECT id FROM messages WHERE thread_id = ? AND turn_id = ?",
                        (thread_id, turn_id),
                    )
                    existing = await cur_existing.fetchone()
                    if existing is not None:
                        return int(existing[0])
                cur = await conn.execute(
                    "INSERT INTO messages (thread_id, parent_message_id, role, "
                    "content_json, tool_calls_json, tool_call_id, name, is_error, "
                    "turn_id, is_branch_head, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (
                        thread_id,
                        parent_message_id,
                        role,
                        content_json,
                        tool_calls_json,
                        tool_call_id,
                        name,
                        is_error,
                        turn_id,
                        agora,
                    ),
                )
                new_id = cur.lastrowid
                if new_id is None:
                    erro = "INSERT em `messages` não gerou lastrowid"
                    raise RuntimeError(erro)
                await conn.execute(
                    "UPDATE messages SET is_branch_head = 0 "
                    "WHERE thread_id = ? AND id != ?",
                    (thread_id, new_id),
                )
                await conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE thread_id = ?",
                    (agora, thread_id),
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        return int(new_id)

    async def get_message_id_by_turn_id(
        self, thread_id: str, turn_id: str
    ) -> int | None:
        """Returns the persisted message for an idempotent turn."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT id FROM messages WHERE thread_id = ? AND turn_id = ?",
                (thread_id, turn_id),
            )
            row = await cur.fetchone()
        return int(row[0]) if row is not None else None

    async def get_branch_head_id(self, thread_id: str) -> int | None:
        """`id` da mensagem que é a ponta ativa da branch, ou `None` se a
        thread ainda não tem mensagem nenhuma — usado pelo caller (loop de
        conversa nativo) pra encadear `parent_message_id` ao persistir a
        próxima mensagem, sem precisar reler o histórico inteiro só pra
        descobrir o último `id`."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT id FROM messages WHERE thread_id = ? "
                "AND is_branch_head = 1 ORDER BY id DESC LIMIT 1",
                (thread_id,),
            )
            row = await cur.fetchone()
        return row[0] if row is not None else None

    async def get_history(
        self, thread_id: str, *, up_to_message_id: int | None = None
    ) -> list[VMessage]:
        """Reconstrói a cadeia de mensagens seguindo `parent_message_id` a
        partir da ponta ativa (ou de `up_to_message_id`, pra reler uma
        branch antiga sem apagar o que veio depois) até a raiz — reload/
        resume acontece por reconstrução da persistência, nunca por estado
        mantido só em memória (invariante do loop nativo)."""
        pares = await self.get_history_with_ids(
            thread_id, up_to_message_id=up_to_message_id
        )
        return [msg for _id, msg in pares]

    async def get_history_with_ids(
        self, thread_id: str, *, up_to_message_id: int | None = None
    ) -> list[tuple[int, VMessage]]:
        """Mesma reconstrução de `get_history`, mas devolve `(id, VMessage)` —
        o `id` é o alvo de fork (`set_branch_head`) para "editar e reenviar"/
        "regenerar", exposto pela API REST como `checkpoint_id`.

        Reconstrói a cadeia inteira numa única query recursiva (`WITH
        RECURSIVE`) em vez de uma leitura por mensagem — o loop anterior
        fazia N round-trips sequenciais ao SQLite pra uma thread de N
        mensagens, medido como o principal custo de latência ao abrir
        threads longas. `_CHAIN_DEPTH_CAP` limita a recursão: sem essa
        rede de segurança, um `parent_message_id` corrompido formando um
        ciclo faria a CTE recursar sem fim (o loop antigo detectava ciclo
        via um `set` de ids visitados — a query não tem como fazer o
        equivalente nativamente, então o cap + checagem em Python cobre o
        mesmo caso, ainda que sem apontar o id exato do ciclo)."""
        await self.setup()
        async with self._pool.acquire() as conn:
            if up_to_message_id is not None:
                start_id: int | None = up_to_message_id
            else:
                cur = await conn.execute(
                    "SELECT id FROM messages WHERE thread_id = ? "
                    "AND is_branch_head = 1 ORDER BY id DESC LIMIT 1",
                    (thread_id,),
                )
                row = await cur.fetchone()
                start_id = row[0] if row is not None else None

            if start_id is None:
                return []

            cur = await conn.execute(
                "WITH RECURSIVE chain(id, parent_message_id, role, "
                "content_json, tool_calls_json, tool_call_id, name, "
                "is_error, depth) AS ("
                "  SELECT id, parent_message_id, role, content_json, "
                "    tool_calls_json, tool_call_id, name, is_error, 0"
                "  FROM messages WHERE thread_id = ? AND id = ?"
                "  UNION ALL"
                "  SELECT m.id, m.parent_message_id, m.role, "
                "    m.content_json, m.tool_calls_json, m.tool_call_id, "
                "    m.name, m.is_error, chain.depth + 1"
                "  FROM messages m JOIN chain ON m.id = chain.parent_message_id"
                "  WHERE m.thread_id = ? AND chain.depth < ?"
                ")"
                "SELECT id, parent_message_id, role, content_json, "
                "tool_calls_json, tool_call_id, name, is_error "
                "FROM chain ORDER BY depth DESC",
                (thread_id, start_id, thread_id, _CHAIN_DEPTH_CAP - 1),
            )
            cadeia = list(await cur.fetchall())

        if len(cadeia) >= _CHAIN_DEPTH_CAP:
            erro = (
                f"ciclo detectado em parent_message_id da thread "
                f"'{thread_id}' (cadeia excedeu {_CHAIN_DEPTH_CAP} elos)"
            )
            raise RuntimeError(erro)

        return [(row[0], _row_to_message(row)) for row in cadeia]

    async def set_branch_head(self, thread_id: str, message_id: int) -> None:
        """Marca `message_id` como a ponta ativa da thread — fork explícito
        (editar mensagem/regenerar) sem apagar nenhuma mensagem existente.

        `message_id` precisa pertencer a `thread_id`; caso contrário a
        thread ficaria sem nenhuma ponta ativa (histórico "sumiria")."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM messages m WHERE m.thread_id = ? AND m.id = ? "
                "AND NOT EXISTS (SELECT 1 FROM messages d "
                "WHERE d.thread_id = m.thread_id AND d.parent_message_id = m.id)",
                (thread_id, message_id),
            )
            if await cur.fetchone() is None:
                erro = f"mensagem {message_id} não pertence à thread '{thread_id}'"
                raise ValueError(erro)
            try:
                await conn.execute(
                    "UPDATE messages SET is_branch_head = 0 WHERE thread_id = ?",
                    (thread_id,),
                )
                await conn.execute(
                    "UPDATE messages SET is_branch_head = 1 WHERE thread_id = ? AND id = ?",
                    (thread_id, message_id),
                )
                await conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE thread_id = ?",
                    (datetime.now(UTC).isoformat(), thread_id),
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def list_branch_heads(
        self, thread_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Lista as pontas (folhas) persistidas de uma conversa.

        A ponta ativa continua sendo marcada por ``is_branch_head``. Para
        preservar branches antigas, a enumeração também considera mensagens
        que não são pai de nenhuma outra mensagem.
        """
        await self.setup()
        bounded_limit = max(1, min(limit, 500))
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT m.id, m.created_at, m.is_branch_head, "
                "(SELECT COUNT(*) FROM messages d WHERE d.thread_id = m.thread_id "
                "AND d.parent_message_id = m.id) AS child_count "
                "FROM messages m WHERE m.thread_id = ? "
                "AND NOT EXISTS (SELECT 1 FROM messages d WHERE d.thread_id = m.thread_id "
                "AND d.parent_message_id = m.id) "
                "ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
                (thread_id, bounded_limit),
            )
            rows = await cur.fetchall()
        return [
            {
                "head_message_id": int(row[0]),
                "created_at": str(row[1]),
                "active": bool(row[2]),
                "message_count": len(
                    await self.get_history_with_ids(
                        thread_id, up_to_message_id=int(row[0])
                    )
                ),
            }
            for row in rows
        ]

    async def compare_branches(
        self, thread_id: str, selected_head_id: int
    ) -> dict[str, Any]:
        """Compara a ponta ativa com outra ponta da mesma thread."""
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

    async def get_session(
        self, thread_id: str, *, user_id: str | None = None
    ) -> dict[str, Any] | None:
        """Metadados de posse de uma sessão — fonte de verdade sobre a
        EXISTÊNCIA e o DONO (`user_id`) de uma thread no motor nativo.

        Quando `user_id` é passado, devolve `None` também quando a thread
        pertence a outro usuário — não distingue "não existe" de "não é
        sua" pro caller, evitando vazamento de existência em endpoints
        protegidos (ex.: `GetThread`/`UpdateThread` de outra pessoa)."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT thread_id, user_id, workspace_id, parent_thread_id, mode, "
                "permission_mode, created_at, updated_at FROM sessions "
                "WHERE thread_id = ?",
                (thread_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        session = {
            "thread_id": row[0],
            "user_id": row[1],
            "workspace_id": row[2],
            "parent_thread_id": row[3],
            "mode": row[4],
            "permission_mode": row[5],
            "created_at": row[6],
            "updated_at": row[7],
        }
        if user_id is not None and session["user_id"] != user_id:
            return None
        return session

    async def foreign_thread_ids(self, thread_ids: list[str], user_id: str) -> set[str]:
        """Subconjunto de `thread_ids` registrado em `sessions` com um dono
        DIFERENTE de `user_id` — usado por `ListThreads` pra nunca vazar uma
        thread de outro usuário, mesmo quando outra fonte de metadados (ex.:
        cache de UI) ainda a lista. Threads sem registro nenhum em `sessions`
        (legado, criadas antes da posse ser rastreada aqui) não entram no
        resultado — ausência de registro não é o mesmo que posse alheia."""
        if not thread_ids:
            return set()
        await self.setup()
        placeholders = ",".join("?" for _ in thread_ids)
        query = f"SELECT thread_id FROM sessions WHERE user_id != ? AND thread_id IN ({placeholders})"  # noqa: S608  # nosec B608
        async with self._pool.acquire() as conn:
            cur = await conn.execute(query, (user_id, *thread_ids))
            rows = await cur.fetchall()
        return {r[0] for r in rows}

    async def list_all_sessions(self) -> list[SessionSummary]:
        """Todas as threads registradas em `sessions` (fonte de verdade),
        com a contagem real de mensagens de cada uma — usado pela
        reconciliação de `vectora_sessions` (metadados de UI), que precisa
        comparar as duas tabelas pra achar threads reais ausentes/
        desatualizadas na tabela que a sidebar lê."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT s.thread_id, s.user_id, s.workspace_id, s.mode, "
                "s.created_at, s.updated_at, "
                "(SELECT COUNT(*) FROM messages m WHERE m.thread_id = s.thread_id) "
                "AS message_count FROM sessions s"
            )
            rows = await cur.fetchall()
        return [
            {
                "thread_id": r[0],
                "user_id": r[1],
                "workspace_id": r[2],
                "mode": r[3],
                "created_at": r[4],
                "updated_at": r[5],
                "message_count": r[6],
            }
            for r in rows
        ]

    async def list_active_user_ids(self, since_iso: str) -> list[str]:
        """`user_id` distintos com pelo menos uma sessão atualizada desde
        `since_iso` (string ISO comparável lexicograficamente com
        `updated_at`) — usado pelo scheduler de consolidação de memória
        pra saber quem teve atividade recente, sem varrer todas as threads."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT user_id FROM sessions WHERE updated_at >= ?",
                (since_iso,),
            )
            rows = await cur.fetchall()
        return [r[0] for r in rows if r[0]]

    async def get_pending_approval(self, thread_id: str) -> dict[str, Any] | None:
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT interrupt_id, tool_name, tool_call_id, args_json, "
                "reasoning, options_json, priority, expires_at, created_at "
                "FROM pending_approvals WHERE thread_id = ?",
                (thread_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        (
            interrupt_id,
            tool_name,
            tool_call_id,
            args_json,
            reasoning,
            options_json,
            priority,
            expires_at,
            created_at,
        ) = row
        if expires_at and expires_at <= _now():
            await self.clear_pending_approval(thread_id)
            return None
        return {
            "interrupt_id": interrupt_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "args": json.loads(args_json),
            "reasoning": reasoning,
            "options": json.loads(options_json or "[]"),
            "priority": int(priority),
            "expires_at": expires_at,
            "created_at": created_at,
        }

    async def claim_pending_approval(
        self, thread_id: str, *, interrupt_id: str
    ) -> dict[str, Any] | None:
        """Atomically consume a matching pending approval for one resumer."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "DELETE FROM pending_approvals WHERE thread_id = ? AND interrupt_id = ? "
                "RETURNING interrupt_id, tool_name, tool_call_id, args_json, reasoning, "
                "options_json, priority, expires_at, created_at",
                (thread_id, interrupt_id),
            )
            row = await cur.fetchone()
            await conn.commit()
        if row is None:
            return None
        (
            claimed_id,
            tool_name,
            tool_call_id,
            args_json,
            reasoning,
            options_json,
            priority,
            expires_at,
            created_at,
        ) = row
        if expires_at and expires_at <= _now():
            return None
        return {
            "interrupt_id": claimed_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "args": json.loads(args_json),
            "reasoning": reasoning,
            "options": json.loads(options_json or "[]"),
            "priority": int(priority),
            "expires_at": expires_at,
            "created_at": created_at,
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
        """Persiste IMEDIATA e SINCRONAMENTE a aprovação pendente, antes de
        qualquer espera — HITL sobrevive a restart do backend porque o
        estado nunca vive só em memória."""
        await self.setup()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO pending_approvals (thread_id, "
                "interrupt_id, tool_name, tool_call_id, args_json, reasoning, options_json, "
                "priority, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
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
                ),
            )
            await conn.commit()

    async def clear_pending_approval(self, thread_id: str) -> None:
        await self.setup()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pending_approvals WHERE thread_id = ?", (thread_id,)
            )
            await conn.commit()

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
                "INSERT INTO approval_decisions (thread_id, interrupt_id, tool_name, "
                "decision, selection, decided_by, decided_at, options_json, priority, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
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
                ),
            )
            await conn.commit()

    async def delete_session(self, thread_id: str) -> None:
        """Apaga `thread_id` e tudo que referencia ela (`messages`,
        `pending_approvals`) desta fonte de verdade. Precisa ser chamado
        junto de qualquer exclusão em `vectora_sessions` — do contrário a
        thread continua existindo aqui, e a reconciliação periódica
        (`reconcile_vectora_sessions`) a repovoa na sidebar na próxima
        rodada."""
        await self.setup()
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    "DELETE FROM pending_approvals WHERE thread_id = ?", (thread_id,)
                )
                await conn.execute(
                    "DELETE FROM messages WHERE thread_id = ?", (thread_id,)
                )
                await conn.execute(
                    "DELETE FROM sessions WHERE thread_id = ?", (thread_id,)
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
