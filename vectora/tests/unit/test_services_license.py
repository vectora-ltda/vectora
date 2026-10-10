"""Testes do serviço de licença."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.services import license as lic


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redireciona cache e config.toml para tmp e zera env relevantes."""
    cache = tmp_path / "license_cache.json"
    monkeypatch.setattr(lic, "CACHE_PATH", cache)
    # _get_token() agora cai para o config.toml — isola para não ler o real.
    monkeypatch.setattr(lic, "CONFIG_PATH", tmp_path / "config.toml")
    for var in ("VECTORA_TOKEN", "VECTORA_LICENSE_BYPASS", "VECTORA_LICENSE_URL"):
        monkeypatch.delenv(var, raising=False)


def _write_cache_fixture(payload: dict) -> None:
    lic.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    lic.CACHE_PATH.write_text(json.dumps(payload), encoding="utf-8")


class TestBootstrapVectoraHome:
    """CACHE_PATH/CONFIG_PATH precisam respeitar VECTORA_HOME — sem isso,
    uma instância isolada (testes, verificação ao vivo) lê/escreve
    silenciosamente no `~/.vectora` real do usuário, vazando token/cache de
    licença reais pra fora do isolamento pretendido."""

    def test_respeita_vectora_home_quando_setado(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("VECTORA_HOME", str(tmp_path))
        assert lic._bootstrap_vectora_home() == tmp_path

    def test_cai_pro_home_real_sem_vectora_home(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("VECTORA_HOME", raising=False)
        assert lic._bootstrap_vectora_home() == Path.home() / ".vectora"

    def test_expande_home_e_respeita_perfil(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VECTORA_HOME", raising=False)
        monkeypatch.setenv("VECTORA_RUNTIME_PROFILE", "preview")
        assert lic._bootstrap_vectora_home() == Path.home() / ".vectora-preview"

        monkeypatch.setenv("VECTORA_HOME", "~/vectora-license")
        assert lic._bootstrap_vectora_home() == Path.home() / "vectora-license"


def test_bypass_returns_active_pro(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
    info = lic.validate_license_sync()
    assert info.tier == "pro"
    assert info.status == "active"
    assert info.days_remaining > 300


def test_missing_token_returns_free_tier() -> None:
    """Sem VECTORA_TOKEN → tier free direto (uso local, sem conta), não erro."""
    info = lic.validate_license_sync()
    assert info.tier == "free"
    assert info.status == "active"
    assert info.cached is False


def test_missing_token_writes_cache() -> None:
    """O status free é escrito no cache — sem isso, get_current_tier()/GET
    /license/status veriam tier=None (não free) até a 1ª validação com token,
    quebrando o gating de subscription logo após o boot (par de erro)."""
    assert not lic.CACHE_PATH.exists()
    lic.validate_license_sync()
    assert lic.CACHE_PATH.exists()
    cached = lic.read_cached_status()
    assert cached is not None
    assert cached.tier == "free"


def test_cache_fresh_returns_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VECTORA_TOKEN", "tok_abc")
    _write_cache_fixture(
        {
            "tier": "free",
            "status": "active",
            "days_remaining": 25,
            "expires_at": "2027-01-01",
            "validated_at": datetime.now(UTC).isoformat(),
        }
    )
    info = lic.validate_license_sync()
    assert info.cached is True
    assert info.tier == "free"
    assert info.days_remaining == 25


def test_cache_stale_offline_within_48h(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache ficou >6h mas <48h → ainda devolve em modo offline graceful."""
    monkeypatch.setenv("VECTORA_TOKEN", "tok_abc")
    monkeypatch.setenv("VECTORA_LICENSE_URL", "http://127.0.0.1:1/should-fail")
    stale = (datetime.now(UTC) - timedelta(hours=10)).isoformat()
    _write_cache_fixture(
        {
            "tier": "free",
            "status": "active",
            "days_remaining": 25,
            "expires_at": "2027-01-01",
            "validated_at": stale,
        }
    )
    info = lic.validate_license_sync()
    assert info.cached is True
    assert info.status == "active"


class _FakeResponse:
    """Resposta httpx mínima para simular a edge function."""

    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _patch_remote(
    monkeypatch: pytest.MonkeyPatch, payload: dict, status_code: int = 200
) -> None:
    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None: ...

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> None: ...

        async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
            return _FakeResponse(payload, status_code)

    monkeypatch.setattr(lic.httpx, "AsyncClient", _FakeClient)


def test_remote_valid_false_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Edge function responde 200 {valid: false} → LicenseError (não 'active')."""
    monkeypatch.setenv("VECTORA_TOKEN", "tok_invalido")
    _patch_remote(monkeypatch, {"valid": False, "reason": "not_found"})
    with pytest.raises(lic.LicenseError, match="inválido"):
        lic.validate_license_sync()


def test_remote_valid_false_expired_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VECTORA_TOKEN", "tok_abc")
    _patch_remote(monkeypatch, {"valid": False, "reason": "expired"})
    with pytest.raises(lic.LicenseError, match="expirada"):
        lic.validate_license_sync()


def test_remote_valid_true_parses_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VECTORA_TOKEN", "tok_abc")
    _patch_remote(
        monkeypatch,
        {
            "valid": True,
            "reason": "valid",
            "tier": "pro",
            "status": "trial",
            "days_remaining": 12,
            "expires_at": "2026-06-23",
        },
    )
    info = lic.validate_license_sync()
    assert info.tier == "pro"
    assert info.status == "trial"
    assert info.days_remaining == 12
    assert info.cached is False


def test_force_skips_fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """force=True revalida no servidor mesmo com cache fresco."""
    import asyncio

    monkeypatch.setenv("VECTORA_TOKEN", "tok_abc")
    _write_cache_fixture(
        {
            "tier": "free",
            "status": "active",
            "days_remaining": 25,
            "expires_at": "2027-01-01",
            "validated_at": datetime.now(UTC).isoformat(),
        }
    )
    _patch_remote(monkeypatch, {"valid": True, "tier": "pro", "status": "active"})
    info = asyncio.run(lic.validate_license_async(force=True))
    assert info.cached is False
    assert info.tier == "pro"  # veio do remoto, não do cache "free"


def test_get_token_falls_back_to_config() -> None:
    """Sem env var, o token persistido em config.toml [license] é usado."""
    lic.CONFIG_PATH.write_text('[license]\ntoken = "tok_do_toml"\n', encoding="utf-8")
    assert lic._get_token() == "tok_do_toml"
    assert os.environ["VECTORA_TOKEN"] == "tok_do_toml"


def test_read_cached_status_returns_none_when_no_cache() -> None:
    assert lic.read_cached_status() is None


def test_read_cached_status_returns_info() -> None:
    _write_cache_fixture(
        {
            "tier": "pro",
            "status": "trial",
            "days_remaining": 5,
            "expires_at": "2026-06-30",
            "validated_at": datetime.now(UTC).isoformat(),
        }
    )
    info = lic.read_cached_status()
    assert info is not None
    assert info.tier == "pro"
    assert info.status == "trial"
    assert info.days_remaining == 5
