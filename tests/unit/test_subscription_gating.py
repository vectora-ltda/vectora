"""Testes de gating por tier de assinatura (free/pro).

`get_current_tier`/`require_pro` (`backend/rbac/subscription.py`) e os
pontos de integração: `storage/factory.py` (Postgres/Qdrant → pro-only) e
`scheduling/background_tasks.py::create_task` (trigger_type="webhook" →
pro-only, testado em test_services_background.py::TestWebhookTaskRequiresPro).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.rbac import subscription as sub
from backend.services import license as lic


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lic, "CACHE_PATH", tmp_path / "license_cache.json")


def _write_cache(tier: str) -> None:
    lic.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    lic.CACHE_PATH.write_text(
        __import__("json").dumps(
            {
                "tier": tier,
                "status": "active",
                "days_remaining": 30,
                "expires_at": "2027-01-01",
                "validated_at": datetime.now(UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )


def test_get_current_tier_free_without_cache() -> None:
    """Sem cache local (nunca validou) → free, não erro (par de sucesso)."""
    assert sub.get_current_tier() == "free"


def test_get_current_tier_reads_cache() -> None:
    _write_cache("pro")
    assert sub.get_current_tier() == "pro"


def test_require_pro_raises_402_on_free() -> None:
    """Endpoint pro-only sem plano → 402, não 403 (falta pagar, não permissão)."""
    with pytest.raises(HTTPException) as exc:
        sub.require_pro()
    assert exc.value.status_code == 402
    assert "upgrade_url" in exc.value.detail


def test_require_pro_passes_on_pro() -> None:
    _write_cache("pro")
    sub.require_pro()  # não levanta


class TestStorageFactoryGating:
    """Postgres/Qdrant exigem pro — SQLite/LanceDB (free) seguem livres."""

    def test_get_pg_pool_raises_402_on_free(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        from backend.storage import factory

        monkeypatch.setattr(factory, "_pg_pool", None)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(factory.get_pg_pool(dsn="postgresql://x/y"))
        assert exc.value.status_code == 402

    def test_get_pg_pool_checks_tier_before_dsn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mesmo sem DSN configurado, o gate de tier dispara primeiro (edge:
        erro de tier não deve ser mascarado por erro de config)."""
        import asyncio

        from backend.storage import factory

        monkeypatch.setattr(factory, "_pg_pool", None)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(factory.get_pg_pool(dsn=None))
        assert exc.value.status_code == 402
