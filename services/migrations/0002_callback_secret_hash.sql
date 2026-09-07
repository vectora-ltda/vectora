-- Store only a one-way digest for self-hosted review callbacks.
ALTER TABLE gha_bot_review_jobs ADD COLUMN callback_secret_hash TEXT;

CREATE INDEX IF NOT EXISTS gha_bot_review_jobs_callback_secret_hash_idx
  ON gha_bot_review_jobs(callback_secret_hash);
