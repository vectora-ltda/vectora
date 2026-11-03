"""Persistência de threads na tabela `vectora_sessions`.

Cobre: `_upsert_session()` grava/atualiza a tabela; `stream_chat()` chama
`_upsert_session()` com o thread_id correto; o endpoint UpdateThread
persiste o title no campo extra JSON; threads registradas via
`SessionStore` e `_upsert_session()` aparecem em ListThreads.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _native_dispatch_patches() -> list[Any]:
    """Patches pro motor nativo — mesmo objetivo do helper equivalente em
    `tests/unit/test_file_attachments.py`. `chat.py` faz `from
    backend.engine.conversation_loop import run_conversation` (nome direto,
    não `conversation_loop.run_conversation` dinâmico) — por isso o patch
    tem que mirar `backend.api.handlers.chat.run_conversation` (onde o nome
    foi importado), não a origem. Um `importlib.reload(chat_mod)` fazia
    esse papel antes, reexecutando o import pra rebindar o nome local —
    mas reload() nunca é seguro num teste: reexecuta o corpo inteiro do
    módulo (decorators de rota, imports de terceiros como `lancedb` que
    inicializam threads de background) toda vez, acumulando ao longo da
    suíte inteira. Patchar o destino certo direto elimina a necessidade."""
    from backend.engine.conversation_loop import LoopResult
    from backend.services.agent_factory import NativeAgent
    from backend.tools.registry import ToolRegistry

    fake_agent = NativeAgent(
        tool_registry=ToolRegistry(), subagent_catalog={}, system_prompt="prompt"
    )
    session_store = AsyncMock()
    session_store.create_session = AsyncMock()
    session_store.get_branch_head_id = AsyncMock(return_value=1)
    session_store.append_message = AsyncMock(return_value=2)
    session_store.set_branch_head = AsyncMock()

    return [
        patch(
            "backend.services.agent_factory.get_native_agent",
            new=AsyncMock(return_value=fake_agent),
        ),
        patch(
            "backend.services.agent_factory.get_session_store",
            new=AsyncMock(return_value=session_store),
        ),
        patch(
            "backend.services.agent_factory.get_approval_gate",
            new=AsyncMock(return_value=AsyncMock()),
        ),
        patch(
            "backend.services.agent_factory.get_store",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "backend.api.handlers.chat.run_conversation",
            new=AsyncMock(return_value=LoopResult(stopped_reason="stop")),
        ),
    ]


def _collect_route_paths(routes: list[Any]) -> list[str]:
    paths: list[str] = []
    for r in routes:
        if hasattr(r, "path") and r.path is not None:
            paths.append(r.path)
        orig = getattr(r, "original_router", None)
        if orig is not None and hasattr(orig, "routes"):
            paths.extend(_collect_route_paths(orig.routes))
        elif hasattr(r, "routes") and r.routes:
            paths.extend(_collect_route_paths(r.routes))
    return paths


os.environ["VECTORA_AUTH_REQUIRED"] = "false"

# ---------------------------------------------------------------------------
# Fake DB para testes unitários
# ---------------------------------------------------------------------------


class _CursorProxy:
    """Suporta tanto `await db.execute(...)` quanto `async with db.execute(...) as cur`.

    Espelha o comportamento do aiosqlite._AioContextManager.
    """

    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __await__(self):  # type: ignore[override]
        async def _inner() -> FakeCursor:
            return self._cursor

        return _inner().__await__()

    async def __aenter__(self) -> FakeCursor:
        return self._cursor

    async def __aexit__(self, *_: object) -> None:
        pass


class FakeDB:
    """Banco em memória que simula aiosqlite.Connection.

    `execute()` é síncrono (como aiosqlite requer internamente) e retorna
    _CursorProxy que suporta `await` e `async with`.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.sql_calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()) -> _CursorProxy:  # sync!
        self.sql_calls.append((sql, params))
        sql_norm = sql.upper().replace("\n", " ").strip()

        if "INSERT OR REPLACE" in sql_norm or (
            "INSERT" in sql_norm and "ON CONFLICT" in sql_norm
        ):
            thread_id = params[0]
            created_at = params[1] if len(params) > 1 else "2025-01-01T00:00:00Z"
            last_activity = params[2] if len(params) > 2 else created_at
            extra = params[3] if len(params) > 3 else "{}"
            if thread_id in self.rows:
                self.rows[thread_id]["last_activity"] = last_activity
                try:
                    extra_obj = json.loads(extra)
                    existing = json.loads(self.rows[thread_id].get("extra", "{}"))
                    existing.update(extra_obj)
                    self.rows[thread_id]["extra"] = json.dumps(existing)
                except Exception:
                    self.rows[thread_id]["extra"] = extra
            else:
                self.rows[thread_id] = {
                    "thread_id": thread_id,
                    "created_at": created_at,
                    "last_activity": last_activity,
                    "message_count": 0,
                    "extra": extra,
                }
            cursor = FakeCursor(self, thread_id)

        elif "UPDATE" in sql_norm and "vectora_sessions" in sql_norm.lower():
            thread_id = params[-1] if params else None
            if thread_id and thread_id in self.rows:
                extra_json = params[0]
                self.rows[thread_id]["extra"] = extra_json
            cursor = FakeCursor(self, thread_id)

        else:
            # SELECT — lookup pelo primeiro param
            lookup_id = params[0] if params else None
            cursor = FakeCursor(self, lookup_id)

        return _CursorProxy(cursor)

    async def commit(self) -> None:
        pass


class FakeCursor:
    def __init__(self, db: FakeDB, lookup_id: str | None = None) -> None:
        self._db = db
        self._lookup_id = lookup_id

    async def fetchone(self) -> tuple | None:
        if self._lookup_id and self._lookup_id in self._db.rows:
            return _row_tuple(self._db.rows[self._lookup_id])
        return None

    async def fetchall(self) -> list[tuple]:
        return [_row_tuple(r) for r in self._db.rows.values()]


def _row_tuple(r: dict) -> tuple:
    return (
        r["thread_id"],
        "human",
        r["created_at"],
        r["last_activity"],
        r.get("message_count", 0),
        r.get("extra", "{}"),
    )


# ---------------------------------------------------------------------------
# 0. _get_db — singleton de conexão, sem mock (banco isolado em tmp_path)
# ---------------------------------------------------------------------------


class TestGetDbConcurrency:
    """``_get_db()`` é um singleton de módulo com init lazy — várias
    coroutines chamando antes da primeira ``await aiosqlite.connect()``
    terminar devem convergir pra UMA única conexão, não abrir uma por
    chamada (o check-then-act sem lock deixava a segunda conexão sem os
    PRAGMAs de WAL/busy_timeout aplicados a tempo, gerando "database is
    locked" em produção sob concorrência real)."""

    @pytest.mark.asyncio
    async def test_concurrent_calls_share_single_connection(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        import asyncio

        import backend.api.handlers.threads as t_mod
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        original_db_conn = t_mod._db_conn
        t_mod._db_conn = None
        try:
            connections = await asyncio.gather(*(t_mod._get_db() for _ in range(20)))
            assert len({id(c) for c in connections}) == 1
        finally:
            if t_mod._db_conn is not None:
                await t_mod._db_conn.close()
            t_mod._db_conn = original_db_conn


class TestUpsertSession:
    """A função _upsert_session deve existir em threads.py e fazer UPSERT correto."""

    @pytest.mark.asyncio
    async def test_upsert_session_exists(self):
        """_upsert_session deve ser importável de vectora.api.handlers.threads."""
        from backend.api.handlers.threads import _upsert_session

    @pytest.mark.asyncio
    async def test_upsert_creates_row(self):
        """_upsert_session escreve INSERT ... ON CONFLICT no banco."""
        from backend.api.handlers.threads import _upsert_session

        db = FakeDB()
        with patch(
            "backend.api.handlers.threads._get_db", new=AsyncMock(return_value=db)
        ):
            await _upsert_session("thread-abc")

        assert len(db.sql_calls) >= 1
        sql_used = " ".join(s for s, _ in db.sql_calls).upper()
        assert "INSERT" in sql_used
        assert "ON CONFLICT" in sql_used or "OR REPLACE" in sql_used

    @pytest.mark.asyncio
    async def test_upsert_stores_thread_id(self):
        """O thread_id passado aparece como parâmetro SQL e é armazenado."""
        from backend.api.handlers.threads import _upsert_session

        db = FakeDB()
        with patch(
            "backend.api.handlers.threads._get_db", new=AsyncMock(return_value=db)
        ):
            await _upsert_session("thread-xyz")

        all_params = [p for _, p in db.sql_calls]
        flat = [item for sublist in all_params for item in sublist]
        assert "thread-xyz" in flat

    @pytest.mark.asyncio
    async def test_upsert_with_title_stores_in_extra(self):
        """Quando title é fornecido, deve aparecer no JSON do campo extra."""
        from backend.api.handlers.threads import _upsert_session

        db = FakeDB()
        with patch(
            "backend.api.handlers.threads._get_db", new=AsyncMock(return_value=db)
        ):
            await _upsert_session("thread-titled", title="Minha Conversa")

        all_params_str = str(db.sql_calls)
        assert "Minha Conversa" in all_params_str

    @pytest.mark.asyncio
    async def test_upsert_idempotent(self):
        """Chamar duas vezes com o mesmo thread_id não lança exceção."""
        from backend.api.handlers.threads import _upsert_session

        db = FakeDB()
        with patch(
            "backend.api.handlers.threads._get_db", new=AsyncMock(return_value=db)
        ):
            await _upsert_session("thread-dup")
            await _upsert_session("thread-dup")  # segunda chamada não deve falhar

    @pytest.mark.asyncio
    async def test_upsert_calls_commit(self):
        """_upsert_session deve chamar commit() após executar."""
        from backend.api.handlers.threads import _upsert_session

        db = MagicMock()
        db.execute = MagicMock(return_value=_CursorProxy(FakeCursor(FakeDB())))
        db.commit = AsyncMock()

        with patch(
            "backend.api.handlers.threads._get_db", new=AsyncMock(return_value=db)
        ):
            await _upsert_session("thread-commit-test")

        db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# 2. stream_chat chama _upsert_session
# ---------------------------------------------------------------------------


class TestStreamChatRegistersThread:
    """stream_chat() deve registrar a thread em vectora_sessions via _upsert_session."""

    @pytest.mark.asyncio
    async def test_stream_chat_calls_upsert_with_thread_id(self):
        """stream_chat chama _upsert_session com o thread_id do request."""
        upsert_calls: list[str] = []

        async def mock_upsert(
            thread_id: str,
            title: str = "",
            workspace_id: str | None = None,
            mode: str | None = None,
        ) -> None:
            upsert_calls.append(thread_id)

        from backend.api.schemas import StreamChatRequest

        mock_ws = MagicMock()
        mock_ws.id = "test-ws-explicit"
        mock_registry = MagicMock()
        mock_registry.get.return_value = None
        mock_registry.get_active.return_value = None
        mock_registry.get_or_create_session_workspace.return_value = mock_ws

        with ExitStack() as stack:
            for p in _native_dispatch_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._upsert_session",
                    side_effect=mock_upsert,
                )
            )
            stack.enter_context(
                patch(
                    "backend.workspace.workspace.workspace_registry",
                    mock_registry,
                )
            )
            import backend.api.handlers.chat as chat_mod

            request = StreamChatRequest(content="Olá", thread_id="explicit-thread-id")
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            _response = await chat_mod.stream_chat(request, http_request)

        assert "explicit-thread-id" in upsert_calls, (
            "stream_chat deve chamar _upsert_session com o thread_id fornecido"
        )

    @pytest.mark.asyncio
    async def test_stream_chat_upserts_when_thread_id_empty(self):
        """Quando thread_id é vazio, stream_chat gera UUID e chama _upsert_session."""
        upsert_calls: list[str] = []

        async def mock_upsert(
            thread_id: str,
            title: str = "",
            workspace_id: str | None = None,
            mode: str | None = None,
        ) -> None:
            upsert_calls.append(thread_id)

        from backend.api.schemas import StreamChatRequest

        mock_ws2 = MagicMock()
        mock_ws2.id = "test-ws-empty"
        mock_registry2 = MagicMock()
        mock_registry2.get.return_value = None
        mock_registry2.get_active.return_value = None
        mock_registry2.get_or_create_session_workspace.return_value = mock_ws2

        with ExitStack() as stack:
            for p in _native_dispatch_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._upsert_session",
                    side_effect=mock_upsert,
                )
            )
            stack.enter_context(
                patch(
                    "backend.workspace.workspace.workspace_registry",
                    mock_registry2,
                )
            )
            import backend.api.handlers.chat as chat_mod

            request = StreamChatRequest(content="Sem thread id")
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            _response = await chat_mod.stream_chat(request, http_request)

        assert len(upsert_calls) == 1, "Deve ter sido chamado exatamente uma vez"
        thread_id_used = upsert_calls[0]
        assert len(thread_id_used) > 0, "thread_id gerado não deve ser vazio"

    @pytest.mark.asyncio
    async def test_stream_chat_loga_erro_alto_quando_upsert_session_falha(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Regressão do achado real de 2026-08-30: uma falha em
        _upsert_session (a ÚNICA escrita que faz a thread aparecer em
        ListThreads) era engolida com logger.warning sem stack trace —
        indistinguível de um log qualquer no meio de um burst de erros. O
        chat continua funcionando (nunca deve propagar pro usuário), mas o
        log precisa ser ERROR + exc_info, com o thread_id explícito, pra
        aparecer alto o bastante no terminal e ser encontrável depois."""
        from backend.api.schemas import StreamChatRequest

        mock_ws = MagicMock()
        mock_ws.id = "test-ws-upsert-fails"
        mock_registry = MagicMock()
        mock_registry.get.return_value = None
        mock_registry.get_active.return_value = None
        mock_registry.get_or_create_session_workspace.return_value = mock_ws

        with (
            ExitStack() as stack,
            caplog.at_level(logging.ERROR, logger="backend.api.handlers.chat"),
        ):
            for p in _native_dispatch_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._upsert_session",
                    side_effect=RuntimeError("disco cheio"),
                )
            )
            stack.enter_context(
                patch(
                    "backend.workspace.workspace.workspace_registry",
                    mock_registry,
                )
            )
            import backend.api.handlers.chat as chat_mod

            request = StreamChatRequest(
                content="Olá", thread_id="thread-com-falha-upsert"
            )
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            # Não deve propagar — o chat funciona mesmo com o registro
            # de UI falhando.
            _response = await chat_mod.stream_chat(request, http_request)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records, (
            "falha em _upsert_session deve gerar pelo menos 1 log ERROR"
        )
        assert any(
            "thread-com-falha-upsert" in r.getMessage() for r in error_records
        ), "o log de erro deve identificar qual thread ficou invisível"
        assert any(r.exc_info for r in error_records), (
            "o log deve incluir stack trace (exc_info) — WARNING sem exc_info "
            "some no meio de qualquer burst de erros"
        )

    @pytest.mark.asyncio
    async def test_stream_chat_does_not_upsert_when_agent_init_fails(self):
        """Erro/borda: quando `get_native_agent` falha (ex. sem provider
        configurado), nem `_upsert_session` nem `_increment_message_count`
        podem ter rodado — persistir a thread nesse ponto criaria uma sessão
        com message_count > 0 sem mensagem real, invisível tanto ao filtro
        de `list_threads` quanto a `cleanup_empty_threads`."""
        upsert_calls: list[str] = []

        async def mock_upsert(
            thread_id: str,
            title: str = "",
            workspace_id: str | None = None,
            mode: str | None = None,
        ) -> None:
            upsert_calls.append(thread_id)

        increment_calls: list[str] = []

        async def mock_increment(thread_id: str) -> None:
            increment_calls.append(thread_id)

        from backend.api.schemas import StreamChatRequest

        mock_ws3 = MagicMock()
        mock_ws3.id = "test-ws-fail"
        mock_registry3 = MagicMock()
        mock_registry3.get.return_value = None
        mock_registry3.get_active.return_value = None
        mock_registry3.get_or_create_session_workspace.return_value = mock_ws3

        with (
            patch(
                "backend.services.agent_factory.get_native_agent",
                new=AsyncMock(side_effect=RuntimeError("sem provider configurado")),
            ),
            patch(
                "backend.api.handlers.threads._upsert_session",
                side_effect=mock_upsert,
            ),
            patch(
                "backend.api.handlers.threads._increment_message_count",
                side_effect=mock_increment,
            ),
            patch(
                "backend.workspace.workspace.workspace_registry",
                mock_registry3,
            ),
        ):
            from fastapi import HTTPException

            import backend.api.handlers.chat as chat_mod

            request = StreamChatRequest(content="oi", thread_id="failed-init-thread")
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            with pytest.raises(HTTPException):
                await chat_mod.stream_chat(request, http_request)

        assert upsert_calls == [], (
            "_upsert_session não pode rodar se o grafo falhou ao inicializar"
        )
        assert increment_calls == [], (
            "_increment_message_count não pode rodar se o grafo falhou ao inicializar"
        )

    @pytest.mark.asyncio
    async def test_stream_chat_counts_user_message_even_without_assistant_reply(
        self,
    ):
        """message_count incrementa assim que a mensagem do usuário é enviada
        ao grafo, mesmo que o assistente nunca produza nenhum token (erro de
        auth/quota/timeout do provider, `astream_events` sem nenhum evento) —
        quem inicia a conversa é o usuário, então a thread já conta como
        real nesse ponto."""
        increment_calls: list[str] = []

        async def mock_increment(thread_id: str) -> None:
            increment_calls.append(thread_id)

        from backend.api.schemas import StreamChatRequest

        mock_ws4 = MagicMock()
        mock_ws4.id = "test-ws-no-reply"
        mock_registry4 = MagicMock()
        mock_registry4.get.return_value = None
        mock_registry4.get_active.return_value = None
        mock_registry4.get_or_create_session_workspace.return_value = mock_ws4

        with ExitStack() as stack:
            for p in _native_dispatch_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._upsert_session",
                    new=AsyncMock(),
                )
            )
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._increment_message_count",
                    side_effect=mock_increment,
                )
            )
            stack.enter_context(
                patch(
                    "backend.workspace.workspace.workspace_registry",
                    mock_registry4,
                )
            )
            import backend.api.handlers.chat as chat_mod

            request = StreamChatRequest(
                content="oi, ninguém vai responder", thread_id="no-reply-thread"
            )
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            _response = await chat_mod.stream_chat(request, http_request)

        assert increment_calls == ["no-reply-thread"], (
            "message_count deve ser incrementado pela mensagem do usuário, "
            "sem esperar nenhuma resposta do assistente"
        )


# ---------------------------------------------------------------------------
# 3. UpdateThread endpoint
# ---------------------------------------------------------------------------


def _make_app_with_db(tmp_db: Any) -> tuple:
    """Cria app FastAPI com um banco (FakeDB ou aiosqlite real) injetado em
    threads._get_db."""
    import backend.api.handlers.threads as t_mod

    original_get_db = t_mod._get_db
    original_db_conn = t_mod._db_conn
    t_mod._db_conn = None

    async def patched_get_db():
        return tmp_db

    t_mod._get_db = patched_get_db  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    from backend.api.server import create_app

    app = create_app(serve_static=False)
    return app, original_get_db, original_db_conn, t_mod


def _restore_app(t_mod, original_get_db, original_db_conn) -> None:
    t_mod._get_db = original_get_db
    t_mod._db_conn = original_db_conn


class TestUpdateThreadEndpoint:
    """Endpoint UpdateThread deve existir e persistir o title no campo extra."""

    def test_update_thread_route_exists(self):
        """Rota UpdateThread deve estar registrada no app."""
        from fastapi.testclient import TestClient

        db = FakeDB()
        app, orig_get_db, orig_conn, t_mod = _make_app_with_db(db)
        try:
            routes = _collect_route_paths(app.routes)
            assert "/vectora.chat.v1.ThreadService/UpdateThread" in routes, (
                f"Rota UpdateThread não encontrada. Rotas: {routes}"
            )
        finally:
            _restore_app(t_mod, orig_get_db, orig_conn)

    def test_update_thread_saves_title(self):
        """POST /UpdateThread deve salvar o title no campo extra da thread."""
        from fastapi.testclient import TestClient

        db = FakeDB()
        # Pré-popula uma thread via execute síncrono
        db.execute(
            "INSERT INTO vectora_sessions (thread_id, created_at, last_activity, message_count, extra) "
            "VALUES (?, ?, ?, 0, ?) ON CONFLICT(thread_id) DO UPDATE SET last_activity = excluded.last_activity",
            ("thread-upd", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z", "{}"),
        )

        app, orig_get_db, orig_conn, t_mod = _make_app_with_db(db)
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/vectora.chat.v1.ThreadService/UpdateThread",
                json={"thread_id": "thread-upd", "title": "Título Persistido"},
            )
            assert response.status_code == 200, (
                f"Esperado 200, obtido {response.status_code}: {response.text}"
            )
            # O title deve ter sido atualizado no DB
            if "thread-upd" in db.rows:
                extra = json.loads(db.rows["thread-upd"].get("extra", "{}"))
                assert extra.get("title") == "Título Persistido"
        finally:
            _restore_app(t_mod, orig_get_db, orig_conn)

    def test_update_thread_returns_updated_thread(self):
        """UpdateThread deve retornar a Thread com o title atualizado."""
        from fastapi.testclient import TestClient

        db = FakeDB()
        db.execute(
            "INSERT INTO vectora_sessions (thread_id, created_at, last_activity, message_count, extra) "
            "VALUES (?, ?, ?, 0, ?) ON CONFLICT(thread_id) DO UPDATE SET last_activity = excluded.last_activity",
            ("thread-ret", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z", "{}"),
        )

        app, orig_get_db, orig_conn, t_mod = _make_app_with_db(db)
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/vectora.chat.v1.ThreadService/UpdateThread",
                json={"thread_id": "thread-ret", "title": "Novo Título"},
            )
            if response.status_code == 200:
                body = response.json()
                assert body.get("id") == "thread-ret"
                assert body.get("title") == "Novo Título"
        finally:
            _restore_app(t_mod, orig_get_db, orig_conn)

    def test_update_thread_not_found(self):
        """UpdateThread com thread inexistente deve retornar 404."""
        from fastapi.testclient import TestClient

        db = FakeDB()
        app, orig_get_db, orig_conn, t_mod = _make_app_with_db(db)
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/vectora.chat.v1.ThreadService/UpdateThread",
                json={"thread_id": "nao-existe-xyz", "title": "Qualquer"},
            )
            assert response.status_code == 404, (
                f"Esperado 404 para thread inexistente, obtido {response.status_code}"
            )
        finally:
            _restore_app(t_mod, orig_get_db, orig_conn)


# ---------------------------------------------------------------------------
# 4. ListThreads retorna threads criadas via _upsert_session
# ---------------------------------------------------------------------------


class TestListThreadsIncludesUpserted:
    """Threads com posse nativa e metadados de UI aparecem em ListThreads."""

    @pytest.fixture
    async def session_store(self, monkeypatch, tmp_path):
        """Provide isolated native ownership for the HTTP listing tests."""
        from backend.persistence.native.session_store import SessionStore
        from backend.storage.sqlite.pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            str(tmp_path / "sessions.db"), min_size=1, max_size=2
        )
        await pool.open()
        store = SessionStore(pool)
        await store.setup()
        monkeypatch.setattr(
            "backend.api.handlers.threads._get_session_store",
            AsyncMock(return_value=store),
        )
        try:
            yield store
        finally:
            await pool.close()

    def test_list_threads_empty_initially(self):
        """Sem threads criadas, ListThreads retorna lista vazia."""
        from fastapi.testclient import TestClient

        db = FakeDB()
        app, orig_get_db, orig_conn, t_mod = _make_app_with_db(db)
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/vectora.chat.v1.ThreadService/ListThreads",
                json={"limit": 50},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["threads"] == []
        finally:
            _restore_app(t_mod, orig_get_db, orig_conn)

    @pytest.mark.asyncio
    async def test_upserted_thread_appears_in_list(self, session_store):
        """Thread com uma troca de mensagem real (`_upsert_session` seguido
        de `_increment_message_count`) aparece em ListThreads. Usa aiosqlite
        real (não FakeDB): FakeDB não aplica o filtro
        `WHERE message_count > 0` de verdade, o que mascararia esse
        comportamento."""
        import aiosqlite
        from fastapi.testclient import TestClient

        from backend.api.handlers.threads import (
            _ensure_schema,
            _increment_message_count,
            _upsert_session,
        )

        db = await aiosqlite.connect(":memory:")
        await _ensure_schema(db)
        app, orig_get_db, orig_conn, t_mod = _make_app_with_db(db)
        try:
            await session_store.create_session("streamed-thread-001", user_id="local")
            await _upsert_session("streamed-thread-001")
            await _increment_message_count("streamed-thread-001")

            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/vectora.chat.v1.ThreadService/ListThreads",
                json={"limit": 50},
            )
            assert response.status_code == 200
            body = response.json()
            ids = [t["id"] for t in body["threads"]]
            assert "streamed-thread-001" in ids, (
                f"Thread com mensagem real não aparece em ListThreads. ids={ids}"
            )
        finally:
            _restore_app(t_mod, orig_get_db, orig_conn)
            await db.close()

    @pytest.mark.asyncio
    async def test_upserted_thread_without_message_omitted_from_list(
        self, session_store
    ):
        """Par de erro do teste acima — _upsert_session sozinho (sem
        _increment_message_count) não faz a thread aparecer em ListThreads.
        A sessão nativa existe para que a ausência dependa só de
        `message_count`, não do filtro de posse."""
        import aiosqlite
        from fastapi.testclient import TestClient

        from backend.api.handlers.threads import _ensure_schema, _upsert_session

        db = await aiosqlite.connect(":memory:")
        await _ensure_schema(db)
        app, orig_get_db, orig_conn, t_mod = _make_app_with_db(db)
        try:
            await session_store.create_session("streamed-thread-empty", user_id="local")
            await _upsert_session("streamed-thread-empty")

            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/vectora.chat.v1.ThreadService/ListThreads",
                json={"limit": 50},
            )
            assert response.status_code == 200
            ids = [t["id"] for t in response.json()["threads"]]
            assert "streamed-thread-empty" not in ids
        finally:
            _restore_app(t_mod, orig_get_db, orig_conn)
            await db.close()

    @pytest.mark.asyncio
    async def test_upserted_thread_with_title_in_list(self, session_store):
        """Thread com title upsertada e uma mensagem real aparece com o
        title correto em ListThreads."""
        import aiosqlite
        from fastapi.testclient import TestClient

        from backend.api.handlers.threads import (
            _ensure_schema,
            _increment_message_count,
            _upsert_session,
        )

        db = await aiosqlite.connect(":memory:")
        await _ensure_schema(db)
        app, orig_get_db, orig_conn, t_mod = _make_app_with_db(db)
        try:
            await session_store.create_session("streamed-thread-002", user_id="local")
            await _upsert_session("streamed-thread-002", title="Conversa Persistida")
            await _increment_message_count("streamed-thread-002")

            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/vectora.chat.v1.ThreadService/ListThreads",
                json={"limit": 50},
            )
            assert response.status_code == 200
            body = response.json()
            thread = next(
                (t for t in body["threads"] if t["id"] == "streamed-thread-002"), None
            )
            assert thread is not None
            assert thread.get("title") == "Conversa Persistida"
        finally:
            _restore_app(t_mod, orig_get_db, orig_conn)
            await db.close()


# ---------------------------------------------------------------------------
# Modo: dev → code — _normalize_mode + _row_to_thread
# ---------------------------------------------------------------------------


class TestNormalizeMode:
    def test_dev_becomes_code(self):
        from backend.api.handlers.threads import _normalize_mode

        assert _normalize_mode("dev") == "code"

    def test_code_stays_code(self):
        from backend.api.handlers.threads import _normalize_mode

        assert _normalize_mode("code") == "code"

    def test_chat_preserved(self):
        from backend.api.handlers.threads import _normalize_mode

        assert _normalize_mode("chat") == "chat"

    def test_none_defaults_to_code(self):
        from backend.api.handlers.threads import _normalize_mode

        assert _normalize_mode(None) == "code"

    def test_empty_defaults_to_code(self):
        from backend.api.handlers.threads import _normalize_mode

        assert _normalize_mode("") == "code"

    def test_unknown_defaults_to_code(self):
        from backend.api.handlers.threads import _normalize_mode

        assert _normalize_mode("qualquer") == "code"


def _row(extra: dict) -> tuple:
    return ("tid", "human", "2026-01-01", "2026-01-02", 0, json.dumps(extra))


class TestRowToThreadMode:
    def test_legacy_dev_read_as_code(self):
        from backend.api.handlers.threads import _row_to_thread

        t = _row_to_thread(_row({"mode": "dev", "title": "X"}))
        assert t.mode == "code"
        assert t.title == "X"

    def test_chat_mode_preserved(self):
        from backend.api.handlers.threads import _row_to_thread

        t = _row_to_thread(_row({"mode": "chat"}))
        assert t.mode == "chat"

    def test_code_mode_preserved(self):
        from backend.api.handlers.threads import _row_to_thread

        t = _row_to_thread(_row({"mode": "code"}))
        assert t.mode == "code"

    def test_missing_mode_defaults_code(self):
        from backend.api.handlers.threads import _row_to_thread

        t = _row_to_thread(_row({"title": "sem modo"}))
        assert t.mode == "code"

    def test_malformed_extra_defaults_code(self):
        from backend.api.handlers.threads import _row_to_thread

        bad_row = ("tid", "human", "2026-01-01", "2026-01-02", 0, "NOTJSON{")
        t = _row_to_thread(bad_row)
        assert t.mode == "code"


# ---------------------------------------------------------------------------
# Coluna mode de 1ª classe — migração, backfill e filtro
# ---------------------------------------------------------------------------


class TestModeColumn:
    """mode promovido de extra JSON para coluna; aiosqlite real."""

    async def _fresh_db(self):
        import aiosqlite

        from backend.api.handlers.threads import _ensure_schema

        db = await aiosqlite.connect(":memory:")
        await _ensure_schema(db)
        return db

    @pytest.mark.asyncio
    async def test_migrate_adds_column_and_backfills_dev_to_code(self):
        import aiosqlite

        from backend.api.handlers.threads import _migrate_mode_column

        db = await aiosqlite.connect(":memory:")
        # Schema ANTIGO (sem coluna mode), com modos em extra.
        await db.execute(
            "CREATE TABLE vectora_sessions (thread_id TEXT PRIMARY KEY, "
            "user_type TEXT, created_at TEXT, last_activity TEXT, "
            "message_count INTEGER, extra TEXT)"
        )
        await db.execute(
            "INSERT INTO vectora_sessions VALUES (?,?,?,?,?,?)",
            ("t-dev", "human", "c", "a", 0, json.dumps({"mode": "dev"})),
        )
        await db.execute(
            "INSERT INTO vectora_sessions VALUES (?,?,?,?,?,?)",
            ("t-chat", "human", "c", "a", 0, json.dumps({"mode": "chat"})),
        )
        await db.execute(
            "INSERT INTO vectora_sessions VALUES (?,?,?,?,?,?)",
            ("t-none", "human", "c", "a", 0, "{}"),
        )
        await db.commit()

        await _migrate_mode_column(db)

        async with db.execute(
            "SELECT thread_id, mode FROM vectora_sessions ORDER BY thread_id"
        ) as cur:
            rows = {r[0]: r[1] for r in await cur.fetchall()}
        await db.close()
        assert rows == {"t-dev": "code", "t-chat": "chat", "t-none": "code"}

    @pytest.mark.asyncio
    async def test_migrate_is_idempotent(self):
        from backend.api.handlers.threads import _migrate_mode_column

        db = await self._fresh_db()
        # Rodar de novo num schema que já tem a coluna não deve quebrar.
        await _migrate_mode_column(db)
        async with db.execute("PRAGMA table_info(vectora_sessions)") as cur:
            cols = [r[1] for r in await cur.fetchall()]
        await db.close()
        assert cols.count("mode") == 1

    @pytest.mark.asyncio
    async def test_upsert_writes_mode_column_and_list_filters(self):
        """_upsert_session sozinho registra metadados (workspace/mode), mas
        só uma troca de mensagem real (_increment_message_count, chamado por
        stream_chat a cada turno) faz a thread aparecer em ListThreads —
        replica o par (_upsert_session + _increment_message_count) que
        stream_chat executa a cada turno de verdade.
        """
        from backend.api.handlers import threads as th

        db = await self._fresh_db()
        with patch.object(th, "_get_db", new=AsyncMock(return_value=db)):
            await th._upsert_session("t-code", mode="code")
            await th._increment_message_count("t-code")
            await th._upsert_session("t-chat", mode="chat")
            await th._increment_message_count("t-chat")

            from backend.api.schemas import ListThreadsRequest

            all_threads = await th.list_threads(ListThreadsRequest(limit=50))
            only_chat = await th.list_threads(ListThreadsRequest(limit=50, mode="chat"))
            only_code = await th.list_threads(ListThreadsRequest(limit=50, mode="code"))
        await db.close()

        assert {t.id for t in all_threads.threads} == {"t-code", "t-chat"}
        assert [t.id for t in only_chat.threads] == ["t-chat"]
        assert [t.id for t in only_code.threads] == ["t-code"]
        assert only_chat.threads[0].mode == "chat"

    @pytest.mark.asyncio
    async def test_upsert_without_message_never_lists(self):
        """Par de erro do teste acima — sem _increment_message_count (nenhuma
        mensagem trocada), a thread nunca aparece em ListThreads."""
        from backend.api.handlers import threads as th
        from backend.api.schemas import ListThreadsRequest

        db = await self._fresh_db()
        with patch.object(th, "_get_db", new=AsyncMock(return_value=db)):
            await th._upsert_session("t-empty", mode="code")
            out = await th.list_threads(ListThreadsRequest(limit=50))
        await db.close()

        assert {t.id for t in out.threads} == set()

    @pytest.mark.asyncio
    async def test_upsert_normalizes_dev_to_code_in_column(self):
        from backend.api.handlers import threads as th

        db = await self._fresh_db()
        with patch.object(th, "_get_db", new=AsyncMock(return_value=db)):
            await th._upsert_session("t-legacy", mode="dev")
            async with db.execute(
                "SELECT mode FROM vectora_sessions WHERE thread_id = ?", ("t-legacy",)
            ) as cur:
                row = await cur.fetchone()
        await db.close()
        assert row[0] == "code"


# ---------------------------------------------------------------------------
# Sessões fixadas — coluna pinned + ordenação + UpdateThread parcial
# ---------------------------------------------------------------------------


class TestPinnedColumn:
    """pinned promovido a coluna de 1ª classe; aiosqlite real."""

    async def _fresh_db(self):
        import aiosqlite

        from backend.api.handlers.threads import _ensure_schema

        db = await aiosqlite.connect(":memory:")
        await _ensure_schema(db)
        return db

    @pytest.mark.asyncio
    async def test_migrate_adds_column_on_old_schema(self):
        import aiosqlite

        from backend.api.handlers.threads import _migrate_pinned_column

        db = await aiosqlite.connect(":memory:")
        # Schema antigo (sem coluna pinned).
        await db.execute(
            "CREATE TABLE vectora_sessions (thread_id TEXT PRIMARY KEY, "
            "user_type TEXT, created_at TEXT, last_activity TEXT, "
            "message_count INTEGER, extra TEXT, mode TEXT)"
        )
        await db.execute(
            "INSERT INTO vectora_sessions VALUES (?,?,?,?,?,?,?)",
            ("t-old", "human", "c", "a", 0, "{}", "code"),
        )
        await db.commit()

        await _migrate_pinned_column(db)

        async with db.execute("PRAGMA table_info(vectora_sessions)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        async with db.execute(
            "SELECT pinned FROM vectora_sessions WHERE thread_id = ?", ("t-old",)
        ) as cur:
            row = await cur.fetchone()
        await db.close()
        assert "pinned" in cols
        assert row is not None
        assert row[0] == 0

    @pytest.mark.asyncio
    async def test_migrate_pinned_is_idempotent(self):
        from backend.api.handlers.threads import _migrate_pinned_column

        db = await self._fresh_db()
        # Rodar de novo num schema que já tem a coluna não deve quebrar.
        await _migrate_pinned_column(db)
        async with db.execute("PRAGMA table_info(vectora_sessions)") as cur:
            cols = [r[1] for r in await cur.fetchall()]
        await db.close()
        assert cols.count("pinned") == 1

    @pytest.mark.asyncio
    async def test_list_threads_orders_pinned_before_last_activity(self):
        from backend.api.handlers import threads as th
        from backend.api.schemas import ListThreadsRequest, UpdateThreadRequest

        db = await self._fresh_db()
        with patch.object(th, "_get_db", new=AsyncMock(return_value=db)):
            # Ordem de criação: older-not-pinned, newest-not-pinned, mid-pinned.
            await th._upsert_session("older-not-pinned")
            await th._increment_message_count("older-not-pinned")
            await th._upsert_session("newest-not-pinned")
            await th._increment_message_count("newest-not-pinned")
            await th._upsert_session("mid-pinned")
            await th._increment_message_count("mid-pinned")
            await th.update_thread(
                UpdateThreadRequest(thread_id="mid-pinned", pinned=True)
            )

            out = await th.list_threads(ListThreadsRequest(limit=50))
        await db.close()

        ids = [t.id for t in out.threads]
        assert ids[0] == "mid-pinned"
        assert set(ids[1:]) == {"older-not-pinned", "newest-not-pinned"}

    @pytest.mark.asyncio
    async def test_update_thread_pin_only_does_not_touch_title(self):
        """Erro/borda: fixar uma sessão não deve resetar o título já gravado
        (title vem `None` no request quando só se quer fixar)."""
        from backend.api.handlers import threads as th
        from backend.api.schemas import UpdateThreadRequest

        db = await self._fresh_db()
        with patch.object(th, "_get_db", new=AsyncMock(return_value=db)):
            await th._upsert_session("t-titled", title="Meu Título")

            result = await th.update_thread(
                UpdateThreadRequest(thread_id="t-titled", pinned=True)
            )
        await db.close()

        assert result.title == "Meu Título"
        assert result.pinned is True

    @pytest.mark.asyncio
    async def test_increment_remember_turn_count_upserts_in_extra(self):
        from backend.api.handlers import threads as th

        db = await self._fresh_db()
        with patch.object(th, "_get_db", new=AsyncMock(return_value=db)):
            first = await th.increment_remember_turn_count("t-remember")
            second = await th.increment_remember_turn_count("t-remember")
        await db.close()

        assert first == 1
        assert second == 2

    @pytest.mark.asyncio
    async def test_remember_pending_default_false_for_unknown_thread(self):
        from backend.api.handlers import threads as th

        db = await self._fresh_db()
        with patch.object(th, "_get_db", new=AsyncMock(return_value=db)):
            pending = await th.get_remember_pending("does-not-exist")
        await db.close()

        assert pending is False

    @pytest.mark.asyncio
    async def test_set_and_get_remember_pending_round_trips(self):
        from backend.api.handlers import threads as th

        db = await self._fresh_db()
        with patch.object(th, "_get_db", new=AsyncMock(return_value=db)):
            await th.set_remember_pending("t-pending", True)
            pending_true = await th.get_remember_pending("t-pending")
            await th.set_remember_pending("t-pending", False)
            pending_false = await th.get_remember_pending("t-pending")
        await db.close()

        assert pending_true is True
        assert pending_false is False

    @pytest.mark.asyncio
    async def test_increment_remember_turn_count_preserves_existing_extra(self):
        """Erro/borda: incrementar o contador não pode apagar outros campos
        já gravados em extra (title, pins etc.)."""
        from backend.api.handlers import threads as th

        db = await self._fresh_db()
        with patch.object(th, "_get_db", new=AsyncMock(return_value=db)):
            await th._upsert_session("t-mixed", title="Título preservado")
            await th.increment_remember_turn_count("t-mixed")

            async with db.execute(
                "SELECT extra FROM vectora_sessions WHERE thread_id = ?",
                ("t-mixed",),
            ) as cur:
                row = await cur.fetchone()
        await db.close()

        assert row is not None
        extra = json.loads(row[0])
        assert extra["title"] == "Título preservado"
        assert extra["remember_turn_count"] == 1

    @pytest.mark.asyncio
    async def test_update_thread_nonexistent_returns_404_when_pinning(self):
        from fastapi import HTTPException

        from backend.api.handlers import threads as th
        from backend.api.schemas import UpdateThreadRequest

        db = await self._fresh_db()
        with patch.object(th, "_get_db", new=AsyncMock(return_value=db)):
            with pytest.raises(HTTPException) as exc_info:
                await th.update_thread(
                    UpdateThreadRequest(thread_id="does-not-exist", pinned=True)
                )
        await db.close()
        assert exc_info.value.status_code == 404
