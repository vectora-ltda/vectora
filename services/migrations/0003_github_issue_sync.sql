-- Link Company issues to the public intake repository and the core repository.
ALTER TABLE issues ADD COLUMN github_repo TEXT;
ALTER TABLE issues ADD COLUMN github_number INTEGER;
ALTER TABLE issues ADD COLUMN github_url TEXT;
ALTER TABLE issues ADD COLUMN github_sync_state TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE issues ADD COLUMN github_sync_error TEXT;
ALTER TABLE issues ADD COLUMN core_repo TEXT;
ALTER TABLE issues ADD COLUMN core_number INTEGER;
ALTER TABLE issues ADD COLUMN core_url TEXT;
ALTER TABLE issues ADD COLUMN approved_at TEXT;
ALTER TABLE issues ADD COLUMN approved_by TEXT;

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
  UNIQUE(issue_id, github_comment_id)
);

CREATE INDEX IF NOT EXISTS idx_issue_comments_issue
  ON issue_comments(issue_id, created_at ASC);
