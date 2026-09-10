-- Objetos auxiliares da sincronização GitHub. As colunas de `issues` vivem no
-- shape final de 0001_schema.sql; o upgrade operacional adiciona-as apenas a
-- bancos legados que já existiam antes desse shape.

CREATE UNIQUE INDEX IF NOT EXISTS idx_issues_github_identity
  ON issues(github_repo, github_number)
  WHERE github_repo IS NOT NULL AND github_number IS NOT NULL;

CREATE TABLE IF NOT EXISTS issue_comments (
  id TEXT PRIMARY KEY,
  issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
  github_comment_id INTEGER NOT NULL,
  author TEXT NOT NULL,
  body TEXT NOT NULL,
  html_url TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  UNIQUE(issue_id, github_comment_id)
);

CREATE INDEX IF NOT EXISTS idx_issue_comments_issue
  ON issue_comments(issue_id, created_at ASC);

CREATE TABLE IF NOT EXISTS issue_promotion_effects (
  issue_id       TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
  effect         TEXT NOT NULL CHECK (effect IN ('backlink', 'close')),
  operation_token TEXT NOT NULL,
  started_at     TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at   TEXT,
  PRIMARY KEY (issue_id, effect)
);

CREATE TABLE IF NOT EXISTS github_webhook_deliveries (
  delivery_id TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN ('processing', 'done', 'failed')),
  error TEXT,
  attempt_token TEXT,
  lease_until TEXT,
  received_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Bancos que já receberam a tabela antes do controle de lease precisam destas
-- colunas adicionadas pelo upgrade operacional antes do Worker ser publicado.
