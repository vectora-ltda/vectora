"""Tests — storage/recipes/ (F9): Supabase, Neon, Qdrant Cloud.

Apenas testa build_dsn / build_config e configure_settings.
healthcheck() é skippado (requer infra externa).
"""

from __future__ import annotations

import pytest


class TestSupabaseRecipe:
    def test_build_dsn_direct(self):
        from backend.storage.recipes.supabase import build_dsn

        dsn = build_dsn(
            host="db.xxxx.supabase.co",
            password="secret",
            pooler=False,
        )
        assert "db.xxxx.supabase.co" in dsn
        assert "secret" in dsn
        assert dsn.startswith("postgresql")

    def test_build_dsn_pooler(self):
        from backend.storage.recipes.supabase import build_dsn

        dsn = build_dsn(
            host="db.xxxx.supabase.co",
            password="secret",
            pooler=True,
        )
        assert dsn.startswith("postgresql")
        assert "secret" in dsn

    def test_configure_settings(self, monkeypatch):
        from unittest.mock import MagicMock

        from backend.storage.recipes.supabase import configure_settings

        settings = MagicMock()
        configure_settings(
            settings,
            host="db.xxxx.supabase.co",
            password="secret",
        )
        assert settings.storage_mode == "complete"
        assert settings.postgres_dsn is not None


class TestNeonRecipe:
    def test_build_dsn_without_pooler(self):
        from backend.storage.recipes.neon import build_dsn

        dsn = build_dsn(
            host="ep-xxx.us-east-2.aws.neon.tech",
            user="alice",
            password="secret",
            database="neondb",
        )
        assert "neon.tech" in dsn
        assert "alice" in dsn
        assert dsn.startswith("postgresql")

    def test_build_dsn_with_pooler(self):
        from backend.storage.recipes.neon import build_dsn

        dsn = build_dsn(
            host="ep-xxx.us-east-2.aws.neon.tech",
            user="alice",
            password="secret",
            database="neondb",
            pooler=True,
        )
        # Com pooler, -pooler deve estar no hostname
        assert "-pooler" in dsn or "pooler" in dsn
        assert dsn.startswith("postgresql")

    def test_configure_settings(self, monkeypatch):
        from unittest.mock import MagicMock

        from backend.storage.recipes.neon import configure_settings

        settings = MagicMock()
        configure_settings(
            settings,
            host="ep-xxx.us-east-2.aws.neon.tech",
            user="alice",
            password="secret",
        )
        assert settings.storage_mode == "complete"
        assert settings.postgres_dsn is not None


class TestQdrantCloudRecipe:
    def test_build_config_with_url(self):
        from backend.storage.recipes.qdrant_cloud import build_config

        cfg = build_config(
            url="https://my-cluster.us-east-1.aws.cloud.qdrant.io",
            api_key="test-key",
        )
        assert cfg["url"] == "https://my-cluster.us-east-1.aws.cloud.qdrant.io"
        assert cfg["api_key"] == "test-key"
        assert "articles" in cfg["collections"]

    def test_build_config_from_cluster_id(self):
        from backend.storage.recipes.qdrant_cloud import build_config

        cfg = build_config(
            cluster_id="abc123",
            region="eu-west-1",
            api_key="key",
        )
        assert "abc123" in cfg["url"]
        assert "eu-west-1" in cfg["url"]

    def test_configure_settings(self):
        from unittest.mock import MagicMock

        from backend.storage.recipes.qdrant_cloud import configure_settings

        settings = MagicMock()
        configure_settings(
            settings,
            url="https://cluster.qdrant.io",
            api_key="key",
        )
        assert settings.storage_mode == "complete"
        assert settings.qdrant_url == "https://cluster.qdrant.io"
        assert settings.qdrant_api_key == "key"

    def test_default_collections(self):
        from backend.storage.recipes.qdrant_cloud import build_config

        cfg = build_config(api_key="x", url="https://x.qdrant.io")
        assert set(cfg["collections"]) >= {"articles", "web_cache", "search"}

    def test_custom_collections(self):
        from backend.storage.recipes.qdrant_cloud import build_config

        cfg = build_config(
            api_key="x",
            url="https://x.qdrant.io",
            collections=["my_collection"],
        )
        assert cfg["collections"] == ["my_collection"]
