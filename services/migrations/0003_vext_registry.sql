-- VEXT metadata is stored in D1; immutable package bytes are stored in R2.
CREATE TABLE IF NOT EXISTS vext_publishers (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  public_key TEXT NOT NULL,
  fingerprint TEXT NOT NULL UNIQUE,
  revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS vext_extensions (
  id TEXT PRIMARY KEY,
  publisher_id TEXT NOT NULL REFERENCES vext_publishers(id),
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  homepage TEXT,
  vectora_verified INTEGER NOT NULL DEFAULT 0 CHECK (vectora_verified IN (0, 1)),
  revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(publisher_id, name)
);
CREATE TABLE IF NOT EXISTS vext_versions (
  id TEXT PRIMARY KEY,
  extension_id TEXT NOT NULL REFERENCES vext_extensions(id) ON DELETE CASCADE,
  version TEXT NOT NULL,
  api_version INTEGER NOT NULL,
  protocol_version INTEGER NOT NULL,
  runtime TEXT NOT NULL CHECK (runtime IN ('node', 'python', 'none')),
  platforms TEXT NOT NULL DEFAULT '["any"]',
  permissions TEXT NOT NULL DEFAULT '[]',
  dependencies TEXT NOT NULL DEFAULT '[]',
  changelog TEXT,
  size_bytes INTEGER NOT NULL,
  digest TEXT NOT NULL,
  r2_key TEXT NOT NULL UNIQUE,
  signature TEXT NOT NULL,
  sbom TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'published', 'revoked')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  published_at TEXT,
  UNIQUE(extension_id, version)
);
CREATE INDEX IF NOT EXISTS idx_vext_extensions_name ON vext_extensions(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_vext_versions_extension ON vext_versions(extension_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vext_versions_status ON vext_versions(status);
