from __future__ import annotations

import pytest

from backend.services.media_billing import (
    apply_media_billing_source,
    resolve_media_billing_source,
)
from backend.vtypes.context import VectoraContext


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"OPENAI_API_KEY": "sk-user"}, "byok"),
        ({"OPENAI_API_KEY": "   "}, None),
        ({}, None),
    ],
)
async def test_resolve_media_billing_source_usa_credencial_do_usuario(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str],
    expected: str | None,
) -> None:
    async def _overrides(_user_id: str) -> dict[str, str]:
        return overrides

    monkeypatch.setattr("backend.rbac.auth.get_env_overrides", _overrides)
    assert await resolve_media_billing_source("user-1", "openai:gpt-5") == expected


@pytest.mark.asyncio
async def test_resolve_media_billing_source_nao_confia_no_usuario_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def _overrides(_user_id: str) -> dict[str, str]:
        nonlocal called
        called = True
        return {"OPENAI_API_KEY": "sk-user"}

    monkeypatch.setattr("backend.rbac.auth.get_env_overrides", _overrides)
    assert await resolve_media_billing_source("local", "openai:gpt-5") is None
    assert called is False


@pytest.mark.asyncio
async def test_apply_media_billing_source_remove_marcador_nao_confirmado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = VectoraContext(user_id="user-1", model="openai:gpt-5")
    ctx._extra["media_billing_source"] = "byok"

    async def _none(_user_id: str, _model: str) -> str | None:
        return None

    monkeypatch.setattr(
        "backend.services.media_billing.resolve_media_billing_source", _none
    )
    await apply_media_billing_source(ctx)
    assert "media_billing_source" not in ctx._extra
