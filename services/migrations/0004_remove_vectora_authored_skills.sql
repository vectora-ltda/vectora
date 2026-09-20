-- Remove qualquer variante legada das skills autorais da Vectora.
DELETE FROM skills_catalog
WHERE package_name LIKE '@vectora/%'
   OR id LIKE 'vectora/%'
   OR id LIKE 'vectora-%';
