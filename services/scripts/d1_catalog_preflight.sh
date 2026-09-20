#!/usr/bin/env bash
set -euo pipefail

# Atualiza apenas uma tabela já existente antes de reaplicar o schema base.
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

if table_exists skills_catalog; then
  :
else
  status=$?
  # Código 1 significa que a consulta funcionou e não encontrou a tabela.
  # Qualquer outro código representa uma falha real do Wrangler.
  if [ "$status" -eq 1 ]; then
    exit 0
  fi
  exit "$status"
fi

if schema=$(pnpm exec wrangler d1 execute vectora-db --remote \
  --command "PRAGMA table_info(skills_catalog)" --json 2>&1); then
  :
else
  status=$?
  echo "Falha ao consultar o schema D1: skills_catalog" >&2
  printf '%s\n' "$schema" >&2
  exit 2
fi

ensure_missing_column() {
  local column="$1" definition="$2"
  if ! grep -Eq "\\\"name\\\"[[:space:]]*:[[:space:]]*\\\"${column}\\\"" <<< "$schema"; then
    pnpm exec wrangler d1 execute vectora-db --remote \
      --command "ALTER TABLE skills_catalog ADD COLUMN ${column} ${definition}"
  fi
}

ensure_missing_column package_name "TEXT"
ensure_missing_column version "TEXT NOT NULL DEFAULT '0.0.1'"
ensure_missing_column tags "TEXT NOT NULL DEFAULT '[]'"
ensure_missing_column category "TEXT"
ensure_missing_column catalog_source "TEXT NOT NULL DEFAULT 'curated'"
ensure_missing_column vectora_verified "INTEGER NOT NULL DEFAULT 0"
ensure_missing_column publisher_id "TEXT"
ensure_missing_column verified "INTEGER NOT NULL DEFAULT 0"
ensure_missing_column downloads_count "INTEGER NOT NULL DEFAULT 0"
ensure_missing_column updated_at "TEXT"

# Bancos legados recebem a coluna como anulável porque SQLite não permite
# adicionar uma coluna com DEFAULT(datetime('now')). Repare as linhas antigas
# e preserve o comportamento do schema base para inserts futuros.
pnpm exec wrangler d1 execute vectora-db --remote \
  --command "UPDATE skills_catalog SET updated_at = datetime('now') WHERE updated_at IS NULL"
pnpm exec wrangler d1 execute vectora-db --remote \
  --command "CREATE TRIGGER IF NOT EXISTS skills_catalog_updated_at_default AFTER INSERT ON skills_catalog WHEN NEW.updated_at IS NULL BEGIN UPDATE skills_catalog SET updated_at = datetime('now') WHERE id = NEW.id AND updated_at IS NULL; END"
