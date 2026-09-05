"""`reconcile_vectora_sessions` — rede de segurança contra a divergência
entre `vectora_sessions` (o que a sidebar lê) e `sessions.db`/`SessionStore`
(fonte de verdade do motor nativo).

Achado real (2026-09-03): `vectora_sessions` com só 1 linha há semanas
enquanto `sessions.db` tinha dezenas de threads reais, incluindo uma
conversa do dia com 32 mensagens completamente ausente da sidebar. A causa
raiz exata ficou sem confirmação determinística (VECTORA_HOME e o caminho
de upsert de `chat.py` foram descartados por leitura de código), então a
correção é uma reconciliação idempotente que repovoa qualquer divergência
— não depende de saber qual bug específico causou o desalinhamento.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import aiosqlite
import pytest

from backend.api.handlers import threads as th
from backend.api.schemas import DeleteThreadRequest
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.vtypes.message import MessageRole, text_message


@pytest.fixture
async def checkpoints_db() -> AsyncIterator[aiosqlite.Connection]:
    db = await aiosqlite.connect(":memory:")
    await th._ensure_schema(db)
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def session_store(tmp_path) -> AsyncIterator[SessionStore]:
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


async def _real_thread(
    session_store: SessionStore,
    thread_id: str,
    n_messages: int,
    workspace_id: str = "",
) -> None:
    await session_store.create_session(
        thread_id, user_id="alice", workspace_id=workspace_id or None, mode="code"
    )
    for i in range(n_messages):
        await session_store.append_message(
            thread_id, text_message(MessageRole.USER, f"mensagem {i}")
        )


async def _real_subagent_thread(session_store: SessionStore, thread_id: str) -> None:
    await session_store.create_session(
        thread_id, user_id="alice", workspace_id="ws", mode="subagent"
    )
    await session_store.append_message(
        thread_id, text_message(MessageRole.USER, "resultado interno")
    )


def _http_request_alice() -> MagicMock:
    request = MagicMock()
    user = MagicMock()
    user.id = "alice"
    request.state = MagicMock(user=user)
    return request


class TestReconcileRepovoaThreadAusente:
    async def test_subagent_interno_nunca_e_repovoado_na_sidebar(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        await _real_subagent_thread(session_store, "thread-pai:search:interno")

        assert await th.reconcile_vectora_sessions() == 0
        async with checkpoints_db.execute(
            "SELECT 1 FROM vectora_sessions WHERE thread_id = ?",
            ("thread-pai:search:interno",),
        ) as cur:
            assert await cur.fetchone() is None

    async def test_thread_real_ausente_de_vectora_sessions_e_repovoada(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        await _real_thread(session_store, "thread-perdida", 32)

        reconciled = await th.reconcile_vectora_sessions()

        assert reconciled == 1
        async with checkpoints_db.execute(
            "SELECT message_count FROM vectora_sessions WHERE thread_id = ?",
            ("thread-perdida",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 32

    async def test_thread_repovoada_aparece_em_list_threads(
        self, session_store: SessionStore
    ) -> None:
        await _real_thread(session_store, "thread-perdida", 5)

        await th.reconcile_vectora_sessions()
        result = await th.list_threads(
            th.ListThreadsRequest(limit=50), _http_request_alice()
        )

        assert [t.id for t in result.threads] == ["thread-perdida"]

    async def test_varias_threads_ausentes_todas_repovoadas(
        self, session_store: SessionStore
    ) -> None:
        await _real_thread(session_store, "thread-1", 3)
        await _real_thread(session_store, "thread-2", 7)

        reconciled = await th.reconcile_vectora_sessions()

        assert reconciled == 2


class TestReconcileCorrigeContagemDivergente:
    async def test_message_count_desatualizado_e_corrigido_sem_apagar_extra(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        await _real_thread(session_store, "thread-1", 10)
        # vectora_sessions já tem a thread, mas com contagem velha (upsert
        # que falhou no meio, ou incremento perdido) e um título já gravado
        # pela UI — a reconciliação corrige a contagem sem tocar no título.
        await th._upsert_session("thread-1", title="Titulo ja existente")
        await checkpoints_db.execute(
            "UPDATE vectora_sessions SET message_count = 1 WHERE thread_id = ?",
            ("thread-1",),
        )
        await checkpoints_db.commit()

        reconciled = await th.reconcile_vectora_sessions()

        assert reconciled == 1
        async with checkpoints_db.execute(
            "SELECT message_count, extra FROM vectora_sessions WHERE thread_id = ?",
            ("thread-1",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        count, extra_json = row
        assert count == 10
        assert json.loads(extra_json)["title"] == "Titulo ja existente"


class TestReconcileNaoMexeEmThreadJaSincronizada:
    async def test_thread_ja_sincronizada_nao_conta_como_reconciliada(
        self, session_store: SessionStore
    ) -> None:
        await _real_thread(session_store, "thread-1", 4)
        await th._upsert_session("thread-1")
        await th._increment_message_count("thread-1")
        await th._increment_message_count("thread-1")
        await th._increment_message_count("thread-1")
        await th._increment_message_count("thread-1")

        reconciled = await th.reconcile_vectora_sessions()

        assert reconciled == 0

    async def test_thread_sem_nenhuma_mensagem_nunca_e_criada(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        """Erro/borda: thread registrada em SessionStore (via create_session,
        sem nenhuma mensagem anexada) não vira linha fantasma em
        vectora_sessions — reconcile só repovoa conversa real."""
        await session_store.create_session("thread-vazia", user_id="alice")

        reconciled = await th.reconcile_vectora_sessions()

        assert reconciled == 0
        async with checkpoints_db.execute(
            "SELECT COUNT(*) FROM vectora_sessions"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 0

    async def test_rodar_duas_vezes_seguidas_e_idempotente(
        self, session_store: SessionStore
    ) -> None:
        await _real_thread(session_store, "thread-1", 5)

        first = await th.reconcile_vectora_sessions()
        second = await th.reconcile_vectora_sessions()

        assert first == 1
        assert second == 0


class TestReconcilePreservaWorkspaceId:
    """Achado real (2026-09-04): a reconciliação recuperava a thread na
    sidebar mas gravava `extra` fixo como `'{}'` — nunca lia
    `sessions.workspace_id` de SessionStore. A conversa reaparecia sem o
    workspace correto."""

    async def test_thread_repovoada_recebe_workspace_id_de_session_store(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        await _real_thread(session_store, "thread-1", 3, workspace_id="ws-real")

        await th.reconcile_vectora_sessions()

        async with checkpoints_db.execute(
            "SELECT extra FROM vectora_sessions WHERE thread_id = ?",
            ("thread-1",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert json.loads(row[0])["workspace_id"] == "ws-real"

    async def test_thread_sem_workspace_id_nao_grava_chave_vazia(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        await _real_thread(session_store, "thread-1", 3)

        await th.reconcile_vectora_sessions()

        async with checkpoints_db.execute(
            "SELECT extra FROM vectora_sessions WHERE thread_id = ?",
            ("thread-1",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert "workspace_id" not in json.loads(row[0])

    async def test_repara_thread_ja_recuperada_sem_workspace_id_sem_apagar_titulo(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        """Regressão específica: uma thread já recuperada por uma versão
        anterior do reconcile (extra='{}', sem workspace_id) precisa ser
        reparada numa próxima rodada — sem apagar título já gravado
        nesse meio-tempo pela UI (GenerateTitle)."""
        await _real_thread(session_store, "thread-1", 3, workspace_id="ws-real")
        await checkpoints_db.execute(
            "INSERT INTO vectora_sessions "
            "(thread_id, created_at, last_activity, message_count, extra) "
            "VALUES ('thread-1', '2026-01-01', '2026-01-01', 3, ?)",
            (json.dumps({"title": "Ja titulada"}),),
        )
        await checkpoints_db.commit()

        reconciled = await th.reconcile_vectora_sessions()

        assert reconciled == 1
        async with checkpoints_db.execute(
            "SELECT extra FROM vectora_sessions WHERE thread_id = ?",
            ("thread-1",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        extra = json.loads(row[0])
        assert extra["workspace_id"] == "ws-real"
        assert extra["title"] == "Ja titulada"

    async def test_remove_workspace_id_obsoleto_quando_session_store_nao_tem_mais(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        """Erro/borda: a divergência precisa ser detectada mesmo quando
        SessionStore NÃO tem workspace — se a thread perdeu o workspace
        (ou o valor em `extra` ficou obsoleto de outra forma) e
        `sessions.workspace_id` virou None, a chave velha precisa ser
        removida, não deixar a thread associada ao workspace errado."""
        await _real_thread(session_store, "thread-1", 3)  # sem workspace_id
        await checkpoints_db.execute(
            "INSERT INTO vectora_sessions "
            "(thread_id, created_at, last_activity, message_count, extra) "
            "VALUES ('thread-1', '2026-01-01', '2026-01-01', 3, ?)",
            (json.dumps({"workspace_id": "ws-obsoleto", "title": "Titulo"}),),
        )
        await checkpoints_db.commit()

        reconciled = await th.reconcile_vectora_sessions()

        assert reconciled == 1
        async with checkpoints_db.execute(
            "SELECT extra FROM vectora_sessions WHERE thread_id = ?",
            ("thread-1",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        extra = json.loads(row[0])
        assert "workspace_id" not in extra
        assert extra["title"] == "Titulo"

    async def test_extra_json_corrompido_nao_derruba_a_reconciliacao(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        """Erro/borda: `extra` malformado não pode fazer json_patch lançar
        e derrubar o lote inteiro antes do commit."""
        await _real_thread(session_store, "thread-1", 3, workspace_id="ws-real")
        await checkpoints_db.execute(
            "INSERT INTO vectora_sessions "
            "(thread_id, created_at, last_activity, message_count, extra) "
            "VALUES ('thread-1', '2026-01-01', '2026-01-01', 1, 'nao-e-json-valido')"
        )
        await checkpoints_db.commit()

        reconciled = await th.reconcile_vectora_sessions()

        assert reconciled == 1
        async with checkpoints_db.execute(
            "SELECT message_count, extra FROM vectora_sessions WHERE thread_id = ?",
            ("thread-1",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        count, extra_json = row
        assert count == 3
        assert json.loads(extra_json)["workspace_id"] == "ws-real"


class TestReconcileRespeitaTombstoneDeExclusao:
    """Achado real (CodeRabbit, 2026-09-04): `delete_thread` só apagava
    `vectora_sessions`/`sessions.db` sem deixar rastro — uma reconciliação
    que já tivesse lido `list_all_sessions()` ANTES da exclusão (achando a
    thread ainda viva) escrevia de volta em `vectora_sessions` DEPOIS,
    ressuscitando a thread. O tombstone em `deleted_threads` (gravado por
    `delete_thread` antes de qualquer exclusão) fecha essa corrida."""

    async def test_thread_com_tombstone_nunca_e_recriada_mesmo_presente_no_session_store(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        await _real_thread(session_store, "thread-1", 5)
        await th.delete_thread(
            DeleteThreadRequest(thread_id="thread-1"),
            _http_request_alice(),
        )

        # Simula a corrida: SessionStore "ainda tem" a thread (leitura
        # concorrente que rodou antes da exclusão terminar, ou um retry
        # que recriou a sessão) — o tombstone precisa bloquear mesmo assim.
        await _real_thread(session_store, "thread-1", 5)

        reconciled = await th.reconcile_vectora_sessions()

        assert reconciled == 0
        async with checkpoints_db.execute(
            "SELECT COUNT(*) FROM vectora_sessions WHERE thread_id = ?",
            ("thread-1",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 0

    async def test_erro_borda_tombstone_de_thread_nunca_apagada_nao_afeta_outras(
        self, session_store: SessionStore, checkpoints_db: aiosqlite.Connection
    ) -> None:
        """Erro/borda: um tombstone de OUTRA thread não pode bloquear a
        recriação de uma thread ativa sem tombstone nenhum — a checagem
        precisa ser por thread_id, não um interruptor global."""
        await _real_thread(session_store, "thread-nunca-apagada", 4)
        await checkpoints_db.execute(
            "INSERT INTO deleted_threads (thread_id, deleted_at) VALUES (?, ?)",
            ("thread-de-outra-conversa-ja-apagada", "2026-01-01T00:00:00+00:00"),
        )
        await checkpoints_db.commit()

        reconciled = await th.reconcile_vectora_sessions()

        assert reconciled == 1
        async with checkpoints_db.execute(
            "SELECT COUNT(*) FROM vectora_sessions WHERE thread_id = ?",
            ("thread-nunca-apagada",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 1

    @pytest.mark.parametrize("delete_primeiro", [False, True])
    async def test_reconcile_e_delete_thread_concorrentes_convergem_sem_ressuscitar(
        self,
        session_store: SessionStore,
        checkpoints_db: aiosqlite.Connection,
        delete_primeiro: bool,
    ) -> None:
        """`reconcile_vectora_sessions` e `delete_thread` rodando de verdade
        ao mesmo tempo (`asyncio.gather`) — não um mock de atraso simulado —
        precisam convergir pro mesmo estado final (thread apagada e nunca
        recriada) não importa qual dos dois vence a corrida pelo lock
        (`_reconcile_delete_lock`), já que `delete_thread` sempre termina
        gravando o tombstone e apagando, incondicionalmente. Parametrizado
        nas duas ordens de argumento — `asyncio.gather` inicia as corrotinas
        na ordem dada e o escalonamento resultante é estável, então só uma
        ordem não provaria que o lock (e não a sorte) garante a convergência."""
        await _real_thread(session_store, "thread-1", 5)

        delete_coro = th.delete_thread(
            DeleteThreadRequest(thread_id="thread-1"),
            _http_request_alice(),
        )
        reconcile_coro = th.reconcile_vectora_sessions()
        if delete_primeiro:
            await asyncio.gather(delete_coro, reconcile_coro)
        else:
            await asyncio.gather(reconcile_coro, delete_coro)

        async with checkpoints_db.execute(
            "SELECT COUNT(*) FROM vectora_sessions WHERE thread_id = ?",
            ("thread-1",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 0

        # Roda a reconciliação de novo (sem mais corrida) — se o tombstone
        # não tivesse pego por causa da corrida, essa segunda rodada
        # ressuscitaria a thread.
        reconciled_depois = await th.reconcile_vectora_sessions()
        assert reconciled_depois == 0
        async with checkpoints_db.execute(
            "SELECT COUNT(*) FROM vectora_sessions WHERE thread_id = ?",
            ("thread-1",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 0
