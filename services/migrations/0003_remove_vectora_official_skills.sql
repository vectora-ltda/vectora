-- Remove o seed legado que não deve aparecer no catálogo público.
DELETE FROM skills_catalog
WHERE catalog_source = 'curated'
  AND package_name LIKE '@vectora/%';
