from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.api.handlers.chat import CancelRunRequest, cancel_run


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/"})


@pytest.mark.asyncio
async def test_cancel_run_checks_thread_ownership_and_reports_cancelled_count() -> None:
    request = _request()
    with (
        patch(
            "backend.api.handlers.threads._assert_owns_thread",
            new=AsyncMock(),
        ) as owns_thread,
        patch("backend.api.native_stream.cancel_active_runs", return_value=2),
    ):
        result = await cancel_run(CancelRunRequest(thread_id="thread-1"), request)

    owns_thread.assert_awaited_once_with("thread-1", request, require_existing=True)
    assert result == {"cancelled": True, "cancelled_runs": 2}


@pytest.mark.asyncio
async def test_cancel_run_does_not_cancel_when_thread_is_not_owned() -> None:
    request = _request()
    with (
        patch(
            "backend.api.handlers.threads._assert_owns_thread",
            new=AsyncMock(side_effect=HTTPException(status_code=404)),
        ),
        patch("backend.api.native_stream.cancel_active_runs") as cancel_active,
    ):
        with pytest.raises(HTTPException, match="404"):
            await cancel_run(CancelRunRequest(thread_id="other-user-thread"), request)

    cancel_active.assert_not_called()
