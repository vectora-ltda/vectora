"""Testes para backend/api/handlers/connect.py — status e toggle do Vectora
Connect por plataforma. Chama os handlers direto (mesmo padrão de
test_api_settings_prefs.py) em vez de subir o app inteiro via TestClient —
mais isolado, sem competir por conexões SQLite com outros testes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

from backend.api.handlers.connect import (
    SetEnabledRequest,
    get_status,
    set_platform_enabled,
)
from backend.services.connect import manager
from backend.workspace.runtime_settings import RuntimeSettings


class _FakeRequest:
    def __init__(self, user_id: str | None) -> None:
        user = SimpleNamespace(id=user_id) if user_id else None
        self.state = SimpleNamespace(user=user)


def _req(user_id: str | None = "u1") -> Request:
    return cast("Request", _FakeRequest(user_id))


@pytest.fixture(autouse=True)
def _isolated_runtime_settings(tmp_path, monkeypatch):
    from backend.workspace import runtime_settings as rs_module

    fresh = RuntimeSettings(path=tmp_path / "settings.json")
    monkeypatch.setattr(rs_module, "runtime_settings", fresh)


@pytest.fixture(autouse=True)
def _sem_sync_de_verdade(monkeypatch):
    """`sync_adapters()` real chamaria _start_platform/_stop_platform, que
    tentam conectar em serviços externos de verdade — substitui por um
    espião que só registra a chamada."""
    calls: list[None] = []

    async def _fake_sync() -> dict[str, str]:
        calls.append(None)
        return {}

    monkeypatch.setattr(manager, "sync_adapters", _fake_sync)
    return calls


class TestGetStatus:
    async def test_lista_as_4_plataformas_com_estado_correto(self, monkeypatch):
        monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        for var in (
            "DISCORD_BOT_TOKEN",
            "SLACK_BOT_TOKEN",
            "SLACK_APP_TOKEN",
            "EMAIL_IMAP_HOST",
        ):
            monkeypatch.delenv(var, raising=False)

        out = await get_status(_req())

        assert set(out.keys()) == {"telegram", "discord", "slack", "email"}
        assert out["telegram"] == {
            "configured": True,
            "enabled": True,
            "running": False,
        }
        assert out["discord"]["configured"] is False

    async def test_sem_autenticacao_levanta_401(self):
        with pytest.raises(HTTPException) as exc_info:
            await get_status(_req(None))
        assert exc_info.value.status_code == 401


class TestSetPlatformEnabled:
    async def test_liga_plataforma_configurada_e_reconcilia(
        self, monkeypatch, _sem_sync_de_verdade
    ):
        monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")

        out = await set_platform_enabled(
            "discord", SetEnabledRequest(enabled=True), _req()
        )

        assert out == {"ok": True}
        assert manager.is_enabled("discord") is True
        assert len(_sem_sync_de_verdade) == 1

    async def test_liga_plataforma_sem_credencial_persiste_sem_erro(
        self, monkeypatch, _sem_sync_de_verdade
    ):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)

        out = await set_platform_enabled(
            "slack", SetEnabledRequest(enabled=True), _req()
        )

        assert out == {"ok": True}
        # Persiste a preferência — mas sem credencial, não conta como
        # configurada (configured_platforms() continua vazia pra ela).
        assert manager.is_enabled("slack") is True
        assert "slack" not in manager.configured_platforms()

    async def test_plataforma_desconhecida_404(self, _sem_sync_de_verdade):
        with pytest.raises(HTTPException) as exc_info:
            await set_platform_enabled(
                "myspace", SetEnabledRequest(enabled=True), _req()
            )
        assert exc_info.value.status_code == 404
        assert len(_sem_sync_de_verdade) == 0

    async def test_sem_autenticacao_levanta_401(self, _sem_sync_de_verdade):
        with pytest.raises(HTTPException) as exc_info:
            await set_platform_enabled(
                "discord", SetEnabledRequest(enabled=True), _req(None)
            )
        assert exc_info.value.status_code == 401
        assert len(_sem_sync_de_verdade) == 0
