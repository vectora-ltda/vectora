"""Contrato de criação, listagem e contagem de threads.

`CreateThread` marca a thread como vazia; `ListThreads` só a lista com
`include_empty`, devolve o total do filtro e um cursor estável; `CountThreads`
conta exatamente o que a lista devolveria, sempre dentro da posse do usuário.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
import pytest
from fastapi import HTTPException

from backend.api.handlers import threads as th
from backend.api.schemas import (
    CountThreadsRequest,
    CreateThreadRequest,
    ListThreadsRequest,
)
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool


def _http_request(user_id: str) -> MagicMock:
    request = MagicMock()
    user = MagicMock()
    user.id = user_id
    request.state = MagicMock(user=user, device_id=None)
    return request


@pytest.fixture
async def checkpoints_db() -> AsyncIterator[aiosqlite.Connection]:
    db = await aiosqlite.connect(":memory:")
    await th._ensure_schema(db)
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def session_store(tmp_path: Path) -> AsyncIterator[SessionStore]:
    pool = AsyncConnectionPool(str(tmp_path / "sessions.db"), min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    try:
        yield store
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _wire_stores(
    monkeypatch: pytest.MonkeyPatch,
    checkpoints_db: aiosqlite.Connection,
    session_store: SessionStore,
) -> None:
    async def _fake_get_db() -> aiosqlite.Connection:
        return checkpoints_db

    async def _fake_get_session_store() -> SessionStore:
        return session_store

    monkeypatch.setattr(th, "_get_db", _fake_get_db)
    monkeypatch.setattr(th, "_get_session_store", _fake_get_session_store)


async def _add_thread(
    store: SessionStore,
    db: aiosqlite.Connection,
    thread_id: str,
    *,
    user: str = "alice",
    messages: int = 1,
    activity: str = "2026-01-01T00:00:00+00:00",
    pinned: bool = False,
    mode: str = "code",
) -> None:
    await store.create_session(thread_id, user_id=user, mode=mode)
    await db.execute(
        "INSERT INTO vectora_sessions "
        "(thread_id, created_at, last_activity, message_count, extra, mode, pinned) "
        "VALUES (?, ?, ?, ?, '{}', ?, ?)",
        (thread_id, activity, activity, messages, mode, int(pinned)),
    )
    await db.commit()


async def _list_all(request: ListThreadsRequest, user: str = "alice") -> list[str]:
    """Percorre todas as páginas e devolve os IDs na ordem recebida."""
    seen: list[str] = []
    cursor = ""
    for _ in range(50):
        page = await th.list_threads(
            request.model_copy(update={"cursor": cursor}), _http_request(user)
        )
        seen.extend(t.id for t in page.threads)
        cursor = page.next_cursor
        if not cursor:
            return seen
    raise AssertionError("a paginação não terminou")


class TestEmptyThreads:
    async def test_create_thread_devolve_thread_vazia(self) -> None:
        created = await th.create_thread(CreateThreadRequest(), _http_request("alice"))

        assert created.empty is True

    async def test_thread_vazia_so_aparece_com_include_empty(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        await _add_thread(session_store, checkpoints_db, "vazia", messages=0)
        await _add_thread(session_store, checkpoints_db, "cheia", messages=2)

        padrao = await th.list_threads(ListThreadsRequest(), _http_request("alice"))
        todas = await th.list_threads(
            ListThreadsRequest(include_empty=True), _http_request("alice")
        )

        assert [t.id for t in padrao.threads] == ["cheia"]
        assert padrao.threads[0].empty is False
        assert {t.id: t.empty for t in todas.threads} == {"vazia": True, "cheia": False}

    async def test_thread_criada_aparece_com_include_empty_e_some_por_padrao(
        self,
    ) -> None:
        created = await th.create_thread(CreateThreadRequest(), _http_request("alice"))

        padrao = await th.list_threads(ListThreadsRequest(), _http_request("alice"))
        todas = await th.list_threads(
            ListThreadsRequest(include_empty=True), _http_request("alice")
        )

        assert [t.id for t in padrao.threads] == []
        assert [t.id for t in todas.threads] == [created.id]


class TestCountThreads:
    async def test_contagem_igual_ao_total_listado_em_cada_filtro(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        await _add_thread(session_store, checkpoints_db, "a", mode="chat")
        await _add_thread(session_store, checkpoints_db, "b", mode="code")
        await _add_thread(session_store, checkpoints_db, "c", mode="code")
        await _add_thread(session_store, checkpoints_db, "d", messages=0, mode="code")
        user = _http_request("alice")

        for mode in ("", "chat", "code"):
            for include_empty in (False, True):
                listed = await th.list_threads(
                    ListThreadsRequest(mode=mode, include_empty=include_empty), user
                )
                counted = await th.count_threads(
                    CountThreadsRequest(mode=mode, include_empty=include_empty), user
                )
                assert counted.count == listed.total == len(listed.threads)

    async def test_contagem_nao_inclui_thread_de_outro_usuario(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        await _add_thread(session_store, checkpoints_db, "minha", user="alice")
        await _add_thread(session_store, checkpoints_db, "dele", user="bob")

        alice = await th.count_threads(CountThreadsRequest(), _http_request("alice"))
        bob = await th.count_threads(CountThreadsRequest(), _http_request("bob"))

        assert (alice.count, bob.count) == (1, 1)


class TestListCursor:
    async def test_paginar_ate_o_fim_devolve_cada_thread_uma_vez(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        for index in range(7):
            await _add_thread(
                session_store,
                checkpoints_db,
                f"t{index}",
                activity=f"2026-01-0{index + 1}T00:00:00+00:00",
            )

        ids = await _list_all(ListThreadsRequest(limit=3))

        assert ids == ["t6", "t5", "t4", "t3", "t2", "t1", "t0"]

    async def test_total_e_next_cursor_da_primeira_e_da_ultima_pagina(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        for index in range(3):
            await _add_thread(
                session_store,
                checkpoints_db,
                f"t{index}",
                activity=f"2026-01-0{index + 1}T00:00:00+00:00",
            )
        user = _http_request("alice")

        first = await th.list_threads(ListThreadsRequest(limit=2), user)
        last = await th.list_threads(
            ListThreadsRequest(limit=2, cursor=first.next_cursor), user
        )

        assert (first.total, len(first.threads), bool(first.next_cursor)) == (
            3,
            2,
            True,
        )
        assert (last.total, len(last.threads), last.next_cursor) == (3, 1, "")

    async def test_atividade_igual_desempata_por_id_sem_repetir_nem_pular(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        for thread_id in ("c", "a", "d", "b"):
            await _add_thread(session_store, checkpoints_db, thread_id)

        ids = await _list_all(ListThreadsRequest(limit=1))

        assert ids == ["a", "b", "c", "d"]

    async def test_fixadas_vem_primeiro_e_o_cursor_atravessa_a_fronteira(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        await _add_thread(
            session_store,
            checkpoints_db,
            "velha-fixa",
            pinned=True,
            activity="2025-01-01T00:00:00+00:00",
        )
        await _add_thread(session_store, checkpoints_db, "nova")

        assert await _list_all(ListThreadsRequest(limit=1)) == ["velha-fixa", "nova"]

    async def test_thread_nova_entre_paginas_nao_repete_nem_pula_as_antigas(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        for index in range(4):
            await _add_thread(
                session_store,
                checkpoints_db,
                f"t{index}",
                activity=f"2026-01-0{index + 1}T00:00:00+00:00",
            )
        user = _http_request("alice")
        first = await th.list_threads(ListThreadsRequest(limit=2), user)

        await _add_thread(
            session_store, checkpoints_db, "recem", activity="2026-02-01T00:00:00+00:00"
        )
        second = await th.list_threads(
            ListThreadsRequest(limit=2, cursor=first.next_cursor), user
        )

        assert [t.id for t in first.threads] == ["t3", "t2"]
        assert [t.id for t in second.threads] == ["t1", "t0"]

    async def test_cursor_invalido_e_rejeitado_com_400(self) -> None:
        with pytest.raises(HTTPException) as error:
            await th.list_threads(
                ListThreadsRequest(cursor="isto-nao-e-um-cursor"),
                _http_request("alice"),
            )

        assert error.value.status_code == 400

    async def test_paginacao_nao_vaza_thread_de_outro_usuario(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        await _add_thread(session_store, checkpoints_db, "a1", user="alice")
        await _add_thread(session_store, checkpoints_db, "b1", user="bob")
        await _add_thread(session_store, checkpoints_db, "a2", user="alice")

        assert sorted(await _list_all(ListThreadsRequest(limit=1))) == ["a1", "a2"]
