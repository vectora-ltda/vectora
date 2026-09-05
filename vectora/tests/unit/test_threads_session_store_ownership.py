"""Consolidação da fonte de verdade sobre existência/posse de threads.

`vectora_sessions` (SQLite em `checkpoints.db`, gerenciada por
`backend/api/handlers/threads.py`) e `SessionStore.sessions` (motor nativo,
`backend/persistence/native/session_store.py`) coexistem — a primeira guarda
metadados de UI (título, contagem de mensagens, fixação), a segunda é a
única fonte de verdade sobre EXISTÊNCIA e DONO (`user_id`) de uma thread.

Cobre: `CreateThread`/`_upsert_session` registram a posse em `SessionStore`;
`ListThreads`/`GetThread`/`UpdateThread`/`DeleteThread`/`GenerateTitle` nunca
vazam uma thread registrada em `SessionStore` sob outro usuário.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import aiosqlite
import pytest

from backend.api.handlers import threads as th
from backend.api.schemas import (
    DeleteThreadRequest,
    GetThreadRequest,
    ListThreadsRequest,
    UpdateThreadRequest,
)
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool


def _http_request(user_id: str | None) -> MagicMock:
    request = MagicMock()
    if user_id is None:
        request.state = MagicMock(user=None)
    else:
        user = MagicMock()
        user.id = user_id
        request.state = MagicMock(user=user)
    return request


@pytest.fixture
async def checkpoints_db():
    db = await aiosqlite.connect(":memory:")
    await th._ensure_schema(db)
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def session_store(tmp_path):
    pool = AsyncConnectionPool(str(tmp_path / "sessions.db"), min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    try:
        yield store
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _wire_stores(monkeypatch, checkpoints_db, session_store):
    """Redireciona `_get_db()`/`_get_session_store()` de `threads.py` pros
    bancos de teste isolados, sem tocar nos singletons globais."""

    async def _fake_get_db():
        return checkpoints_db

    async def _fake_get_session_store():
        return session_store

    monkeypatch.setattr(th, "_get_db", _fake_get_db)
    monkeypatch.setattr(th, "_get_session_store", _fake_get_session_store)


class TestListThreadsReflectsSessionStore:
    """Threads registradas em `SessionStore.create_session` (motor nativo)
    aparecem em ListThreads pro dono, e nunca pro usuário errado."""

    async def test_thread_criada_via_session_store_aparece_para_o_dono(
        self, session_store, checkpoints_db
    ):
        await session_store.create_session("thread-alice", user_id="alice", mode="code")
        await th._upsert_session("thread-alice", title="Conversa da Alice")
        await th._increment_message_count("thread-alice")

        result = await th.list_threads(
            ListThreadsRequest(limit=50), _http_request("alice")
        )

        assert [t.id for t in result.threads] == ["thread-alice"]
        assert result.threads[0].title == "Conversa da Alice"

    async def test_thread_de_outro_usuario_nao_vaza_na_listagem(self, session_store):
        """Erro/borda: uma thread registrada em SessionStore sob `bob` nunca
        aparece na listagem de `alice`, mesmo estando em `vectora_sessions`
        (cache de UI compartilhado)."""
        await session_store.create_session("thread-bob", user_id="bob", mode="code")
        await th._upsert_session("thread-bob", title="Conversa do Bob")
        await th._increment_message_count("thread-bob")

        result = await th.list_threads(
            ListThreadsRequest(limit=50), _http_request("alice")
        )

        assert result.threads == []

    async def test_thread_legada_sem_registro_no_session_store_ainda_aparece(self):
        """Compatibilidade: threads criadas antes da posse ser rastreada em
        `SessionStore` (nenhum registro lá) continuam visíveis — ausência de
        registro não é o mesmo que pertencer a outra pessoa."""
        await th._upsert_session("thread-legada", title="Conversa antiga")
        await th._increment_message_count("thread-legada")

        result = await th.list_threads(
            ListThreadsRequest(limit=50), _http_request("alice")
        )

        assert [t.id for t in result.threads] == ["thread-legada"]

    async def test_subagent_interno_nao_aparece_na_listagem(
        self, session_store: SessionStore
    ) -> None:
        """Sessões internas podem existir para histórico e auditoria, mas
        nunca viram conversas selecionáveis pelo usuário."""
        await session_store.create_session(
            "thread-pai:search:interno", user_id="alice", mode="subagent"
        )
        await th._upsert_session(
            "thread-pai:search:interno", title="search", mode="subagent"
        )
        await th._increment_message_count("thread-pai:search:interno")

        result = await th.list_threads(
            ListThreadsRequest(limit=50), _http_request("alice")
        )

        assert result.threads == []


class TestOwnershipEnforcement:
    """GetThread/UpdateThread/DeleteThread/GenerateTitle não vazam threads
    de outro usuário nem revelam se a thread não existe vs. pertence a
    outra pessoa — 404 nos dois casos."""

    async def test_get_thread_de_outro_usuario_404(self, session_store):
        await session_store.create_session("thread-bob", user_id="bob")
        await th._upsert_session("thread-bob")

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await th.get_thread(
                GetThreadRequest(thread_id="thread-bob"), _http_request("alice")
            )
        assert exc_info.value.status_code == 404

    async def test_get_thread_do_proprio_dono_retorna_200(self, session_store):
        await session_store.create_session("thread-alice", user_id="alice")
        await th._upsert_session("thread-alice", title="Minha thread")

        thread = await th.get_thread(
            GetThreadRequest(thread_id="thread-alice"), _http_request("alice")
        )

        assert thread.id == "thread-alice"
        assert thread.title == "Minha thread"

    async def test_get_thread_sem_http_request_pula_checagem(self, session_store):
        """Chamadores internos (GetHistory, GenerateTitle, histórico
        paginado) invocam `get_thread` sem `http_request` — comportamento
        preexistente preservado."""
        await session_store.create_session("thread-bob", user_id="bob")
        await th._upsert_session("thread-bob")

        thread = await th.get_thread(GetThreadRequest(thread_id="thread-bob"))

        assert thread.id == "thread-bob"

    async def test_update_thread_de_outro_usuario_404_e_nao_persiste(
        self, session_store, checkpoints_db
    ):
        await session_store.create_session("thread-bob", user_id="bob")
        await th._upsert_session("thread-bob", title="Original")

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await th.update_thread(
                UpdateThreadRequest(thread_id="thread-bob", title="Sequestrado"),
                _http_request("alice"),
            )
        assert exc_info.value.status_code == 404

        thread = await th.get_thread(GetThreadRequest(thread_id="thread-bob"))
        assert thread.title == "Original"

    async def test_delete_thread_de_outro_usuario_404_e_nao_apaga(self, session_store):
        await session_store.create_session("thread-bob", user_id="bob")
        await th._upsert_session("thread-bob")

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await th.delete_thread(
                DeleteThreadRequest(thread_id="thread-bob"), _http_request("alice")
            )
        assert exc_info.value.status_code == 404

        thread = await th.get_thread(GetThreadRequest(thread_id="thread-bob"))
        assert thread.id == "thread-bob"

    async def test_delete_thread_do_proprio_dono_remove(self, session_store):
        await session_store.create_session("thread-alice", user_id="alice")
        await th._upsert_session("thread-alice")

        await th.delete_thread(
            DeleteThreadRequest(thread_id="thread-alice"), _http_request("alice")
        )

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await th.get_thread(GetThreadRequest(thread_id="thread-alice"))
        assert exc_info.value.status_code == 404

    async def test_delete_thread_apaga_tambem_do_session_store(
        self, session_store: SessionStore
    ) -> None:
        """Regressão real (2026-09-04): `delete_thread` só apagava de
        `vectora_sessions`, nunca de `SessionStore` — a próxima rodada de
        `reconcile_vectora_sessions` (boot ou hora em hora) encontrava a
        thread ainda viva em `sessions.db` e a recriava na sidebar, fazendo
        uma conversa apagada pelo usuário reaparecer sozinha."""
        from backend.vtypes.message import MessageRole, text_message

        await session_store.create_session("thread-alice", user_id="alice")
        await session_store.append_message(
            "thread-alice", text_message(MessageRole.USER, "oi")
        )
        await th._upsert_session("thread-alice")
        await th._increment_message_count("thread-alice")

        await th.delete_thread(
            DeleteThreadRequest(thread_id="thread-alice"), _http_request("alice")
        )

        assert await session_store.get_session("thread-alice") is None

        reconciled = await th.reconcile_vectora_sessions()

        assert reconciled == 0

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await th.get_thread(GetThreadRequest(thread_id="thread-alice"))
        assert exc_info.value.status_code == 404


class TestUpsertSessionRegistersOwnership:
    """`_upsert_session(..., user_id=...)` garante (idempotente) o registro
    de posse em `SessionStore` antes de gravar metadados de UI — fecha o elo
    entre as duas tabelas pra callers (ex.: `background_tasks.py`) que ainda
    não têm a posse registrada quando chamam `_upsert_session`."""

    async def test_upsert_com_user_id_cria_sessao_no_session_store(self, session_store):
        assert await session_store.get_session("thread-nova") is None

        await th._upsert_session(
            "thread-nova", workspace_id="ws-1", mode="code", user_id="alice"
        )

        session = await session_store.get_session("thread-nova")
        assert session is not None
        assert session["user_id"] == "alice"

    async def test_upsert_sem_user_id_nao_mexe_no_session_store(self, session_store):
        """Par de erro: chamadores que já registraram a posse por conta
        própria (ex.: `stream_chat`, via `session_store.create_session`
        direto) não devem disparar uma segunda escrita ao omitir `user_id`."""
        await th._upsert_session("thread-sem-user")

        assert await session_store.get_session("thread-sem-user") is None

    async def test_upsert_idempotente_nao_sobrescreve_dono_existente(
        self, session_store
    ):
        await session_store.create_session("thread-1", user_id="alice")

        await th._upsert_session("thread-1", user_id="bob")

        session = await session_store.get_session("thread-1")
        assert session is not None
        assert session["user_id"] == "alice"


class TestCreateThreadRegistersOwnership:
    async def test_create_thread_registra_posse_no_session_store(self, session_store):
        from backend.api.schemas import CreateThreadRequest

        thread = await th.create_thread(
            CreateThreadRequest(workspace_id=""), _http_request("alice")
        )

        session = await session_store.get_session(thread.id)
        assert session is not None
        assert session["user_id"] == "alice"

    async def test_create_thread_usa_mode_explicito_quando_informado(
        self, session_store
    ):
        """Regressão: sem `mode` explícito no request, o endpoint gravava
        "code" fixo mesmo pra uma conversa criada em modo Chat — o campo
        precisa ser respeitado quando o caller informa."""
        from backend.api.schemas import CreateThreadRequest

        thread = await th.create_thread(
            CreateThreadRequest(workspace_id="", mode="chat"), _http_request("alice")
        )

        session = await session_store.get_session(thread.id)
        assert session is not None
        assert session["mode"] == "chat"

    async def test_create_thread_sem_mode_mantem_default_code(self, session_store):
        """Erro/borda: `mode` ausente/vazio preserva o comportamento
        histórico do endpoint (default "code"), sem quebrar callers
        existentes que nunca informaram o campo."""
        from backend.api.schemas import CreateThreadRequest

        thread = await th.create_thread(
            CreateThreadRequest(workspace_id=""), _http_request("alice")
        )

        session = await session_store.get_session(thread.id)
        assert session is not None
        assert session["mode"] == "code"
