-- Keeps discovery snapshots auditable and lets the public catalog hide entries
-- that disappeared from a complete upstream sync without deleting history.
ALTER TABLE mcp_catalog ADD COLUMN snapshot_id TEXT;
ALTER TABLE mcp_catalog ADD COLUMN last_seen_at TEXT;
ALTER TABLE mcp_catalog ADD COLUMN catalog_status TEXT NOT NULL DEFAULT 'active';

CREATE INDEX IF NOT EXISTS idx_mcp_catalog_public_rank
  ON mcp_catalog (catalog_status, stars_count DESC, updated_at DESC);
