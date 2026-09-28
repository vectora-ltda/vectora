-- Token monotônico por fonte: uma descoberta antiga não pode promover um
-- snapshot depois que uma execução mais nova assumiu a fonte.
CREATE TABLE IF NOT EXISTS registry_sync_runs (
  source     TEXT PRIMARY KEY,
  token      TEXT NOT NULL,
  started_at TEXT NOT NULL DEFAULT (datetime('now'))
);
