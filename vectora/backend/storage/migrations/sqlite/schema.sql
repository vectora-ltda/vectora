-- Schema SQLite do Vectora — fonte única de verdade (~/.vectora/data/backend.db).
--
-- Arquivo único e idempotente: toda tabela usa CREATE TABLE/INDEX IF NOT
-- EXISTS já com o shape atual. Sempre que este arquivo mudar (checksum
-- diferente do último aplicado), o MigrationRunner reaplica o script
-- inteiro no próximo boot — nunca crie um novo arquivo numerado. Para
-- colunas novas em tabelas já existentes, adicione um `ALTER TABLE ...
-- ADD COLUMN ...` normal ao final da tabela correspondente: o runner
-- verifica via PRAGMA table_info antes de cada ALTER e pula se a coluna já
-- existir, então é seguro reaplicar em qualquer banco (novo ou já
-- populado).

CREATE TABLE IF NOT EXISTS users (
    id                 TEXT PRIMARY KEY,
    email              TEXT NOT NULL UNIQUE,
    password_hash      TEXT NOT NULL,
    role               TEXT NOT NULL DEFAULT 'member',
    name               TEXT NOT NULL DEFAULT '',
    username           TEXT NOT NULL DEFAULT '',
    env_overrides_json TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    last_login_at      TEXT
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash  TEXT    PRIMARY KEY,
    user_id     TEXT    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  TEXT    NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id            TEXT    PRIMARY KEY,
    user_id       TEXT,
    action        TEXT    NOT NULL,
    target_type   TEXT,
    target_id     TEXT,
    timestamp     TEXT    NOT NULL,
    ip            TEXT,
    user_agent    TEXT,
    success       INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT    NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS invites (
    token_hash  TEXT    PRIMARY KEY,
    email       TEXT,
    role        TEXT    NOT NULL DEFAULT 'member',
    created_by  TEXT,
    expires_at  TEXT    NOT NULL,
    used_at     TEXT,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_email         ON users(email);
-- Identidade por username: índice único parcial (ignora '' — só existe
-- transitoriamente antes do backfill em backend/rbac/auth.py).
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username) WHERE username != '';
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_user          ON audit(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp     ON audit(timestamp DESC);

-- Sessões, checkpoints e compartilhamento.
CREATE TABLE IF NOT EXISTS vectora_sessions (
    thread_id     TEXT    PRIMARY KEY,
    user_type     TEXT    NOT NULL DEFAULT 'human',
    created_at    TEXT    NOT NULL,
    last_activity TEXT    NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    extra         TEXT    NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS vectora_checkpoint_artifacts (
    id              TEXT PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    checkpoint_id   TEXT NOT NULL,
    strategy        TEXT NOT NULL DEFAULT 'git',
    git_sha         TEXT,
    snapshot_path   TEXT,
    files_touched   TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shared_threads (
    token       TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_activity ON vectora_sessions(last_activity DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_thread  ON vectora_checkpoint_artifacts(thread_id);
CREATE INDEX IF NOT EXISTS idx_shares_thread     ON shared_threads(thread_id);
CREATE INDEX IF NOT EXISTS idx_shares_expires    ON shared_threads(expires_at);

-- Spans de observabilidade.
CREATE TABLE IF NOT EXISTS spans (
    span_id      TEXT PRIMARY KEY,
    parent_id    TEXT,
    session_id   INTEGER,
    node         TEXT NOT NULL,
    event        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'ok',
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    duration_ms  REAL,
    in_tokens    INTEGER,
    out_tokens   INTEGER,
    metadata     TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_spans_session ON spans(session_id);
CREATE INDEX IF NOT EXISTS idx_spans_node    ON spans(node);
CREATE INDEX IF NOT EXISTS idx_spans_started ON spans(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_spans_parent  ON spans(parent_id);

-- Segredos cifrados por usuário.
CREATE TABLE IF NOT EXISTS secrets (
    user_id    TEXT NOT NULL,
    key        TEXT NOT NULL,
    ciphertext BLOB NOT NULL,
    nonce      BLOB NOT NULL,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS vectora_routines (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    instruction  TEXT NOT NULL,
    cron_expr    TEXT NOT NULL,
    workspace_id TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1,
    last_run_at  TEXT,
    next_run_at  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS webhook_events (
    id           TEXT PRIMARY KEY,
    provider     TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    workspace_id TEXT,
    received_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_provider    ON webhook_events(provider);
CREATE INDEX IF NOT EXISTS idx_webhook_events_received_at ON webhook_events(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhook_events_workspace   ON webhook_events(workspace_id);

CREATE TABLE IF NOT EXISTS email_events (
    id           TEXT PRIMARY KEY,
    provider     TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    from_email   TEXT,
    to_email     TEXT,
    subject      TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    received_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_email_events_provider    ON email_events(provider);
CREATE INDEX IF NOT EXISTS idx_email_events_received_at ON email_events(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_events_event_type  ON email_events(event_type);

CREATE TABLE IF NOT EXISTS vectora_background_tasks (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    workspace_id   TEXT,
    user_id        TEXT NOT NULL,
    kind           TEXT NOT NULL,
    name           TEXT NOT NULL,
    instruction    TEXT NOT NULL,
    trigger_type   TEXT NOT NULL,
    trigger_config TEXT NOT NULL DEFAULT '{}',
    enabled        INTEGER NOT NULL DEFAULT 1,
    last_run_at    TEXT,
    next_run_at    TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Kanban: ciclo triage/todo/ready/running/blocked/done/archived.
-- `claim_lock` guarda o id da run que detém a task; `claim_expires_at`
-- é o TTL que devolve o card se o worker morrer sem liberar. Bancos já
-- populados não ganham essas colunas de graça via CREATE TABLE IF NOT
-- EXISTS (no-op na tabela existente) — daí os ALTER TABLE abaixo.
ALTER TABLE vectora_background_tasks ADD COLUMN status TEXT NOT NULL DEFAULT 'todo';
ALTER TABLE vectora_background_tasks ADD COLUMN block_kind TEXT;
ALTER TABLE vectora_background_tasks ADD COLUMN block_reason TEXT;
ALTER TABLE vectora_background_tasks ADD COLUMN claim_lock TEXT;
ALTER TABLE vectora_background_tasks ADD COLUMN claim_expires_at TEXT;
-- Teto de custo por tarefa, em centavos. NULL = sem limite (o corte é
-- opt-in); 0 = não gaste nada, que é diferente de NULL.
ALTER TABLE vectora_background_tasks ADD COLUMN budget_cents INTEGER;
-- Bloqueios consecutivos (não-"dependency"); zera ao sair de "blocked"
-- com sucesso. Ao atingir BLOCK_RECURRENCE_LIMIT, block_task() escala pra
-- "triage" em vez de deixar o card preso em "blocked" pra sempre.
ALTER TABLE vectora_background_tasks ADD COLUMN block_count INTEGER NOT NULL DEFAULT 0;
-- Prioridade exibida no card do Kanban — "low" | "normal" | "high" |
-- "urgent". Não afeta ordem de claim (`claim_task` continua FIFO
-- por `ready`/`scheduled`); é só sinal visual pro humano triar mais rápido.
ALTER TABLE vectora_background_tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal';
-- Percentual do budget_cents (ou do teto herdado do perfil) que dispara um
-- aviso informativo em log — NULL = sem aviso configurado (opt-in, igual
-- ao próprio budget_cents). Nunca pausa nada sozinho; só o hard-stop de
-- budget_cents estourado bloqueia (backend/scheduling/budget.py).
ALTER TABLE vectora_background_tasks ADD COLUMN budget_warn_percent INTEGER;

CREATE INDEX IF NOT EXISTS idx_background_tasks_session ON vectora_background_tasks(session_id);
CREATE INDEX IF NOT EXISTS idx_background_tasks_due     ON vectora_background_tasks(enabled, trigger_type);
CREATE INDEX IF NOT EXISTS idx_background_tasks_status  ON vectora_background_tasks(status);

-- Dependências entre tasks: `child_id` só fica pronto quando `parent_id`
-- conclui. PK composta impede o mesmo par duas vezes.
CREATE TABLE IF NOT EXISTS vectora_task_links (
    parent_id  TEXT NOT NULL,
    child_id   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (parent_id, child_id)
);

CREATE INDEX IF NOT EXISTS idx_task_links_child ON vectora_task_links(child_id);

CREATE TABLE IF NOT EXISTS vectora_background_runs (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    session_id     TEXT NOT NULL,
    run_thread_id  TEXT,
    trigger_source TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'running',
    summary        TEXT,
    started_at     TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at    TEXT
);

-- Consumo da run. NULL (não 0) quando o provider não expõe usage: somar 0
-- faria o budget nunca estourar. Mesmo motivo do ALTER acima: banco já
-- populado não ganha coluna nova via CREATE TABLE IF NOT EXISTS.
ALTER TABLE vectora_background_runs ADD COLUMN tokens_used INTEGER;
ALTER TABLE vectora_background_runs ADD COLUMN estimated_cost_cents REAL;
-- Sinal de liveness semântica (backend/scheduling/liveness.py) — NULL quando
-- nenhum padrão de estagnação foi reconhecido no texto final da run.
ALTER TABLE vectora_background_runs ADD COLUMN liveness TEXT;

CREATE INDEX IF NOT EXISTS idx_background_runs_task    ON vectora_background_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_background_runs_session ON vectora_background_runs(session_id);

-- Configuração não-secreta do app (provider/model ativo, storage_mode,
-- auth_required, nome/empresa do usuário local, prefs do frontend etc.) —
-- espelha o schema que RuntimeSettings cria idempotentemente em
-- backend/workspace/runtime_settings.py, no mesmo backend.db.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Vectora Connect — mapeamento (plataforma, usuário externo) -> thread do
-- Vectora. Sem isto cada mensagem do Telegram/Discord/Slack/Email abriria uma
-- conversa nova, perdendo todo o histórico do interlocutor.
CREATE TABLE IF NOT EXISTS connect_thread_mappings (
    platform         TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    thread_id        TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (platform, platform_user_id)
);

-- Perfis de agente customizados: preset reutilizável (instrução, escopo de
-- tools, modelo, budget) que uma task do Kanban pode referenciar em vez de
-- rodar sempre com a personalidade genérica do orchestrator. tool_scope/
-- instruction_path/model_override NULL = herda o comportamento padrão.
CREATE TABLE IF NOT EXISTS vectora_agent_profiles (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    name              TEXT NOT NULL,
    title             TEXT NOT NULL DEFAULT '',
    icon              TEXT NOT NULL DEFAULT '',
    color             TEXT NOT NULL DEFAULT '',
    instruction_path  TEXT,
    tool_scope        TEXT NOT NULL DEFAULT '[]',
    model_override    TEXT,
    budget_cents      INTEGER,
    status            TEXT NOT NULL DEFAULT 'active',
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agent_profiles_user ON vectora_agent_profiles(user_id);

-- Perfil de agente atribuído a uma task do Kanban — NULL = comportamento
-- padrão do orchestrator.
ALTER TABLE vectora_background_tasks ADD COLUMN agent_profile_id TEXT;

-- Comentários de humanos num card do Kanban — discussão sobre a task,
-- separada da timeline de transições abaixo.
CREATE TABLE IF NOT EXISTS vectora_task_comments (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_task_comments_task ON vectora_task_comments(task_id);

-- Timeline de transições de status do Kanban. `backend/scheduling/kanban.py`
-- já dispara um evento SSE (efêmero) para cada transição; esta tabela é o
-- histórico consultável que o SSE nunca persistiu. `from_status` nullable —
-- a primeira transição registrada de uma task pode não ter estado anterior
-- capturado.
CREATE TABLE IF NOT EXISTS vectora_task_events (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    block_kind   TEXT,
    block_reason TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_task_events_task ON vectora_task_events(task_id);

-- Sprint 4 Fase 6 — multi-board. Um board é agrupamento NOMEADO por cima
-- das tasks; a session (`vectora_background_tasks.session_id`) continua
-- sendo o contexto de execução — um board não a substitui. `workspace_id`
-- nullable é só o default herdado por cards novos criados dentro do
-- board, não um filtro rígido. `slug` é único por usuário (não global),
-- pra permitir URLs legíveis sem coordenar entre contas.
CREATE TABLE IF NOT EXISTS vectora_boards (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    slug        TEXT NOT NULL,
    name        TEXT NOT NULL,
    workspace_id TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    archived_at TEXT,
    UNIQUE (user_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_boards_user ON vectora_boards(user_id);

-- Atividade pseudônima por usuário, dispositivo e thread. Não contém
-- conteúdo de conversa nem identificadores pessoais.
CREATE TABLE IF NOT EXISTS vectora_thread_activity (
    user_id        TEXT NOT NULL,
    device_id      TEXT NOT NULL,
    thread_id      TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    PRIMARY KEY (user_id, device_id, thread_id)
);

CREATE INDEX IF NOT EXISTS idx_thread_activity_user_thread
    ON vectora_thread_activity(user_id, thread_id);

-- NULL = task pré-Fase-6, nunca associada a um board — absorve o legado
-- sem exigir backfill; o board "Default" é criado sob demanda
-- (`get_or_create_default_board`) na primeira vez que o usuário abre a
-- visão de boards, não numa migração de dados em massa.
ALTER TABLE vectora_background_tasks ADD COLUMN board_id TEXT;
