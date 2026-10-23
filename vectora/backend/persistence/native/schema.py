"""Definições canônicas de DDL/schema para persistência de sessões, mensagens, aprovações e ledger de eventos.

Separa explicitamente a infraestrutura de criação/migração de tabelas
do consumo de dados feito por `SessionStore` e `PostgresSessionStore`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

    from backend.storage.sqlite.pool import AsyncConnectionPool

# ---------------------------------------------------------------------------
# SQLite Schema (sessions.db)
# ---------------------------------------------------------------------------

SQLITE_SESSIONS_SCHEMA = """
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
    run_id TEXT,
    edited_files_json TEXT,
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
    run_id TEXT,
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
CREATE TABLE IF NOT EXISTS session_runs (
    run_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES sessions(thread_id),
    turn_id TEXT,
    status TEXT NOT NULL CHECK(status IN
        ('queued', 'running', 'streaming', 'completed', 'failed', 'cancelled')),
    detail TEXT,
    error_code TEXT,
    partial_text TEXT NOT NULL DEFAULT '',
    provider_model TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    terminal_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_session_runs_thread_turn
    ON session_runs(thread_id, turn_id) WHERE turn_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_session_runs_status
    ON session_runs(status, updated_at);
CREATE TABLE IF NOT EXISTS thread_events (
    thread_id TEXT NOT NULL REFERENCES sessions(thread_id),
    seq INTEGER NOT NULL,
    event_key TEXT NOT NULL,
    type TEXT NOT NULL,
    turn_id TEXT,
    run_id TEXT,
    message_id INTEGER,
    tool_call_id TEXT,
    interrupt_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (thread_id, seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_thread_events_key
    ON thread_events(thread_id, event_key);
CREATE INDEX IF NOT EXISTS ix_thread_events_run
    ON thread_events(run_id) WHERE run_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS thread_event_cursors (
    thread_id TEXT NOT NULL REFERENCES sessions(thread_id),
    user_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    last_seen_seq INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (thread_id, user_id, device_id)
);
CREATE TABLE IF NOT EXISTS run_drafts (
    thread_id TEXT NOT NULL REFERENCES sessions(thread_id),
    run_id TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (thread_id, run_id)
);
CREATE TABLE IF NOT EXISTS ledger_backfill (
    thread_id TEXT PRIMARY KEY REFERENCES sessions(thread_id),
    status TEXT NOT NULL CHECK(status IN ('pending', 'done', 'failed')),
    migrated_messages INTEGER NOT NULL DEFAULT 0,
    last_seq INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES sessions(thread_id),
    turn_id TEXT,
    parent_run_id TEXT,
    correlation_id TEXT,
    user_id TEXT NOT NULL DEFAULT '',
    workspace_id TEXT,
    model TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN
        ('planned', 'admitted', 'running', 'waiting_approval', 'waiting_provider', 'tool_running', 'verifying', 'passed', 'degraded', 'failed', 'cancelled', 'recovery_required')),
    plan_step TEXT,
    tools_available_json TEXT NOT NULL DEFAULT '[]',
    tools_used_json TEXT NOT NULL DEFAULT '[]',
    approvals_json TEXT NOT NULL DEFAULT '[]',
    files_touched_json TEXT NOT NULL DEFAULT '[]',
    commands_run_json TEXT NOT NULL DEFAULT '[]',
    tests_run_json TEXT NOT NULL DEFAULT '[]',
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    detail TEXT,
    error_code TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    terminal_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_agent_runs_thread_turn
    ON agent_runs(thread_id, turn_id) WHERE turn_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_agent_runs_thread_status
    ON agent_runs(thread_id, status, updated_at);
"""


async def setup_sqlite_session_schema(pool: AsyncConnectionPool) -> None:
    """Cria e migra todas as tabelas e índices necessários no SQLite (sessions.db)."""
    async with pool.acquire() as conn:
        await conn.executescript(SQLITE_SESSIONS_SCHEMA)
        # Expande a constraint de status sem perder AgentRuns já persistidos.
        # SQLite não suporta ALTER CONSTRAINT; a reconstrução é idempotente e
        # mantém a mesma ordem de colunas usada pelo store.
        cursor = await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_runs'"
        )
        agent_runs_sql = await cursor.fetchone()
        if agent_runs_sql and "waiting_provider" not in str(agent_runs_sql[0]):
            legacy_columns = {
                str(column[1])
                for column in await conn.execute_fetchall(
                    "PRAGMA table_info(agent_runs)"
                )
            }
            for lineage_column in ("parent_run_id", "correlation_id"):
                if lineage_column not in legacy_columns:
                    await conn.execute(
                        f"ALTER TABLE agent_runs ADD COLUMN {lineage_column} TEXT"
                    )
            await conn.execute("DROP INDEX IF EXISTS ix_agent_runs_thread_turn")
            await conn.execute("DROP INDEX IF EXISTS ix_agent_runs_thread_status")
            await conn.execute("ALTER TABLE agent_runs RENAME TO agent_runs_legacy")
            await conn.execute(
                """CREATE TABLE agent_runs (
                    run_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES sessions(thread_id),
                    turn_id TEXT,
                    parent_run_id TEXT,
                    correlation_id TEXT,
                    user_id TEXT NOT NULL DEFAULT '',
                    workspace_id TEXT,
                    model TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN
                        ('planned', 'admitted', 'running', 'waiting_approval', 'waiting_provider', 'tool_running', 'verifying', 'passed', 'degraded', 'failed', 'cancelled', 'recovery_required')),
                    plan_step TEXT,
                    tools_available_json TEXT NOT NULL DEFAULT '[]',
                    tools_used_json TEXT NOT NULL DEFAULT '[]',
                    approvals_json TEXT NOT NULL DEFAULT '[]',
                    files_touched_json TEXT NOT NULL DEFAULT '[]',
                    commands_run_json TEXT NOT NULL DEFAULT '[]',
                    tests_run_json TEXT NOT NULL DEFAULT '[]',
                    artifacts_json TEXT NOT NULL DEFAULT '[]',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    detail TEXT,
                    error_code TEXT,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    terminal_at TEXT
                )"""
            )
            await conn.execute(
                """INSERT INTO agent_runs (
                    run_id, thread_id, turn_id, user_id, workspace_id, model, provider,
                    parent_run_id, correlation_id,
                    status, plan_step, tools_available_json, tools_used_json,
                    approvals_json, files_touched_json, commands_run_json,
                    tests_run_json, artifacts_json, metrics_json, evidence_json,
                    detail, error_code, started_at, updated_at, terminal_at
                ) SELECT run_id, thread_id, turn_id, user_id, workspace_id, model,
                    provider, parent_run_id, correlation_id, status, plan_step,
                    tools_available_json, tools_used_json,
                    approvals_json, files_touched_json, commands_run_json,
                    tests_run_json, artifacts_json, metrics_json, evidence_json,
                    detail, error_code, started_at, updated_at, terminal_at
                    FROM agent_runs_legacy"""
            )
            await conn.execute("DROP TABLE agent_runs_legacy")
            await conn.execute(
                "CREATE UNIQUE INDEX ix_agent_runs_thread_turn "
                "ON agent_runs(thread_id, turn_id) WHERE turn_id IS NOT NULL"
            )
            await conn.execute(
                "CREATE INDEX ix_agent_runs_thread_status "
                "ON agent_runs(thread_id, status, updated_at)"
            )
        # Upgrade de bancos criados antes de decisões estruturadas de HITL
        for statement in (
            "ALTER TABLE pending_approvals ADD COLUMN options_json TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE pending_approvals ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE pending_approvals ADD COLUMN expires_at TEXT",
            "ALTER TABLE pending_approvals ADD COLUMN run_id TEXT",
        ):
            try:
                await conn.execute(statement)
            except Exception as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        try:
            await conn.execute(
                "ALTER TABLE session_runs ADD COLUMN provider_model TEXT"
            )
        except Exception as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        columns = await conn.execute_fetchall("PRAGMA table_info(messages)")
        if not any(str(column[1]) == "turn_id" for column in columns):
            await conn.execute("ALTER TABLE messages ADD COLUMN turn_id TEXT")
        if not any(str(column[1]) == "run_id" for column in columns):
            await conn.execute("ALTER TABLE messages ADD COLUMN run_id TEXT")
        if not any(str(column[1]) == "edited_files_json" for column in columns):
            await conn.execute("ALTER TABLE messages ADD COLUMN edited_files_json TEXT")
        run_cols = await conn.execute_fetchall("PRAGMA table_info(agent_runs)")
        run_col_names = {str(col[1]) for col in run_cols}
        if "parent_run_id" not in run_col_names:
            await conn.execute("ALTER TABLE agent_runs ADD COLUMN parent_run_id TEXT")
        if "correlation_id" not in run_col_names:
            await conn.execute("ALTER TABLE agent_runs ADD COLUMN correlation_id TEXT")
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_messages_thread_turn "
            "ON messages(thread_id, turn_id) WHERE turn_id IS NOT NULL"
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# PostgreSQL Schema (complete mode / vectora_native_*)
# ---------------------------------------------------------------------------

POSTGRES_SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS vectora_sessions (
    thread_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT '',
    user_type TEXT NOT NULL DEFAULT 'human',
    mode TEXT NOT NULL DEFAULT 'code',
    permission_mode TEXT NOT NULL DEFAULT 'ask',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activity TIMESTAMPTZ NOT NULL DEFAULT now(),
    message_count BIGINT NOT NULL DEFAULT 0,
    extra JSONB NOT NULL DEFAULT '{}'
);
ALTER TABLE vectora_sessions ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT '';
ALTER TABLE vectora_sessions ADD COLUMN IF NOT EXISTS permission_mode TEXT NOT NULL DEFAULT 'ask';
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
    run_id TEXT,
    edited_files_json TEXT,
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
    run_id TEXT,
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
CREATE TABLE IF NOT EXISTS vectora_native_thread_events (
    thread_id TEXT NOT NULL REFERENCES vectora_native_sessions(thread_id),
    seq BIGINT NOT NULL,
    event_key TEXT NOT NULL,
    type TEXT NOT NULL,
    turn_id TEXT,
    run_id TEXT,
    message_id BIGINT,
    tool_call_id TEXT,
    interrupt_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (thread_id, seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_vectora_native_thread_events_key
    ON vectora_native_thread_events(thread_id, event_key);
CREATE INDEX IF NOT EXISTS ix_vectora_native_thread_events_run
    ON vectora_native_thread_events(run_id) WHERE run_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS vectora_native_thread_event_cursors (
    thread_id TEXT NOT NULL REFERENCES vectora_native_sessions(thread_id),
    user_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    last_seen_seq BIGINT NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (thread_id, user_id, device_id)
);
CREATE TABLE IF NOT EXISTS vectora_native_run_drafts (
    thread_id TEXT NOT NULL REFERENCES vectora_native_sessions(thread_id),
    run_id TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (thread_id, run_id)
);
CREATE TABLE IF NOT EXISTS vectora_native_ledger_backfill (
    thread_id TEXT PRIMARY KEY REFERENCES vectora_native_sessions(thread_id),
    status TEXT NOT NULL CHECK(status IN ('pending', 'done', 'failed')),
    migrated_messages BIGINT NOT NULL DEFAULT 0,
    last_seq BIGINT NOT NULL DEFAULT 0,
    error TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS vectora_native_agent_runs (
    run_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES vectora_native_sessions(thread_id),
    turn_id TEXT,
    parent_run_id TEXT,
    correlation_id TEXT,
    user_id TEXT NOT NULL DEFAULT '',
    workspace_id TEXT,
    model TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN
        ('planned', 'admitted', 'running', 'waiting_approval', 'waiting_provider', 'tool_running', 'verifying', 'passed', 'degraded', 'failed', 'cancelled', 'recovery_required')),
    plan_step TEXT,
    tools_available_json TEXT NOT NULL DEFAULT '[]',
    tools_used_json TEXT NOT NULL DEFAULT '[]',
    approvals_json TEXT NOT NULL DEFAULT '[]',
    files_touched_json TEXT NOT NULL DEFAULT '[]',
    commands_run_json TEXT NOT NULL DEFAULT '[]',
    tests_run_json TEXT NOT NULL DEFAULT '[]',
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    detail TEXT,
    error_code TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    terminal_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_vectora_native_agent_runs_thread_turn
    ON vectora_native_agent_runs(thread_id, turn_id) WHERE turn_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_vectora_native_agent_runs_thread_status
    ON vectora_native_agent_runs(thread_id, status, updated_at);
"""


async def setup_postgres_session_schema(pool: asyncpg.Pool) -> None:
    """Cria e migra todas as tabelas e índices necessários no PostgreSQL."""
    async with pool.acquire() as conn:
        await conn.execute(POSTGRES_SESSIONS_SCHEMA)
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
            (
                "ALTER TABLE vectora_native_pending_approvals "
                "ADD COLUMN IF NOT EXISTS run_id TEXT"
            ),
        ):
            await conn.execute(statement)
        await conn.execute(
            "ALTER TABLE vectora_native_messages ADD COLUMN IF NOT EXISTS turn_id TEXT"
        )
        await conn.execute(
            "ALTER TABLE vectora_native_messages ADD COLUMN IF NOT EXISTS run_id TEXT"
        )
        await conn.execute(
            "ALTER TABLE vectora_native_messages "
            "ADD COLUMN IF NOT EXISTS edited_files_json TEXT"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_vectora_native_messages_thread_turn "
            "ON vectora_native_messages(thread_id, turn_id) WHERE turn_id IS NOT NULL"
        )
        await conn.execute(
            "ALTER TABLE vectora_native_agent_runs ADD COLUMN IF NOT EXISTS parent_run_id TEXT"
        )
        await conn.execute(
            "ALTER TABLE vectora_native_agent_runs ADD COLUMN IF NOT EXISTS correlation_id TEXT"
        )
