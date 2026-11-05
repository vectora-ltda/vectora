"""Runner de schema para SQLite — arquivo único (sqlite/schema.sql),
reaplicado inteiro sempre que seu conteúdo muda. Ver runner.py.
"""

from __future__ import annotations

from backend.storage.migrations.runner import MigrationRunner, run_migrations

__all__ = ["MigrationRunner", "run_migrations"]
