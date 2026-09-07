-- Schema D1 do vectora-services — fonte única de verdade, substitui o
-- Postgres do Supabase.
--
-- Arquivo único e idempotente: toda tabela usa CREATE TABLE/INDEX IF NOT
-- EXISTS já com o shape FINAL (nenhuma coluna entra depois via ALTER
-- solto) e todo seed usa INSERT OR IGNORE / ON CONFLICT DO UPDATE. Sempre
-- que o schema mudar, este arquivo inteiro é reaplicado — nunca crie um
-- novo arquivo de migration numerado.
--
-- wrangler d1 migrations rastreia migrations por NOME DE ARQUIVO (não por
-- conteúdo) — `wrangler d1 migrations apply` só roda este arquivo uma vez
-- por ambiente D1 (a primeira vez que existir). Depois de editar o schema
-- aqui, reaplique explicitamente contra um D1 já existente (local ou
-- remoto) com:
--   wrangler d1 execute vectora-db [--local|--remote] --file=migrations/0001_schema.sql
-- Seguro porque cada statement é idempotente — reaplicar não duplica nada
-- nem falha em objetos já existentes.
--
-- Sem RLS (D1/SQLite não tem) — autorização é responsabilidade de cada
-- handler (checar user_id da sessão contra o dono da linha antes de
-- retornar/mutar), mesmo princípio já vinculante do CLAUDE.md regra 7
-- ("Auth-first para tudo server").
--
-- Espelha o shape das tabelas Supabase (company/supabase/migrations/*.sql),
-- com dois ajustes de fundo:
--   1. `users` substitui auth.users + profiles combinados (D1 não tem um
--      sistema de auth embutido separado — o próprio services é o auth).
--   2. `sessions` é novo — token opaco de sessão (não existia no Supabase,
--      que gerenciava isso internamente via JWT). Ver src/auth/session.ts.

CREATE TABLE IF NOT EXISTS users (
  id             TEXT PRIMARY KEY,  -- uuid gerado em auth/routes.ts
  email          TEXT NOT NULL UNIQUE,
  password_hash  TEXT NOT NULL,     -- formato pbkdf2$<iter>$<saltB64>$<hashB64>
  full_name      TEXT NOT NULL DEFAULT '',
  country        TEXT NOT NULL DEFAULT 'INTL' CHECK (country IN ('BR', 'INTL')),
  language       TEXT NOT NULL DEFAULT 'pt',
  email_verified INTEGER NOT NULL DEFAULT 0, -- boolean (0/1)
  role           TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
  soft_delete_at TEXT,                        -- ISO8601, agendamento de hard-delete (GDPR)
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS users_email_idx ON users(email);
CREATE INDEX IF NOT EXISTS users_soft_delete_idx ON users(soft_delete_at) WHERE soft_delete_at IS NOT NULL;

-- Sessão web (substitui o JWT do Supabase Auth) — token opaco, hash guardado
-- aqui, raw só existe no cookie HttpOnly da company. company é a única
-- consumidora (server-to-server); o browser nunca vê o token de sessão do
-- Supabase nem deste.
CREATE TABLE IF NOT EXISTS sessions (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_used_at TEXT
);

CREATE INDEX IF NOT EXISTS sessions_user_id_idx ON sessions(user_id);
CREATE INDEX IF NOT EXISTS sessions_token_hash_idx ON sessions(token_hash);

-- Tokens de verificação de email / magic link — uso único, TTL curto.
CREATE TABLE IF NOT EXISTS email_verifications (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash  TEXT NOT NULL UNIQUE,
  purpose     TEXT NOT NULL CHECK (purpose IN ('verify_email', 'magic_link')),
  expires_at  TEXT NOT NULL,
  used_at     TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS email_verifications_user_id_idx ON email_verifications(user_id);

-- Token de licença (VECTORA_TOKEN) — recuperável: `token` (plaintext) fica
-- gravado indefinidamente pra poder ser revelado de novo pelo dashboard;
-- `token_hash` é o que /validate usa pra comparação, nunca o plaintext.
CREATE TABLE IF NOT EXISTS tokens (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  token      TEXT,               -- NULL só em linhas legadas pré-migração
  token_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS tokens_hash_idx ON tokens(token_hash);

-- provider aceita 'gift' — presentes e cupons free_lifetime não passam por
-- Asaas/Stripe.
CREATE TABLE IF NOT EXISTS subscriptions (
  id                  TEXT PRIMARY KEY,
  user_id             TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  tier                TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free', 'pro')),
  status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('trialing','active','past_due','canceled','expired')),
  currency            TEXT NOT NULL DEFAULT 'BRL' CHECK (currency IN ('BRL', 'USD')),
  provider            TEXT CHECK (provider IN ('asaas', 'stripe', 'gift')),
  provider_id         TEXT,
  customer_id         TEXT,
  started_at          TEXT NOT NULL DEFAULT (datetime('now')),
  trial_ends_at       TEXT,       -- NULL pra free (permanente, sem trial)
  current_period_end  TEXT,
  canceled_at         TEXT,
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS license_checks (
  id              TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  vectora_version TEXT NOT NULL,
  result          TEXT NOT NULL CHECK (result IN ('valid', 'invalid', 'expired', 'not_found')),
  ip              TEXT,
  checked_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS license_checks_user_id_idx ON license_checks(user_id);

CREATE TABLE IF NOT EXISTS payment_events (
  id           TEXT PRIMARY KEY,
  user_id      TEXT REFERENCES users(id) ON DELETE SET NULL,
  provider     TEXT NOT NULL CHECK (provider IN ('asaas', 'stripe')),
  event_type   TEXT NOT NULL,
  payload      TEXT NOT NULL DEFAULT '{}', -- JSON serializado (D1 não tem JSONB)
  processed_at TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS api_keys (
  id           TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name         TEXT NOT NULL,
  scopes       TEXT NOT NULL DEFAULT '["read"]', -- JSON array serializado
  key_hash     TEXT NOT NULL,
  key_prefix   TEXT NOT NULL DEFAULT 'vk_',
  last_used_at TEXT,
  revoked_at   TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS api_keys_user_id_idx ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS api_keys_hash_idx ON api_keys(key_hash);

CREATE TABLE IF NOT EXISTS waitlist (
  id         TEXT PRIMARY KEY,
  email      TEXT NOT NULL UNIQUE,
  source     TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- status: 'open' por padrão (badge de notificação in-app no admin conta
-- issues nesse status); 'resolved' quando o admin responde/marca como
-- resolvida. response/responded_at NULL até a primeira resposta.
-- files: JSON array das keys R2 de anexos (prints/vídeos), NULL = sem anexos.
-- archived_at: soft-delete — NULL = ativa, visível nas listagens públicas e
-- de admin; arquivada some das duas listagens por padrão mas continua
-- acessível via GET /admin/issues/:id (admin pode desarquivar).
CREATE TABLE IF NOT EXISTS issues (
  id           TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  category     TEXT NOT NULL CHECK (category IN ('bug', 'feedback', 'feature')),
  description  TEXT,
  email        TEXT,
  files        TEXT,
  status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
  response     TEXT,
  responded_at TEXT,
  archived_at  TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Biblioteca de bancos RAG pré-indexados (catálogo só-leitura; artefatos
-- de verdade vivem em storage externo, não Cloudflare). status:
-- pacotes existentes nascem 'ready' — só quem passa por /reindex entra em
-- 'pending'/'failed'.
-- source_lib/source_version são NOT NULL só pra linhas first-party
-- (bibliotecas de código pré-indexadas, ex. "requests 2.31.0"); publicações
-- da comunidade (Memory Library) usam publisher_id em vez disso
-- e ficam com source_lib/source_version vazios — não dá pra tornar essas
-- colunas nullable retroativamente sem quebrar linhas antigas, então o
-- handler de POST /publish grava string vazia ('') nesses dois campos para
-- publicações de usuário, nunca NULL.
CREATE TABLE IF NOT EXISTS rag_packages (
  id               TEXT PRIMARY KEY,
  name             TEXT NOT NULL,
  source_lib       TEXT NOT NULL,
  source_version   TEXT NOT NULL,
  package_name     TEXT,
  version          TEXT NOT NULL DEFAULT '0.0.1',
  size_bytes       INTEGER NOT NULL,
  checksum         TEXT NOT NULL,
  storage_url      TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'pending', 'failed')),
  status_reason    TEXT,
  embed_model      TEXT,
  publisher_id     TEXT REFERENCES users(id),
  verified         INTEGER NOT NULL DEFAULT 0,
  downloads_count  INTEGER NOT NULL DEFAULT 0,
  license          TEXT,
  description      TEXT,
  updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Registry real de MCP/Skills (`services/src/registry/routes.ts`) —
-- substitui os placeholders `{entries: []}`. Seed inicial migra os 6
-- conectores hoje hardcoded em `backend/api/handlers/mcp_marketplace.py`
-- (fonte de verdade passa a ser este catálogo; a lista Python vira só
-- fallback offline). `extensions` fica de fora — SDK de extensões ainda
-- não existe, não crie tabela pra ela até existir.
--
-- `catalog_source` distingue linhas curadas manualmente (seed abaixo,
-- sempre 'curated') das descobertas automaticamente pelo discovery cron
-- (`services/src/registry/discovery.ts`) — 'official' para o registry
-- oficial de MCP, 'github' para skills achadas via GitHub code search. O
-- upsert do discovery nunca sobrescreve uma linha 'curated', mesmo que o
-- id colida.
CREATE TABLE IF NOT EXISTS mcp_catalog (
  id               TEXT PRIMARY KEY,
  name             TEXT NOT NULL,
  description      TEXT NOT NULL,
  install_cmd      TEXT NOT NULL,
  env_vars         TEXT NOT NULL DEFAULT '[]', -- JSON array serializado
  homepage         TEXT,
  category         TEXT NOT NULL,
  icon_url         TEXT,
  catalog_source   TEXT NOT NULL DEFAULT 'curated',
  vectora_verified INTEGER NOT NULL DEFAULT 0,
  downloads_count  INTEGER NOT NULL DEFAULT 0,
  updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- `vectora_verified` = selo oficial/curado (seed manual ou discovery), como
-- em `mcp_catalog`. `verified` é um gate SEPARADO — curadoria de admin sobre
-- item publicado por usuário via
-- `POST /skills` (`catalog_source='community'`), mesmo papel de
-- `rag_packages.verified`. Uma skill pode ter `verified=1` sem nunca ganhar
-- o selo `vectora_verified` (que continua exclusivo de curadoria oficial).
-- `publisher_id` NULL pra linhas curadas/discovery, preenchido só pra
-- publicações de comunidade. D1/SQLite não suporta `ALTER TABLE ... ADD
-- COLUMN IF NOT EXISTS` — bancos já provisionados antes desta mudança
-- precisam de `ALTER TABLE skills_catalog ADD COLUMN <col> ...` manual
-- (uma vez, fora deste arquivo) antes de reaplicar.
CREATE TABLE IF NOT EXISTS skills_catalog (
  id               TEXT PRIMARY KEY,
  name             TEXT NOT NULL,
  description      TEXT NOT NULL,
  source           TEXT NOT NULL, -- git URL, mesmo formato aceito por POST /skills
  package_name     TEXT,
  version          TEXT NOT NULL DEFAULT '0.0.1',
  tags             TEXT NOT NULL DEFAULT '[]', -- JSON array serializado
  category         TEXT,
  catalog_source   TEXT NOT NULL DEFAULT 'curated',
  vectora_verified INTEGER NOT NULL DEFAULT 0,
  publisher_id     TEXT REFERENCES users(id),
  verified         INTEGER NOT NULL DEFAULT 0,
  downloads_count  INTEGER NOT NULL DEFAULT 0,
  updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO mcp_catalog (id, name, description, install_cmd, env_vars, homepage, category, vectora_verified) VALUES
  ('brave-search', 'Brave Search', 'Pesquisa web via Brave Search API com resultados sem rastreamento.', 'npx -y @modelcontextprotocol/server-brave-search', '["BRAVE_API_KEY"]', 'https://github.com/modelcontextprotocol/servers', 'web', 1),
  ('filesystem', 'Filesystem', 'Acesso seguro ao filesystem local com controle de diretórios permitidos.', 'npx -y @modelcontextprotocol/server-filesystem', '[]', 'https://github.com/modelcontextprotocol/servers', 'filesystem', 1),
  ('github', 'GitHub', 'Integração com GitHub: PRs, issues, código, actions e mais.', 'npx -y @modelcontextprotocol/server-github', '["GITHUB_PERSONAL_ACCESS_TOKEN"]', 'https://github.com/modelcontextprotocol/servers', 'devtools', 1),
  ('postgres', 'PostgreSQL', 'Consultas read-only em banco PostgreSQL.', 'npx -y @modelcontextprotocol/server-postgres', '["POSTGRES_CONNECTION_STRING"]', 'https://github.com/modelcontextprotocol/servers', 'database', 1),
  ('slack', 'Slack', 'Leitura e envio de mensagens no Slack via Bot Token.', 'npx -y @modelcontextprotocol/server-slack', '["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"]', 'https://github.com/modelcontextprotocol/servers', 'communication', 1),
  ('sequential-thinking', 'Sequential Thinking', 'Raciocínio passo-a-passo estruturado antes de agir.', 'npx -y @modelcontextprotocol/server-sequential-thinking', '[]', 'https://github.com/modelcontextprotocol/servers', 'reasoning', 1);

-- skills_catalog nasce sem seed: ao contrário do MCP (que já tinha 6
-- conectores hardcoded pra migrar), não existe hoje nenhuma skill oficial
-- do Vectora com repositório git publicado — fabricar uma URL aqui criaria
-- uma entrada curada apontando pra um link inexistente/quebrado. Curadoria
-- entra por PR editando este seed (mesmo fluxo do resto do arquivo, ver
-- comentário no topo) assim que houver skills reais pra publicar.

-- Tabela de telemetria genérica (crash/uso) enviada pelo backend Python do
-- Vectora local — POST /telemetry/ingest, sempre via fila (vectora-jobs,
-- job telemetry_ingest), nunca gravada direto na rota HTTP.
CREATE TABLE IF NOT EXISTS telemetry_events (
  id          TEXT PRIMARY KEY,
  source      TEXT NOT NULL,    -- 'vectora-app' | 'vectora-desktop'
  event_type  TEXT NOT NULL,
  payload     TEXT NOT NULL,    -- JSON serializado
  received_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS telemetry_events_source_idx ON telemetry_events(source, event_type);

-- RBAC + billing — catálogo de planos por duração + cupons de
-- desconto/gratuidade + presentes de licença por email.

-- Catálogo de planos por duração. stripe_price_id é preenchido sob
-- demanda (lazy) no primeiro checkout daquele plano em USD — ver
-- services/src/billing/plans.ts (ensureStripePrice).
CREATE TABLE IF NOT EXISTS plans (
  id                TEXT PRIMARY KEY,      -- "1m" | "3m" | "6m" | "12m" | "36m"
  months            INTEGER NOT NULL,
  price_usd_cents   INTEGER NOT NULL,
  price_brl_cents   INTEGER NOT NULL,
  stripe_price_id   TEXT,
  active            INTEGER NOT NULL DEFAULT 1
);

-- Cupons: 'discount' cobra charge_plan_id mas concede grant_plan_id
-- (ex.: cobra 1m, concede 3m); 'free_lifetime' não cobra nada, concede
-- Pro vitalício e é de uso único por padrão (max_redemptions).
CREATE TABLE IF NOT EXISTS coupons (
  id                TEXT PRIMARY KEY,
  code              TEXT NOT NULL UNIQUE,  -- normalizado uppercase na aplicação
  kind              TEXT NOT NULL CHECK (kind IN ('discount', 'free_lifetime')),
  grant_plan_id     TEXT REFERENCES plans(id),
  charge_plan_id    TEXT REFERENCES plans(id),
  max_redemptions   INTEGER,               -- NULL = ilimitado
  times_redeemed    INTEGER NOT NULL DEFAULT 0,
  active            INTEGER NOT NULL DEFAULT 1,
  created_by        TEXT NOT NULL REFERENCES users(id),
  expires_at        TEXT,
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS coupons_code_idx ON coupons(code);

CREATE TABLE IF NOT EXISTS coupon_redemptions (
  id          TEXT PRIMARY KEY,
  coupon_id   TEXT NOT NULL REFERENCES coupons(id) ON DELETE CASCADE,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  redeemed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS coupon_redemptions_coupon_idx ON coupon_redemptions(coupon_id);

-- Presentes de licença — concedidos por email, antes mesmo de existir
-- conta (aplicado no signup se ainda 'pending').
CREATE TABLE IF NOT EXISTS gifts (
  id               TEXT PRIMARY KEY,
  email            TEXT NOT NULL,
  granted_by       TEXT NOT NULL REFERENCES users(id),
  duration_months  INTEGER,          -- NULL = vitalício
  status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'claimed')),
  claimed_user_id  TEXT REFERENCES users(id),
  claimed_at       TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS gifts_email_idx ON gifts(email);

-- Vectora Bot for GHA — token que a Action pública usa pra buscar
-- provider/modelo/chave em GET /gha-bot/config, sem carregar credencial
-- de provider direto nos Secrets do repositório do usuário.
CREATE TABLE IF NOT EXISTS gha_bot_tokens (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL,
  repo_scope TEXT,               -- NULL = qualquer repositório do usuário
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS gha_bot_tokens_user_id_idx ON gha_bot_tokens(user_id);
CREATE INDEX IF NOT EXISTS gha_bot_tokens_hash_idx ON gha_bot_tokens(token_hash);

-- Config do bot por usuário — 1 linha, editada pelo painel. Chave de
-- provider cifrada com AES-GCM (services/src/gha-bot/crypto.ts, chave
-- mestra única via binding GHA_BOT_ENCRYPTION_KEY) — nunca em texto
-- puro. Cloudflare Secrets Store não serve aqui: exige 1 binding
-- estático por secret, declarado no deploy — não dá pra ler um valor
-- dinâmico por usuário em runtime.
CREATE TABLE IF NOT EXISTS gha_bot_config (
  user_id                     TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  provider                    TEXT NOT NULL,
  model                       TEXT NOT NULL,
  provider_api_key_encrypted  TEXT NOT NULL,
  review_style                TEXT NOT NULL DEFAULT 'balanced' CHECK (review_style IN ('strict', 'balanced', 'lenient')),
  self_hosted_enabled        INTEGER NOT NULL DEFAULT 0,
  updated_at                  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS gha_bot_review_jobs (
  id              TEXT NOT NULL PRIMARY KEY,
  user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  callback_secret TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed')),
  review_text     TEXT,
  error           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS gha_bot_review_jobs_user_id_idx
  ON gha_bot_review_jobs(user_id);

-- Seed idempotente do catálogo de planos.
INSERT OR IGNORE INTO plans (id, months, price_usd_cents, price_brl_cents) VALUES
  ('1m',  1,   900,  2400),
  ('3m',  3,  2700,  7200),
  ('6m',  6,  5100, 13600),
  ('12m', 12, 9600, 25600),
  ('36m', 36, 25900, 69100);

-- Concede role admin + Pro vitalício ao criador do Vectora. Se a conta
-- ainda não existir no momento do deploy, os comandos abaixo são no-op
-- silencioso (WHERE não bate linha nenhuma) — reaplique este arquivo
-- (ver instrução no topo) depois que a conta existir.
UPDATE users SET role = 'admin'
WHERE email = 'bruno.soarxz@gmail.com';

INSERT INTO subscriptions (id, user_id, tier, status, provider, current_period_end)
SELECT lower(hex(randomblob(16))), id, 'pro', 'active', 'gift', NULL
FROM users
WHERE email = 'bruno.soarxz@gmail.com'
ON CONFLICT (user_id) DO UPDATE SET
  tier = 'pro',
  status = 'active',
  provider = 'gift',
  current_period_end = NULL,
  updated_at = datetime('now');
