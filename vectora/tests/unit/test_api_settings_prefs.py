"""Testes de GET/PATCH /settings/prefs — preferências durável do frontend."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request

from backend.api.handlers.flags import get_prefs, update_prefs
from backend.workspace.runtime_settings import RuntimeSettings


class _FakeRequest:
    """Request fake com `.state.user` e `.json()` async, como os handlers usam."""

    def __init__(
        self,
        user_id: str | None,
        body: Any = None,
        query_params: dict[str, str] | None = None,
    ) -> None:
        user = SimpleNamespace(id=user_id) if user_id else None
        self.state = SimpleNamespace(user=user)
        self._body = body
        self.query_params = query_params or {}

    async def json(self) -> Any:
        return self._body


def _req(
    user_id: str | None,
    body: Any = None,
    query_params: dict[str, str] | None = None,
) -> Request:
    """`_FakeRequest` visto como `Request` — os handlers só tocam `.state.user`
    e `.json()`, cobertos pelo fake; o cast satisfaz a assinatura tipada."""
    return cast("Request", _FakeRequest(user_id, body, query_params))


@pytest.fixture(autouse=True)
def _isolated_runtime_settings(tmp_path, monkeypatch):
    """Isola cada teste com um RuntimeSettings próprio (nunca toca o real)."""
    from backend.api.handlers import flags as handler
    from backend.workspace import runtime_settings as rs_module

    fresh = RuntimeSettings(path=tmp_path / "settings.json")
    monkeypatch.setattr(rs_module, "runtime_settings", fresh)
    # get_prefs/update_prefs importam runtime_settings localmente (lazy import
    # dentro da função) — o monkeypatch no módulo já é suficiente.
    yield fresh
    del handler  # só pra deixar claro que não precisa mais nada daqui


def test_get_prefs_vazio_por_padrao():
    out = asyncio.run(get_prefs(_req("u1")))
    assert out == {}


def test_update_e_get_prefs_roundtrip():
    asyncio.run(update_prefs(_req("u1", {"selectedModel": "cohere:command-a"})))
    out = asyncio.run(get_prefs(_req("u1")))
    assert out == {"selectedModel": "cohere:command-a"}


def test_update_merge_parcial():
    asyncio.run(update_prefs(_req("u1", {"theme": "dark"})))
    asyncio.run(update_prefs(_req("u1", {"language": "pt"})))
    out = asyncio.run(get_prefs(_req("u1")))
    assert out == {"theme": "dark", "language": "pt"}


def test_update_ignora_campo_desconhecido():
    result = asyncio.run(
        update_prefs(_req("u1", {"theme": "dark", "campo_esquisito": 1}))
    )
    assert result == {"theme": "dark"}


def test_isola_por_usuario():
    asyncio.run(update_prefs(_req("u1", {"theme": "dark"})))
    asyncio.run(update_prefs(_req("u2", {"theme": "light"})))
    assert asyncio.run(get_prefs(_req("u1"))) == {"theme": "dark"}
    assert asyncio.run(get_prefs(_req("u2"))) == {"theme": "light"}


def test_decision_model_preferences_roundtrip_isolated_by_workspace():
    asyncio.run(
        update_prefs(
            _req(
                "u1",
                {"workspace_id": "workspace-a", "decisionModelsEnabled": True},
            )
        )
    )
    assert (
        asyncio.run(
            get_prefs(_req("u1", query_params={"workspace_id": "workspace-a"}))
        )["decisionModelsEnabled"]
        is True
    )
    assert "decisionModelsEnabled" not in asyncio.run(
        get_prefs(_req("u1", query_params={"workspace_id": "workspace-b"}))
    )


def test_transcription_mode_persists_only_supported_values():
    asyncio.run(update_prefs(_req("u1", {"transcriptionMode": "local"})))
    assert asyncio.run(get_prefs(_req("u1"))) == {"transcriptionMode": "local"}

    # Negative regression: unknown modes must not silently become a route.
    asyncio.run(update_prefs(_req("u1", {"transcriptionMode": "mystery"})))
    assert asyncio.run(get_prefs(_req("u1"))) == {"transcriptionMode": "local"}


def test_decision_model_workspace_preferences_reject_foreign_workspace(monkeypatch):
    from fastapi import HTTPException

    from backend.workspace.workspace import workspace_registry

    monkeypatch.setattr(
        workspace_registry,
        "get",
        lambda workspace_id: SimpleNamespace(id=workspace_id, owner_id="u2"),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            update_prefs(
                _req(
                    "u1",
                    {
                        "workspace_id": "workspace-private",
                        "decisionModelsEnabled": True,
                    },
                )
            )
        )
    assert exc.value.status_code == 403


def test_sem_user_cai_no_fallback_local():
    # request.state.user ausente (ex.: middleware não injetou) — usa "local".
    asyncio.run(update_prefs(_req(None, {"theme": "dark"})))
    assert asyncio.run(get_prefs(_req("local"))) == {"theme": "dark"}


def test_body_nao_objeto_retorna_422():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        asyncio.run(update_prefs(_req("u1", ["nao", "e", "objeto"])))
    assert exc.value.status_code == 422


def test_auto_update_enabled_persiste_e_convive_com_outras_prefs():
    """autoUpdateEnabled — lido pelo Electron main process (fetchBackendJson
    em main.ts) antes de agendar as checagens periódicas de update; precisa
    persistir como bool real, não só "chave presente"."""
    asyncio.run(update_prefs(_req("u1", {"theme": "dark", "autoUpdateEnabled": False})))
    out = asyncio.run(get_prefs(_req("u1")))
    assert out == {"theme": "dark", "autoUpdateEnabled": False}

    # Par de erro/borda: o backend não valida tipo (só a allowlist de chave);
    # o Electron trata qualquer valor != False como "ligado" (fail-open) —
    # documentado aqui pra não haver surpresa se alguém adicionar validação
    # de tipo depois sem revisar esse contrato.
    asyncio.run(update_prefs(_req("u1", {"autoUpdateEnabled": "nao-eh-bool"})))
    out = asyncio.run(get_prefs(_req("u1")))
    assert out["autoUpdateEnabled"] == "nao-eh-bool"
