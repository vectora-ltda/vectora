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

ensure_existing_column() {
  local table="$1" column="$2" definition="$3" schema status
  if table_exists "$table"; then
    :
  else
    status=$?
    # Código 1 significa que a consulta funcionou e não encontrou a tabela.
    # Qualquer outro código representa uma falha real do Wrangler.
    if [ "$status" -eq 1 ]; then
      return 0
    fi
    return "$status"
  fi

  if schema=$(pnpm exec wrangler d1 execute vectora-db --remote \
    --command "PRAGMA table_info(${table})" --json 2>&1); then
    :
  else
    status=$?
    echo "Falha ao consultar o schema D1: ${table}" >&2
    printf '%s\n' "$schema" >&2
    return 2
  fi
  if ! grep -Eq "\\\"name\\\"[[:space:]]*:[[:space:]]*\\\"${column}\\\"" <<< "$schema"; then
    pnpm exec wrangler d1 execute vectora-db --remote \
      --command "ALTER TABLE ${table} ADD COLUMN ${column} ${definition}"
  fi
}

ensure_existing_column skills_catalog package_name "TEXT"
ensure_existing_column skills_catalog version "TEXT NOT NULL DEFAULT '0.0.1'"
ensure_existing_column skills_catalog tags "TEXT NOT NULL DEFAULT '[]'"
ensure_existing_column skills_catalog category "TEXT"
ensure_existing_column skills_catalog catalog_source "TEXT NOT NULL DEFAULT 'curated'"
ensure_existing_column skills_catalog vectora_verified "INTEGER NOT NULL DEFAULT 0"
ensure_existing_column skills_catalog publisher_id "TEXT"
ensure_existing_column skills_catalog verified "INTEGER NOT NULL DEFAULT 0"
ensure_existing_column skills_catalog downloads_count "INTEGER NOT NULL DEFAULT 0"
ensure_existing_column skills_catalog updated_at "TEXT"
