-- Schema PostgreSQL do Vectora — fonte única de verdade (STORAGE_MODE=complete).
-- Usa tipos nativos Postgres: TIMESTAMPTZ, JSONB, BIGINT.
--
-- Arquivo único e idempotente: toda tabela usa CREATE TABLE/INDEX IF NOT
-- EXISTS já com o shape atual. Sempre que este arquivo mudar (checksum
-- diferente do último aplicado), o PostgresMigrationRunner reaplica o
-- script inteiro no próximo boot — nunca crie um novo arquivo numerado.
-- Para colunas novas em tabelas já existentes, use `ALTER TABLE ... ADD
-- COLUMN IF NOT EXISTS ...` (suportado nativamente pelo Postgres — seguro
-- reaplicar em qualquer banco, novo ou já populado).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS vectora_sessions (
    thread_id     TEXT         PRIMARY KEY,
    user_type     TEXT         NOT NULL DEFAULT 'human',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_activity TIMESTAMPTZ  NOT NULL DEFAULT now(),
    message_count BIGINT       NOT NULL DEFAULT 0,
    extra         JSONB        NOT NULL DEFAULT '{}',
    -- Modo da sessão (chat/code) como coluna de 1ª classe — antes vivia em
    -- extra->>'mode'; o modo "dev" foi renomeado para "code".
    mode          TEXT         NOT NULL DEFAULT 'code'
);

-- ADD COLUMN IF NOT EXISTS explícito (não só o CREATE TABLE acima) — cobre
-- bancos que já tinham vectora_sessions sem a coluna mode. O backfill a
-- partir de extra é idempotente (determinístico) e roda sempre: seguro
-- reaplicar em qualquer estado do banco.
ALTER TABLE vectora_sessions ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'code';
UPDATE vectora_sessions
SET mode = CASE WHEN extra->>'mode' = 'chat' THEN 'chat' ELSE 'code' END;

CREATE TABLE IF NOT EXISTS vectora_checkpoint_artifacts (
    id              TEXT         PRIMARY KEY,
    thread_id       TEXT         NOT NULL,
    checkpoint_id   TEXT         NOT NULL,
    strategy        TEXT         NOT NULL DEFAULT 'git',
    git_sha         TEXT,
    snapshot_path   TEXT,
    files_touched   JSONB        NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shared_threads (
    token       TEXT         PRIMARY KEY,
    thread_id   TEXT         NOT NULL,
    created_by  TEXT         NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ  NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_activity ON vectora_sessions(last_activity DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_mode     ON vectora_sessions(mode);
CREATE INDEX IF NOT EXISTS idx_artifacts_thread  ON vectora_checkpoint_artifacts(thread_id);
CREATE INDEX IF NOT EXISTS idx_shares_thread     ON shared_threads(thread_id);
CREATE INDEX IF NOT EXISTS idx_shares_expires    ON shared_threads(expires_at);

-- Fila de embedding assíncrono — usada pelo PostgresQueueDB em STORAGE_MODE=complete.
CREATE TABLE IF NOT EXISTS vectora_embedding_queue (
    id           BIGSERIAL    PRIMARY KEY,
    queue_id     TEXT         NOT NULL UNIQUE,
    task_json    JSONB        NOT NULL,
    status       TEXT         NOT NULL DEFAULT 'pending',
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_veq_status ON vectora_embedding_queue(status);

CREATE TABLE IF NOT EXISTS tool_usage_events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tool_usage_user_time_name
    ON tool_usage_events(user_id, created_at, tool_name);
