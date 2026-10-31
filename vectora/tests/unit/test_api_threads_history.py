"""GET /threads/{thread_id}/history.

Cobre: paginação por offset/limit, has_more, MESSAGES_CAP, erro de thread inexistente.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, call, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.api.handlers import threads as threads_handlers
from backend.persistence.native.agent_run import AgentRun, AgentRunTimelineEntry

os.environ.setdefault("VECTORA_AUTH_REQUIRED", "false")


def _make_app() -> Any:
    from backend.api.server import create_app

    return create_app()


def _fake_thread():
    from backend.api.schemas import Thread

    return Thread(id="t1", created_at="", updated_at="", workspace_id="")


def _patch_get_thread():
    return patch(
        "backend.api.handlers.threads.get_thread",
        new_callable=AsyncMock,
        return_value=_fake_thread(),
    )


def _pairs(n: int, start: int = 0) -> list[tuple[str, str, str, list]]:
    """Gera n quadras (role, text, checkpoint_id, attachments_meta) alternando human/assistant."""
    out = []
    for i in range(n):
        role = "human" if i % 2 == 0 else "assistant"
        out.append((role, f"message {start + i}", f"cp{start + i}", []))
    return out


def _history_entries(
    pairs: list[tuple[str, str, str, list]],
) -> list[object]:
    """Converte fixtures legadas no contrato atual de histórico com arquivos."""
    from backend.vtypes.history import ThreadHistoryEntry

    return [
        ThreadHistoryEntry(
            role=cast("Literal['human', 'assistant']", role),
            text=text,
            checkpoint_id=checkpoint_id,
            message_id=index + 1,
            attachments=attachments,
        )
        for index, (role, text, checkpoint_id, attachments) in enumerate(pairs)
    ]


def _request_for_user(user_id: str) -> Request:
    """Cria request autenticado mínimo para testar a fronteira de posse."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/threads/t1/history",
            "headers": [],
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "root_path": "",
            "query_string": b"",
        }
    )
    request.state.user = type("User", (), {"id": user_id})()
    return request


def _branch_store() -> SimpleNamespace:
    """Store mínimo para exercitar o contrato HTTP de branches."""
    return SimpleNamespace(
        list_branch_heads=AsyncMock(
            return_value=[
                {
                    "head_message_id": 2,
                    "created_at": "2026-01-01T00:00:00Z",
                    "active": True,
                    "message_count": 2,
                },
                {
                    "head_message_id": 3,
                    "created_at": "2026-01-01T00:01:00Z",
                    "active": False,
                    "message_count": 2,
                },
            ]
        ),
        get_branch_head_id=AsyncMock(return_value=2),
        compare_branches=AsyncMock(
            return_value={
                "active_head_message_id": 2,
                "selected_head_message_id": 3,
                "common_message_ids": [1],
                "active_divergent_message_ids": [2],
                "selected_divergent_message_ids": [3],
            }
        ),
        set_branch_head=AsyncMock(),
        get_session=AsyncMock(return_value={"thread_id": "t1", "user_id": "local"}),
    )


# ---------------------------------------------------------------------------
# GET /threads/{thread_id}/history — paginação básica
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_retorna_todas_quando_menos_de_200() -> None:
    """Menos de 200 mensagens → has_more=False, retorna todas."""
    pairs = _pairs(10)
    with (
        _patch_get_thread(),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new_callable=AsyncMock,
            return_value=_history_entries(pairs),
        ),
    ):
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/threads/t1/history")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 10
    assert data["has_more"] is False
    assert data["total_count"] == 10


@pytest.mark.asyncio
async def test_history_limita_a_limit_quando_especificado() -> None:
    """limit=5 retorna só 5 mensagens (mais recentes)."""
    pairs = _pairs(20)
    with (
        _patch_get_thread(),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new_callable=AsyncMock,
            return_value=_history_entries(pairs),
        ),
    ):
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/threads/t1/history?limit=5")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 5
    assert data["has_more"] is True
    assert data["total_count"] == 20


@pytest.mark.asyncio
async def test_history_offset_pula_mensagens_recentes() -> None:
    """offset=5 com limit=5 retorna mensagens mais antigas."""
    pairs = _pairs(20)
    with (
        _patch_get_thread(),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new_callable=AsyncMock,
            return_value=_history_entries(pairs),
        ),
    ):
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/threads/t1/history?limit=5&offset=5")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 5
    # Mensagens com offset=5 vêm do índice 15 ao 19 (mais antigas)
    assert data["has_more"] is True
    assert data["total_count"] == 20


@pytest.mark.asyncio
async def test_history_cap_200_quando_sem_limit() -> None:
    """Sem limit especificado, cap é 200 mensagens mais recentes."""
    pairs = _pairs(250)
    with (
        _patch_get_thread(),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new_callable=AsyncMock,
            return_value=_history_entries(pairs),
        ),
    ):
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/threads/t1/history")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 200
    assert data["has_more"] is True
    assert data["total_count"] == 250


@pytest.mark.asyncio
async def test_history_thread_sem_mensagens_retorna_vazio() -> None:
    """Thread sem mensagens → lista vazia, has_more=False."""
    with (
        _patch_get_thread(),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/threads/t1/history")

    assert resp.status_code == 200
    data = resp.json()
    assert data["messages"] == []
    assert data["has_more"] is False
    assert data["total_count"] == 0


@pytest.mark.asyncio
async def test_history_cursor_capturado_antes_da_leitura_nao_pula_mensagem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uma gravação concorrente não pode fazer o cursor exceder a página lida."""
    from backend.api.schemas import GetHistoryRequest
    from backend.vtypes.history import ThreadHistoryEntry

    store = SimpleNamespace(
        get_thread_summaries=AsyncMock(
            return_value=[SimpleNamespace(status="idle", last_seq=1)]
        )
    )
    pairs = [
        ThreadHistoryEntry(
            role="human",
            text="somente a mensagem do snapshot",
            checkpoint_id="1",
            message_id=1,
        )
    ]

    with (
        _patch_get_thread(),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(return_value=store),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(return_value=pairs),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_todos",
            new=AsyncMock(return_value=[]),
        ),
    ):
        from backend.api.handlers.threads import get_history

        response = await get_history(GetHistoryRequest(thread_id="t1"))

    assert response.last_seq == 1
    assert [message.message_id for message in response.messages] == [1]


@pytest.mark.asyncio
async def test_history_cursor_nunca_avanca_alem_da_mensagem_lida() -> None:
    """Um evento concorrente posterior permanece disponível para replay SSE."""
    from backend.api.handlers.threads import get_history
    from backend.api.schemas import GetHistoryRequest
    from backend.vtypes.history import ThreadHistoryEntry

    store = SimpleNamespace(
        get_thread_summaries=AsyncMock(
            return_value=[SimpleNamespace(status="idle", last_seq=8)]
        ),
        get_event_seq_by_message_id=AsyncMock(return_value=3),
    )
    messages = [
        ThreadHistoryEntry(
            role="assistant", text="snapshot", checkpoint_id="1", message_id=1
        )
    ]
    with (
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(return_value=_fake_thread()),
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(return_value=store),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(return_value=messages),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_todos",
            new=AsyncMock(return_value=[]),
        ),
    ):
        response = await get_history(
            GetHistoryRequest(thread_id="t1"), _request_for_user("alice")
        )

    assert response.last_seq == 3
    store.get_event_seq_by_message_id.assert_awaited_once_with("t1", 1)


@pytest.mark.asyncio
async def test_history_interleaving_preserva_evento_para_replay_sse() -> None:
    """Uma gravação entre resumo e mensagens continua elegível ao replay."""
    from backend.api.handlers.threads import get_history
    from backend.api.schemas import GetHistoryRequest
    from backend.vtypes.history import ThreadHistoryEntry

    replayed: list[dict[str, object]] = []
    store = SimpleNamespace(
        get_thread_summaries=AsyncMock(
            return_value=[SimpleNamespace(status="running", last_seq=1)]
        ),
        get_event_seq_by_message_id=AsyncMock(return_value=1),
        list_events=AsyncMock(
            side_effect=lambda _thread_id, *, after_seq=0, **_kwargs: (
                [{"seq": 2, "kind": "message_created"}] if after_seq < 2 else []
            )
        ),
    )
    messages = [
        ThreadHistoryEntry(
            role="assistant", text="snapshot", checkpoint_id="1", message_id=1
        )
    ]

    async def _read_messages(*_args: object, **_kwargs: object) -> list[object]:
        # This models a commit that occurs after the summary snapshot but
        # before the message query.  The new event has no message in this page.
        replayed.extend(await store.list_events("t1", after_seq=1))
        return cast("list[object]", messages)

    with (
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(return_value=_fake_thread()),
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(return_value=store),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(side_effect=_read_messages),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_todos",
            new=AsyncMock(return_value=[]),
        ),
    ):
        response = await get_history(
            GetHistoryRequest(thread_id="t1"), _request_for_user("alice")
        )

    assert response.last_seq == 1
    assert replayed == [{"seq": 2, "kind": "message_created"}]


@pytest.mark.asyncio
async def test_get_history_preserva_404_de_thread_de_outro_usuario() -> None:
    """Histórico HTTP não converte falha de posse em 500 nem lê mensagens."""
    from fastapi import HTTPException

    from backend.api.handlers.threads import get_history
    from backend.api.schemas import GetHistoryRequest

    with (
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(
                side_effect=HTTPException(status_code=404, detail="not found")
            ),
        ) as load_thread,
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(),
        ) as load_messages,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_history(
                GetHistoryRequest(thread_id="thread-bob"),
                _request_for_user("alice"),
            )

    assert exc_info.value.status_code == 404
    load_thread.assert_awaited_once()
    call = load_thread.await_args
    assert call is not None
    assert call.kwargs["http_request"].state.user.id == "alice"
    load_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_history_isola_resumo_para_usuario_autenticado() -> None:
    """O resumo do histórico usa o mesmo usuário validado pela posse da thread."""
    from backend.api.handlers.threads import get_history
    from backend.api.schemas import GetHistoryRequest
    from backend.vtypes.history import ThreadHistoryEntry

    store = SimpleNamespace(
        get_thread_summaries=AsyncMock(
            return_value=[SimpleNamespace(status="running", last_seq=3)]
        )
    )
    messages = [
        ThreadHistoryEntry(
            role="assistant",
            text="mensagem de alice",
            checkpoint_id="3",
            message_id=3,
            is_error=False,
        )
    ]
    with (
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(return_value=_fake_thread()),
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(return_value=store),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(return_value=messages),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_todos",
            new=AsyncMock(return_value=[]),
        ),
    ):
        response = await get_history(
            GetHistoryRequest(thread_id="t1"), _request_for_user("alice")
        )

    assert response.run_status == "running"
    assert response.last_seq == 3
    assert [message.message_id for message in response.messages] == [3]
    store.get_thread_summaries.assert_awaited_once_with(["t1"], user_id="alice")


@pytest.mark.asyncio
async def test_get_history_preserva_mensagens_se_resumo_falhar() -> None:
    """A telemetria do resumo é opcional para o histórico principal."""
    from backend.api.handlers.threads import get_history
    from backend.api.schemas import GetHistoryRequest
    from backend.vtypes.history import ThreadHistoryEntry

    store = SimpleNamespace(
        get_thread_summaries=AsyncMock(side_effect=RuntimeError("store indisponível")),
        # A ledger cursor must not leak into the response when the summary
        # snapshot could not be read.
        get_event_seq_by_message_id=AsyncMock(return_value=99),
    )
    messages = [
        ThreadHistoryEntry(
            role="assistant",
            text="mensagem preservada",
            checkpoint_id="1",
            message_id=1,
            is_error=False,
        )
    ]
    with (
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(return_value=_fake_thread()),
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(return_value=store),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(return_value=messages),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_todos",
            new=AsyncMock(return_value=[]),
        ),
    ):
        response = await get_history(
            GetHistoryRequest(thread_id="t1"), _request_for_user("alice")
        )

    assert response.run_status == "idle"
    assert response.last_seq == 0
    assert [message.message_id for message in response.messages] == [1]


@pytest.mark.asyncio
async def test_get_history_preserva_mensagens_se_store_nao_puder_ser_resolvido() -> (
    None
):
    """Falha ao abrir o store não pode apagar o snapshot já carregável."""
    from backend.api.handlers.threads import get_history
    from backend.api.schemas import GetHistoryRequest
    from backend.vtypes.history import ThreadHistoryEntry

    messages = [
        ThreadHistoryEntry(
            role="assistant",
            text="mensagem preservada sem store",
            checkpoint_id="1",
            message_id=1,
            is_error=False,
        )
    ]
    with (
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(return_value=_fake_thread()),
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(side_effect=RuntimeError("store indisponível")),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(return_value=messages),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_todos",
            new=AsyncMock(return_value=[]),
        ),
    ):
        response = await get_history(
            GetHistoryRequest(thread_id="t1"), _request_for_user("alice")
        )

    assert response.run_status == "idle"
    assert response.last_seq == 0
    assert [message.message_id for message in response.messages] == [1]


@pytest.mark.asyncio
async def test_historico_paginado_projeta_run_failed_apos_falha_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GetHistory e a página HTTP expõem a falha persistida do AgentRun."""
    from backend.api.handlers.threads import get_thread_history_paginated
    from backend.vtypes.history import ThreadHistoryEntry

    store = SimpleNamespace(
        get_session=AsyncMock(return_value={"user_id": "local"}),
        get_thread_summaries=AsyncMock(
            return_value=[SimpleNamespace(status="failed", last_seq=2)]
        ),
    )
    failed = [
        ThreadHistoryEntry(
            role="assistant",
            text="[falha] provider indisponível",
            checkpoint_id="2",
            message_id=2,
            is_error=True,
        )
    ]

    with (
        patch("backend.api.handlers.threads._assert_owns_thread", new=AsyncMock()),
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(return_value=_fake_thread()),
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(return_value=store),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(return_value=failed),
        ),
    ):
        response = await get_thread_history_paginated(
            "t1", _request_for_user("local"), offset=0
        )

    assert response.run_status == "failed"
    assert response.last_seq == 2
    assert [message.message_id for message in response.messages] == [2]
    assert response.messages[0].is_error is True

    with (
        patch("backend.api.handlers.threads._assert_owns_thread", new=AsyncMock()),
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(return_value=_fake_thread()),
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(return_value=store),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(return_value=failed),
        ),
    ):
        empty_page = await get_thread_history_paginated(
            "t1", _request_for_user("local"), offset=1
        )
    assert empty_page.messages == []


@pytest.mark.asyncio
async def test_get_history_projeta_run_failed_apos_falha_terminal() -> None:
    """O endpoint RPC e a página HTTP usam o mesmo evento terminal."""
    from backend.api.handlers.threads import get_history
    from backend.api.schemas import GetHistoryRequest
    from backend.vtypes.history import ThreadHistoryEntry

    store = SimpleNamespace(
        get_thread_summaries=AsyncMock(
            return_value=[SimpleNamespace(status="failed", last_seq=2)]
        ),
        get_event_seq_by_message_id=AsyncMock(return_value=2),
    )
    messages = [
        ThreadHistoryEntry(
            role="assistant",
            text="[falha] provider indisponível",
            checkpoint_id="2",
            message_id=2,
            is_error=True,
        )
    ]

    with (
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(return_value=_fake_thread()),
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(return_value=store),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(return_value=messages),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_todos",
            new=AsyncMock(return_value=[]),
        ),
    ):
        response = await get_history(GetHistoryRequest(thread_id="t1"))

    assert response.run_status == "failed"
    assert response.last_seq == 2
    assert response.messages[0].is_error is True


@pytest.mark.asyncio
async def test_historico_paginado_nao_avanca_cursor_alem_da_pagina() -> None:
    """Um evento gravado durante a leitura permanece disponível para replay."""
    from backend.api.handlers.threads import get_thread_history_paginated
    from backend.vtypes.history import ThreadHistoryEntry

    store = SimpleNamespace(
        get_thread_summaries=AsyncMock(
            return_value=[SimpleNamespace(status="running", last_seq=8)]
        ),
        get_event_seq_by_message_id=AsyncMock(return_value=3),
    )
    messages = [
        ThreadHistoryEntry(
            role="assistant", text="snapshot", checkpoint_id="1", message_id=1
        )
    ]
    with (
        patch("backend.api.handlers.threads._assert_owns_thread", new=AsyncMock()),
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(return_value=_fake_thread()),
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(return_value=store),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(return_value=messages),
        ),
    ):
        response = await get_thread_history_paginated(
            "t1", _request_for_user("alice"), limit=10, offset=0
        )

    assert response.last_seq == 3
    store.get_event_seq_by_message_id.assert_awaited_once_with("t1", 1)


@pytest.mark.asyncio
async def test_historico_paginado_preserva_mensagens_se_resumo_falhar() -> None:
    """A falha de telemetria do resumo não pode apagar o histórico carregado."""
    from backend.api.handlers.threads import get_thread_history_paginated
    from backend.vtypes.history import ThreadHistoryEntry

    store = SimpleNamespace(
        get_thread_summaries=AsyncMock(side_effect=RuntimeError("store indisponível")),
        get_event_seq_by_message_id=AsyncMock(return_value=99),
    )
    messages = [
        ThreadHistoryEntry(
            role="human",
            text="mensagem preservada",
            checkpoint_id="1",
            message_id=1,
            is_error=False,
        )
    ]
    with (
        patch("backend.api.handlers.threads._assert_owns_thread", new=AsyncMock()),
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(return_value=_fake_thread()),
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(return_value=store),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(return_value=messages),
        ),
    ):
        response = await get_thread_history_paginated(
            "t1", _request_for_user("local"), offset=0
        )

    assert [message.content for message in response.messages] == ["mensagem preservada"]
    assert response.total_count == 1
    assert response.last_seq == 0


@pytest.mark.asyncio
async def test_historico_paginado_preserva_mensagens_se_store_nao_puder_ser_resolvido() -> (
    None
):
    """A página continua útil quando a resolução do store falha antes do resumo."""
    from backend.api.handlers.threads import get_thread_history_paginated
    from backend.vtypes.history import ThreadHistoryEntry

    messages = [
        ThreadHistoryEntry(
            role="human",
            text="mensagem preservada sem store",
            checkpoint_id="1",
            message_id=1,
            is_error=False,
        )
    ]
    with (
        patch("backend.api.handlers.threads._assert_owns_thread", new=AsyncMock()),
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(return_value=_fake_thread()),
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(side_effect=RuntimeError("store indisponível")),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(return_value=messages),
        ),
    ):
        response = await get_thread_history_paginated(
            "t1", _request_for_user("local"), offset=0
        )

    assert [message.content for message in response.messages] == [
        "mensagem preservada sem store"
    ]
    assert response.total_count == 1


@pytest.mark.asyncio
async def test_history_mensagens_na_ordem_cronologica() -> None:
    """Mensagens retornadas na ordem mais-antiga→mais-recente."""
    pairs = [
        ("human", "first", "cp0", []),
        ("assistant", "second", "cp1", []),
        ("human", "third", "cp2", []),
    ]
    with (
        _patch_get_thread(),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new_callable=AsyncMock,
            return_value=_history_entries(pairs),
        ),
    ):
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/threads/t1/history")

    data = resp.json()
    texts = [m["content"] for m in data["messages"]]
    assert texts == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_history_exige_posse_para_usuario_autenticado() -> None:
    """Usuário diferente do dono recebe 404 sem carregar mensagens."""
    store = AsyncMock()
    store.get_session.return_value = {"user_id": "owner"}
    with (
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(return_value=store),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new_callable=AsyncMock,
        ) as load_history,
    ):
        from fastapi import HTTPException

        from backend.api.handlers.threads import get_thread_history_paginated

        with pytest.raises(HTTPException) as exc:
            await get_thread_history_paginated("t1", _request_for_user("other"))

    assert exc.value.status_code == 404
    load_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_propaga_404_de_thread_inexistente() -> None:
    """Thread inexistente não vira página vazia com status 200."""
    from fastapi import HTTPException

    from backend.api.handlers.threads import get_thread_history_paginated

    with (
        patch("backend.api.handlers.threads._assert_owns_thread", new=AsyncMock()),
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(side_effect=HTTPException(status_code=404)),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_thread_history_paginated("missing", _request_for_user("owner"))

    assert exc.value.status_code == 404


async def test_branches_lista_compara_e_seleciona_ponta() -> None:
    """Os três endpoints preservam a ponta ativa e retornam o contrato público."""
    store = _branch_store()
    with (
        patch(
            "backend.api.handlers.threads._require_existing_thread",
            new_callable=AsyncMock,
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new_callable=AsyncMock,
            return_value=store,
        ),
    ):
        client = TestClient(_make_app())
        listed = client.get("/threads/t1/branches?limit=2")
        compared = client.get("/threads/t1/branches/3/compare")
        selected = client.post(
            "/threads/t1/branches/select", json={"head_message_id": 3}
        )

    assert listed.status_code == 200
    assert listed.json()["active_head_message_id"] == 2
    assert compared.status_code == 200
    assert compared.json()["selected_head_message_id"] == 3
    assert selected.status_code == 200
    assert selected.json()["active_head_message_id"] == 3
    store.compare_branches.assert_awaited_once_with("t1", 3)
    store.set_branch_head.assert_awaited_once_with("t1", 3)


@pytest.mark.asyncio
async def test_branches_rejeita_ponta_inexistente_com_404() -> None:
    """Uma ponta inexistente não pode ser comparada nem selecionada."""
    store = _branch_store()
    store.compare_branches.side_effect = ValueError("não é uma ponta")
    store.set_branch_head.side_effect = ValueError("não pertence")
    with (
        patch(
            "backend.api.handlers.threads._require_existing_thread",
            new_callable=AsyncMock,
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new_callable=AsyncMock,
            return_value=store,
        ),
    ):
        client = TestClient(_make_app())
        compared = client.get("/threads/t1/branches/99/compare")
        selected = client.post(
            "/threads/t1/branches/select", json={"head_message_id": 99}
        )

    assert compared.status_code == 404
    assert selected.status_code == 404


@pytest.mark.parametrize(
    ("sequences", "expected_cursor", "expected_message_ids"),
    [
        (["7"], 7, [2]),
        (["0"], 0, [2]),
        ([None, "5"], 5, [2, 1]),
        ([None, None], 0, [2, 1]),
        # A message from a run that started after the summary snapshot can
        # carry a newer seq; the cursor never moves past the summary.
        (["120"], 99, [2]),
        ([None, "100"], 99, [2, 1]),
    ],
)
async def test_safe_history_cursor_uses_latest_available_sequence(
    sequences: list[str | None],
    expected_cursor: int,
    expected_message_ids: list[int],
) -> None:
    """Skip missing IDs and sequences, stopping at the first represented event."""
    from backend.api.handlers.threads import _safe_history_cursor

    store = SimpleNamespace(
        get_event_seq_by_message_id=AsyncMock(side_effect=sequences)
    )
    entries = [
        SimpleNamespace(message_id=1),
        SimpleNamespace(message_id=2),
        SimpleNamespace(message_id=None),
    ]
    assert await _safe_history_cursor(store, "t1", entries, 99) == expected_cursor
    assert store.get_event_seq_by_message_id.await_args_list == [
        call("t1", message_id) for message_id in expected_message_ids
    ]


async def test_safe_history_cursor_without_message_ids_or_lookup() -> None:
    """An empty page returns zero; old stores retain the provided fallback."""
    from backend.api.handlers.threads import _safe_history_cursor

    store = SimpleNamespace(get_event_seq_by_message_id=AsyncMock())
    assert await _safe_history_cursor(store, "t1", [SimpleNamespace()], 99) == 0
    store.get_event_seq_by_message_id.assert_not_awaited()
    assert await _safe_history_cursor(None, "t1", [], 99) == 99


# --- Paginação da timeline de AgentRun ------------------------------------


class _TimelineStore:
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
def timeline_client_and_store(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, _TimelineStore]:
    store = _TimelineStore()

    async def _get_store() -> _TimelineStore:
        return store

    async def _owns(thread_id: str, request: object) -> None:
        return None

    monkeypatch.setattr(threads_handlers, "_get_session_store", _get_store)
    monkeypatch.setattr(threads_handlers, "_assert_existing_thread_ownership", _owns)
    app = FastAPI()
    app.include_router(threads_handlers.router)
    return TestClient(app), store


def test_timeline_encaminha_limit_e_offset_e_rejeita_valores_invalidos(
    timeline_client_and_store: tuple[TestClient, _TimelineStore],
) -> None:
    client, store = timeline_client_and_store

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
    app.include_router(threads_handlers.router)
    parameters = app.openapi()["paths"]["/runs/{run_id}/timeline"]["get"]["parameters"]
    by_name = {item["name"]: item["schema"] for item in parameters}

    assert by_name["limit"]["maximum"] == 500 and by_name["limit"]["minimum"] == 1
    assert by_name["offset"]["minimum"] == 0
    assert SimpleNamespace(**by_name).limit["default"] == 500
