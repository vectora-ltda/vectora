-- Vectora: schema inicial do PostgreSQL (modo complete)
-- Executado automaticamente pelo postgres na criação do volume (docker-entrypoint-initdb.d).
-- O Vectora também garante este schema via get_pg_pool() na primeira conexão.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS vectora_sessions (
    thread_id     TEXT         PRIMARY KEY,
    user_type     TEXT         NOT NULL DEFAULT 'human',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_activity TIMESTAMPTZ  NOT NULL DEFAULT now(),
    message_count BIGINT       NOT NULL DEFAULT 0,
    extra         JSONB        NOT NULL DEFAULT '{}'
);

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

CREATE TABLE IF NOT EXISTS vectora_embedding_queue (
    id           BIGSERIAL    PRIMARY KEY,
    queue_id     TEXT         NOT NULL UNIQUE,
    task_json    JSONB        NOT NULL,
    status       TEXT         NOT NULL DEFAULT 'pending',
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sessions_activity ON vectora_sessions(last_activity DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_thread  ON vectora_checkpoint_artifacts(thread_id);
CREATE INDEX IF NOT EXISTS idx_shares_thread     ON shared_threads(thread_id);
CREATE INDEX IF NOT EXISTS idx_shares_expires    ON shared_threads(expires_at);
CREATE INDEX IF NOT EXISTS ix_veq_status         ON vectora_embedding_queue(status);
