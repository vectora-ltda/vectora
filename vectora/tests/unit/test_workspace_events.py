"""Regressions for workspace filesystem event streams."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request


@pytest.mark.asyncio
async def test_workspace_events_reports_missing_workspace_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import backend.workspace.workspace as workspace_module
    from backend.api.handlers.workspaces import workspace_events
    from backend.vtypes import Workspace

    missing = tmp_path / "deleted-workspace"
    workspace = Workspace(
        id="missing",
        name="missing",
        cwd=str(missing),
        created_at="2026-01-01T00:00:00+00:00",
    )
    monkeypatch.setattr(
        workspace_module,
        "workspace_registry",
        SimpleNamespace(
            get=lambda workspace_id: workspace if workspace_id == "missing" else None
        ),
    )

    request = cast(
        "Request",
        SimpleNamespace(is_disconnected=lambda: asyncio.sleep(0, result=False)),
    )
    response = await workspace_events("missing", request)
    iterator = cast("AsyncIterator[str]", response.body_iterator)
    event = await iterator.__anext__()

    assert response.media_type == "text/event-stream"
    assert "workspace_path_unavailable" in event
