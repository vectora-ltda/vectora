-- Garante que instalações existentes tenham uma linha operacional para cada
-- catálogo, permitindo distinguir "nunca sincronizado" de catálogo vazio.
INSERT OR IGNORE INTO registry_sync_state (source, status)
VALUES ('mcp', 'never'), ('skills', 'never');
