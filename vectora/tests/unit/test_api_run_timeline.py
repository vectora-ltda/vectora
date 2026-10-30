"""Paginação da timeline de AgentRun: `limit` e `offset` chegam ao store."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.handlers import threads as th
from backend.persistence.native.agent_run import AgentRun, AgentRunTimelineEntry


class _Store:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def get_agent_run(self, run_id: str) -> AgentRun:
        return AgentRun(run_id=run_id, thread_id="t1")

    async def get_agent_run_timeline(
        self, run_id: str, *, limit: int = 500, offset: int = 0
    ) -> list[AgentRunTimelineEntry]:
        self.calls.append({"run_id": run_id, "limit": limit, "offset": offset})
        return []


@pytest.fixture
def client_and_store(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _Store]:
    store = _Store()

    async def _get_store() -> _Store:
        return store

    async def _owns(thread_id: str, request: object) -> None:
        return None

    monkeypatch.setattr(th, "_get_session_store", _get_store)
    monkeypatch.setattr(th, "_assert_existing_thread_ownership", _owns)
    app = FastAPI()
    app.include_router(th.router)
    return TestClient(app), store


def test_timeline_encaminha_limit_e_offset_e_rejeita_valores_invalidos(
    client_and_store: tuple[TestClient, _Store],
) -> None:
    client, store = client_and_store

    assert client.get("/runs/r1/timeline").status_code == 200
    assert client.get("/runs/r1/timeline?limit=10&offset=20").status_code == 200
    assert store.calls == [
        {"run_id": "r1", "limit": 500, "offset": 0},
        {"run_id": "r1", "limit": 10, "offset": 20},
    ]

    for query in ("limit=0", "limit=501", "offset=-1", "limit=abc"):
        assert client.get(f"/runs/r1/timeline?{query}").status_code == 422
    assert len(store.calls) == 2


def test_openapi_declara_limit_e_offset_da_timeline() -> None:
    app = FastAPI()
    app.include_router(th.router)
    parameters = app.openapi()["paths"]["/runs/{run_id}/timeline"]["get"]["parameters"]
    by_name = {item["name"]: item["schema"] for item in parameters}

    assert by_name["limit"]["maximum"] == 500 and by_name["limit"]["minimum"] == 1
    assert by_name["offset"]["minimum"] == 0
    assert SimpleNamespace(**by_name).limit["default"] == 500
