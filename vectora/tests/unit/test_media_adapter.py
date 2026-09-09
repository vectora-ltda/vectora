from __future__ import annotations

import pytest

from backend.services import media_adapter


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
