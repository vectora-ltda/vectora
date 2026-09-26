-- Estado operacional das fontes do registry para distinguir catálogo vazio,
-- fonte indisponível e descoberta de Skills desabilitada.
CREATE TABLE IF NOT EXISTS registry_sync_state (
  source           TEXT PRIMARY KEY,
  status           TEXT NOT NULL DEFAULT 'never',
  last_synced_at   TEXT,
  last_error       TEXT,
  updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
