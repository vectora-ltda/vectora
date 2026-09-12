from __future__ import annotations

import pytest

from backend.services import media_adapter, media_billing
from backend.services.media_quota import media_quota
from backend.tools import media
from backend.tools.context import ToolContext


def test_media_specs_hides_tools_blocked_for_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        media_adapter,
        "is_allowed",
        lambda user_id, name: user_id == "allowed" and name == "generate_image",
    )

    specs = media_adapter.media_specs("allowed")

    assert [spec.name for spec in specs] == ["generate_image"]


@pytest.mark.asyncio
async def test_invoke_media_applies_byok_before_media_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP/media adapter must bypass internal quota only for a user key."""
    observed: dict[str, object] = {}

    async def get_overrides(user_id: str) -> dict[str, str]:
        assert user_id == "user-1"
        return {"OPENAI_API_KEY": "user-key"}

    async def invoke(_arguments: dict[str, object], context: ToolContext) -> str:
        observed.update(context._extra)
        reservation, error = await media._reserve_media(context, "generate_image")
        assert reservation is None
        assert error is None
        return "ok"

    class FakeSpec:
        async def ainvoke(
            self, arguments: dict[str, object], context: ToolContext
        ) -> str:
            return await invoke(arguments, context)

    monkeypatch.setattr(
        media_billing, "PROVIDER_API_KEY_ENV", {"openai": "OPENAI_API_KEY"}
    )
    monkeypatch.setattr("backend.rbac.auth.get_env_overrides", get_overrides)
    monkeypatch.setattr(media_adapter, "is_allowed", lambda _user, _name: True)
    monkeypatch.setattr(media_adapter.TOOL_REGISTRY, "get", lambda _name: FakeSpec())
    monkeypatch.setattr(
        media_quota,
        "reserve",
        lambda **_kwargs: pytest.fail("BYOK não deve reservar quota"),
    )
    monkeypatch.setattr(
        media_quota,
        "summary",
        lambda *_args, **_kwargs: pytest.fail("BYOK não deve consultar quota"),
    )

    context = ToolContext(
        user_id="user-1", model="openai:gpt-5", thread_id="mcp", tool_call_id="call-1"
    )
    result = await media_adapter.invoke_media(
        "generate_image", {"prompt": "x"}, context
    )

    assert result == "ok"
    assert observed["media_billing_source"] == "byok"
