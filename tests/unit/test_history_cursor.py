"""Cursor estável do histórico paginado (`before_message_id` e `next_cursor`).

A janela é ancorada na identidade da mensagem, não na posição: mensagens novas
no fim da conversa entre duas páginas não repetem nem pulam nenhuma.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.api.handlers.threads import get_thread_history_paginated
from backend.api.schemas import PagedHistoryResponse, Thread
from backend.vtypes.history import ThreadHistoryEntry


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/threads/t1/history",
            "headers": [],
            "scheme": "http",
            "server": ("test", 80),
            "state": {},
        }
    )


def _entries(ids: list[int | None]) -> list[ThreadHistoryEntry]:
    return [
        ThreadHistoryEntry(
            role="human" if position % 2 == 0 else "assistant",
            text=f"m{message_id}",
            checkpoint_id=str(message_id),
            message_id=message_id,
        )
        for position, message_id in enumerate(ids)
    ]


async def _page(
    entries: list[ThreadHistoryEntry],
    *,
    limit: int,
    offset: int = 0,
    before_message_id: int | None = None,
    todos: AsyncMock | None = None,
) -> PagedHistoryResponse:
    store = SimpleNamespace(
        get_session=AsyncMock(return_value={"user_id": "local"}),
        get_thread_summaries=AsyncMock(return_value=[]),
    )
    with (
        patch("backend.api.handlers.threads._assert_owns_thread", new=AsyncMock()),
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(return_value=Thread(id="t1", created_at="", updated_at="")),
        ),
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(return_value=store),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages_with_files",
            new=AsyncMock(return_value=entries),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_todos",
            new=todos or AsyncMock(return_value=[]),
        ),
        patch("backend.persistence.kv.get_kv", new=AsyncMock(side_effect=OSError)),
    ):
        return await get_thread_history_paginated(
            "t1",
            _request(),
            limit=limit,
            offset=offset,
            before_message_id=before_message_id,
        )


def _ids(page: PagedHistoryResponse) -> list[int | None]:
    return [message.message_id for message in page.messages]


async def test_paginar_pelo_cursor_cobre_cada_mensagem_uma_vez() -> None:
    entries = _entries(list(range(1, 11)))

    newest = await _page(entries, limit=4)
    middle = await _page(entries, limit=4, before_message_id=newest.next_cursor)
    oldest = await _page(entries, limit=4, before_message_id=middle.next_cursor)

    assert _ids(newest) == [7, 8, 9, 10]
    assert _ids(middle) == [3, 4, 5, 6]
    assert _ids(oldest) == [1, 2]
    assert (newest.has_more, middle.has_more, oldest.has_more) == (True, True, False)
    assert oldest.next_cursor is None


async def test_mensagem_nova_entre_paginas_nao_desloca_a_janela() -> None:
    antes = _entries(list(range(1, 9)))
    primeira = await _page(antes, limit=3)

    depois = _entries(list(range(1, 11)))  # duas mensagens novas no fim
    segunda = await _page(depois, limit=3, before_message_id=primeira.next_cursor)

    assert _ids(primeira) == [6, 7, 8]
    assert _ids(segunda) == [3, 4, 5]


async def test_offset_continua_valendo_sem_cursor() -> None:
    entries = _entries(list(range(1, 11)))

    page = await _page(entries, limit=3, offset=3)

    assert _ids(page) == [5, 6, 7]


async def test_cursor_vence_o_offset() -> None:
    entries = _entries(list(range(1, 11)))

    page = await _page(entries, limit=3, offset=5, before_message_id=4)

    assert _ids(page) == [1, 2, 3]
    assert page.has_more is False


async def test_cursor_inexistente_responde_409() -> None:
    with pytest.raises(HTTPException) as error:
        await _page(_entries([1, 2, 3]), limit=2, before_message_id=99)

    assert error.value.status_code == 409


async def test_mensagem_mais_antiga_sem_id_nao_gera_cursor() -> None:
    entries = _entries([1, 2, None, 4, 5])

    page = await _page(entries, limit=2)

    assert _ids(page) == [4, 5]
    assert page.has_more is True
    assert page.next_cursor == 4


async def test_pagina_cujo_inicio_nao_tem_id_devolve_none_e_cliente_usa_offset() -> (
    None
):
    entries = _entries([1, 2, None, 4, 5])

    page = await _page(entries, limit=3)

    assert _ids(page) == [None, 4, 5]
    assert page.has_more is True
    assert page.next_cursor is None


_TODO = {"id": "1", "content": "escrever o teste", "status": "pending"}


@pytest.mark.asyncio
async def test_pagina_mais_recente_traz_as_tarefas() -> None:
    page = await _page(
        _entries([1, 2, 3]), limit=2, todos=AsyncMock(return_value=[_TODO])
    )

    assert [todo.content for todo in page.todos] == ["escrever o teste"]


@pytest.mark.asyncio
async def test_paginas_anteriores_nao_repetem_as_tarefas() -> None:
    todos = AsyncMock(return_value=[_TODO])

    por_cursor = await _page(
        _entries([1, 2, 3]), limit=2, before_message_id=3, todos=todos
    )
    por_offset = await _page(_entries([1, 2, 3]), limit=2, offset=1, todos=todos)

    assert por_cursor.todos == []
    assert por_offset.todos == []
    todos.assert_not_called()


@pytest.mark.asyncio
async def test_falha_nas_tarefas_nao_derruba_o_historico() -> None:
    page = await _page(
        _entries([1, 2, 3]), limit=2, todos=AsyncMock(side_effect=RuntimeError("x"))
    )

    assert _ids(page) == [2, 3]
    assert page.todos == []
