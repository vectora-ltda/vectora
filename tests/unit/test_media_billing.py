from __future__ import annotations

import pytest

from backend.services.media_billing import (
    apply_media_billing_source,
)
from backend.vtypes.context import VectoraContext


@pytest.mark.asyncio
async def test_apply_media_billing_source_usa_provider_runtime_quando_modelo_vazio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = VectoraContext(user_id="user-1", model="")

    async def _overrides(_user_id: str) -> dict[str, str]:
        return {"OPENAI_API_KEY": "sk-user"}

    monkeypatch.setattr("backend.rbac.auth.get_env_overrides", _overrides)
    runtime_settings = __import__(
        "backend.workspace.runtime_settings", fromlist=["runtime_settings"]
    ).runtime_settings
    original_get = runtime_settings.get
    monkeypatch.setattr(
        runtime_settings,
        "get",
        lambda key, default=None: (
            "openai" if key == "active_provider" else original_get(key, default)
        ),
    )
    await apply_media_billing_source(ctx)
    assert ctx._extra["media_billing_source"] == "byok"
