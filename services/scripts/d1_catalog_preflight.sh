#!/usr/bin/env bash
set -euo pipefail

# Atualiza tabelas já existentes antes de reaplicar o schema base.
# Bancos D1 novos não precisam deste passo: o CREATE TABLE do schema base
# criará a tabela com todas as colunas.

table_exists() {
  local table="$1" result status
  if result=$(pnpm exec wrangler d1 execute vectora-db --remote \
    --command "SELECT name FROM sqlite_master WHERE type='table' AND name='${table}' LIMIT 1" --json 2>&1); then
    if grep -Eq "\\\"name\\\"[[:space:]]*:[[:space:]]*\\\"${table}\\\"" <<< "$result"; then
      return 0
    fi
    return 1
  fi
  status=$?
  echo "Falha ao consultar a existência da tabela D1: ${table}" >&2
  printf '%s\n' "$result" >&2
  return 2
}

skills_table_exists=false
if table_exists skills_catalog; then
  skills_table_exists=true
else
  status=$?
  # Código 1 significa que a consulta funcionou e não encontrou a tabela.
  if [ "$status" -ne 1 ]; then
    exit "$status"
  fi
fi

if [ "$skills_table_exists" = true ]; then
  if schema=$(pnpm exec wrangler d1 execute vectora-db --remote \
    --command "PRAGMA table_info(skills_catalog)" --json 2>&1); then
    :
  else
    status=$?
    echo "Falha ao consultar o schema D1: skills_catalog" >&2
    printf '%s\n' "$schema" >&2
    exit "$status"
  fi

  ensure_missing_skill_column() {
    local column="$1" definition="$2"
    if ! grep -Eq "\\\"name\\\"[[:space:]]*:[[:space:]]*\\\"${column}\\\"" <<< "$schema"; then
      pnpm exec wrangler d1 execute vectora-db --remote \
        --command "ALTER TABLE skills_catalog ADD COLUMN ${column} ${definition}"
    fi
  }

  ensure_missing_skill_column package_name "TEXT"
  ensure_missing_skill_column version "TEXT NOT NULL DEFAULT '0.0.1'"
  ensure_missing_skill_column tags "TEXT NOT NULL DEFAULT '[]'"
  ensure_missing_skill_column category "TEXT"
  ensure_missing_skill_column catalog_source "TEXT NOT NULL DEFAULT 'curated'"
  ensure_missing_skill_column vectora_verified "INTEGER NOT NULL DEFAULT 0"
  ensure_missing_skill_column publisher_id "TEXT"
  ensure_missing_skill_column verified "INTEGER NOT NULL DEFAULT 0"
  ensure_missing_skill_column downloads_count "INTEGER NOT NULL DEFAULT 0"
  ensure_missing_skill_column updated_at "TEXT"

  # Bancos legados recebem a coluna como anulável porque SQLite não permite
  # adicionar uma coluna com DEFAULT(datetime('now')). Repare as linhas antigas
  # e preserve o comportamento do schema base para inserts futuros.
  pnpm exec wrangler d1 execute vectora-db --remote \
    --command "CREATE TRIGGER IF NOT EXISTS skills_catalog_updated_at_default AFTER INSERT ON skills_catalog WHEN NEW.updated_at IS NULL BEGIN UPDATE skills_catalog SET updated_at = datetime('now') WHERE id = NEW.id AND updated_at IS NULL; END"
  pnpm exec wrangler d1 execute vectora-db --remote \
    --command "UPDATE skills_catalog SET updated_at = datetime('now') WHERE updated_at IS NULL"
fi

mcp_table_exists=false
if table_exists mcp_catalog; then
  mcp_table_exists=true
else
  status=$?
  if [ "$status" -ne 1 ]; then
    exit "$status"
  fi
fi

if [ "$mcp_table_exists" = true ]; then
  if mcp_schema=$(pnpm exec wrangler d1 execute vectora-db --remote \
    --command "PRAGMA table_info(mcp_catalog)" --json 2>&1); then
    :
  else
    status=$?
    echo "Falha ao consultar o schema D1: mcp_catalog" >&2
    printf '%s\n' "$mcp_schema" >&2
    exit "$status"
  fi

  ensure_missing_mcp_column() {
    local column="$1" definition="$2"
    if ! grep -Eq "\\\"name\\\"[[:space:]]*:[[:space:]]*\\\"${column}\\\"" <<< "$mcp_schema"; then
      pnpm exec wrangler d1 execute vectora-db --remote \
        --command "ALTER TABLE mcp_catalog ADD COLUMN ${column} ${definition}"
    fi
  }

  # The schema replay creates a ranking index over these columns. Add the
  # complete discovery shape first so legacy tables can reach that replay.
  ensure_missing_mcp_column icon_url "TEXT"
  ensure_missing_mcp_column publisher "TEXT"
  ensure_missing_mcp_column publisher_url "TEXT"
  ensure_missing_mcp_column stars_count "INTEGER NOT NULL DEFAULT 0"
  ensure_missing_mcp_column runtime_hint "TEXT"
  ensure_missing_mcp_column package_identifier "TEXT"
  ensure_missing_mcp_column transport "TEXT NOT NULL DEFAULT 'stdio'"
  ensure_missing_mcp_column server_url "TEXT"
  ensure_missing_mcp_column catalog_source "TEXT NOT NULL DEFAULT 'curated'"
  ensure_missing_mcp_column vectora_verified "INTEGER NOT NULL DEFAULT 0"
  ensure_missing_mcp_column downloads_count "INTEGER NOT NULL DEFAULT 0"
  ensure_missing_mcp_column snapshot_id "TEXT"
  ensure_missing_mcp_column last_seen_at "TEXT"
  ensure_missing_mcp_column catalog_status "TEXT NOT NULL DEFAULT 'active'"
  ensure_missing_mcp_column updated_at "TEXT"
  pnpm --silent wrangler d1 execute "$DB_NAME" "$REMOTE_FLAG" \
    --command "CREATE TRIGGER IF NOT EXISTS mcp_catalog_updated_at_default AFTER INSERT ON mcp_catalog WHEN NEW.updated_at IS NULL BEGIN UPDATE mcp_catalog SET updated_at = datetime('now') WHERE id = NEW.id AND updated_at IS NULL; END"
  pnpm --silent wrangler d1 execute "$DB_NAME" "$REMOTE_FLAG" \
    --command "UPDATE mcp_catalog SET updated_at = datetime('now') WHERE updated_at IS NULL"
  pnpm exec wrangler d1 execute vectora-db --remote \
    --command "CREATE INDEX IF NOT EXISTS idx_mcp_catalog_public_rank ON mcp_catalog(catalog_status, stars_count DESC, updated_at DESC)"
fi

# The base schema declares repository for fresh review-job tables, but D1
# instances that already have the table do not replay CREATE TABLE. Upgrade
# that legacy shape before review handlers start writing repository-scoped jobs.
review_jobs_table_exists=false
if table_exists gha_bot_review_jobs; then
  review_jobs_table_exists=true
else
  status=$?
  if [ "$status" -ne 1 ]; then
    exit "$status"
  fi
fi

if [ "$review_jobs_table_exists" = true ]; then
  if review_jobs_schema=$(pnpm exec wrangler d1 execute vectora-db --remote \
    --command "PRAGMA table_info(gha_bot_review_jobs)" --json 2>&1); then
    :
  else
    status=$?
    echo "Falha ao consultar o schema D1: gha_bot_review_jobs" >&2
    printf '%s\n' "$review_jobs_schema" >&2
    exit "$status"
  fi

  if ! grep -Eq '\"name\"[[:space:]]*:[[:space:]]*\"repository\"' <<< "$review_jobs_schema"; then
    pnpm exec wrangler d1 execute vectora-db --remote \
      --command "ALTER TABLE gha_bot_review_jobs ADD COLUMN repository TEXT"
  fi
fi
