-- Upgrade bancos criados antes do escopo por repositório nos jobs de review.
-- O shape de 0001 já inclui a coluna para instalações novas; esta migração
-- completa instalações existentes antes de o Worker usar a coluna.
ALTER TABLE gha_bot_review_jobs ADD COLUMN repository TEXT;
