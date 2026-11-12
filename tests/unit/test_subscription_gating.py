"""Testes de gating por tier de assinatura (free/pro).

`get_current_tier`/`require_pro` (`backend/rbac/subscription.py`) e os
pontos de integração que ainda dependem do Services. Storage configurado e
executado pelo operador não deve ser bloqueado pelo tier da assinatura.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.rbac import subscription as sub
from backend.services import license as lic
from backend.services.license import get_effective_storage_mode


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


def test_storage_mode_complete_nao_depende_da_assinatura(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.settings import settings

    monkeypatch.setattr(settings, "storage_mode", "complete")
    _write_cache("free")

    assert get_effective_storage_mode() == "complete"


class TestStorageFactoryGating:
    """Postgres administrado pelo operador não exige assinatura Pro."""

    def test_get_pg_pool_reaches_configuration_without_pro_tier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio
        from unittest.mock import AsyncMock

        import asyncpg

        from backend.storage import factory

        monkeypatch.setattr(factory, "_pg_pool", None)
        pool = object()
        create_pool = AsyncMock(return_value=pool)
        monkeypatch.setattr(asyncpg, "create_pool", create_pool)
        monkeypatch.setattr(factory, "_ensure_postgres_schema", AsyncMock())

        result = asyncio.run(factory.get_pg_pool(dsn="postgresql://x/y"))

        assert result is pool
        create_pool.assert_awaited_once_with(
            "postgresql://x/y", min_size=2, max_size=20
        )

    def test_get_pg_pool_without_dsn_is_a_configuration_error_for_free(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sem DSN, a ausência de configuração é reportada sem gate Pro."""
        import asyncio

        from backend.settings import settings
        from backend.storage import factory

        monkeypatch.setattr(factory, "_pg_pool", None)
        monkeypatch.setattr(settings, "postgres_dsn", None)
        with pytest.raises(RuntimeError, match="postgres_dsn não configurado"):
            asyncio.run(factory.get_pg_pool(dsn=None))
