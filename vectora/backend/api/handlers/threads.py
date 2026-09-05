"""Handler do serviço ThreadService — CRUD de threads via REST.

Endpoints (todos POST, padrão ConnectRPC):
    POST /vectora.chat.v1.ThreadService/CreateThread
    POST /vectora.chat.v1.ThreadService/GetThread
    POST /vectora.chat.v1.ThreadService/ListThreads
    POST /vectora.chat.v1.ThreadService/DeleteThread
    POST /vectora.chat.v1.ThreadService/GetHistory

Endpoints REST de rewind:
    GET  /threads/{thread_id}/checkpoints  — lista checkpoints de turno
    POST /threads/{thread_id}/rewind       — restaura workspace para checkpoint

Persiste em checkpoints.db (aiosqlite direto, sem ORM/checkpointer externo)
via as tabelas vectora_sessions (metadados de UI) / vectora_checkpoint_artifacts
(snapshots de rewind) — histórico real das mensagens fica em sessions.db,
via SessionStore (backend/persistence/native/session_store.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.api.schemas import (
    CreateThreadRequest,
    DeleteThreadRequest,
    GenerateTitleRequest,
    GenerateTitleResponse,
    GetHistoryRequest,
    GetHistoryResponse,
    GetThreadPinsRequest,
    GetThreadRequest,
    HistoryMessage,
    HITLEvent,
    ListThreadsRequest,
    ListThreadsResponse,
    PagedHistoryResponse,
    SetThreadPinsRequest,
    Thread,
    ThreadPinsResponse,
    TodoItem,
    UpdateThreadRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _user_id(request: Request) -> str:
    """Extrai o user_id do request autenticado, ou 'local' em modo CLI."""
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "id", None):
        return str(user.id)
    return "local"


# ---------------------------------------------------------------------------
# Lazy DB loader
# ---------------------------------------------------------------------------

_db_conn: Any = None
_db_conn_lock: asyncio.Lock = asyncio.Lock()

# Serializa `reconcile_vectora_sessions` contra `delete_thread` — as duas
# compartilham a mesma conexão `_db_conn`, então sem essa exclusão mútua a
# leitura do tombstone em `deleted_threads` e o UPSERT de `vectora_sessions`
# (vários `await` entre si, dentro do loop de reconciliação) podem intercalar
# com uma exclusão concorrente: a reconciliação lê "sem tombstone", a
# exclusão grava tombstone + apaga tudo, e a reconciliação — usando o
# instantâneo já obsoleto — reinsere a thread mesmo assim. O lock cobre a
# seção crítica inteira (leitura do tombstone → upserts → commit) em
# `reconcile_vectora_sessions`, e a escrita do tombstone + as duas exclusões
# em `delete_thread`, garantindo que nunca rodam entrelaçados.
_reconcile_delete_lock: asyncio.Lock = asyncio.Lock()


async def _ensure_schema(db: Any) -> None:
    """Cria as tabelas do banco de checkpoints/sessões se não existirem.

    Idempotente. Exportada para que o ``_lifespan`` do server possa chamar
    no startup, garantindo que as tabelas existam antes do primeiro request —
    evita race com outro consumidor do mesmo arquivo
    ``~/.vectora/checkpoints.db`` criando tabela concorrentemente.

    Tabelas gerenciadas:
    - ``vectora_sessions`` — metadados de cada thread/sessão.
    - ``vectora_checkpoint_artifacts`` — metadados dos snapshots de rewind.
    - ``deleted_threads`` — tombstone de exclusão (ver ``delete_thread``).
    """
    await db.execute("""
        CREATE TABLE IF NOT EXISTS vectora_sessions (
            thread_id     TEXT    PRIMARY KEY,
            user_type     TEXT    NOT NULL DEFAULT 'human',
            created_at    TEXT    NOT NULL,
            last_activity TEXT    NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            extra         TEXT    NOT NULL DEFAULT '{}',
            mode          TEXT    NOT NULL DEFAULT 'code',
            pinned        INTEGER NOT NULL DEFAULT 0
        )
    """)
    await _migrate_mode_column(db)
    await _migrate_pinned_column(db)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS vectora_checkpoint_artifacts (
            id              TEXT PRIMARY KEY,
            thread_id       TEXT NOT NULL,
            checkpoint_id   TEXT NOT NULL,
            strategy        TEXT NOT NULL DEFAULT 'git',
            git_sha         TEXT,
            snapshot_path   TEXT,
            files_touched   TEXT NOT NULL DEFAULT '[]',
            created_at      TEXT NOT NULL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS deleted_threads (
            thread_id  TEXT PRIMARY KEY,
            deleted_at TEXT NOT NULL
        )
    """)
    await db.commit()


async def _migrate_mode_column(db: Any) -> None:
    """Promove ``mode`` de ``extra`` JSON para coluna de 1ª classe (idempotente).

    Em bancos antigos a coluna não existe: adiciona via ALTER TABLE e faz backfill
    a partir de ``extra["mode"]`` normalizado (``dev``→``code``). Bancos novos já
    nascem com a coluna (DEFAULT 'code') e o backfill é no-op.
    """
    async with db.execute("PRAGMA table_info(vectora_sessions)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "mode" not in cols:
        await db.execute(
            "ALTER TABLE vectora_sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'code'"
        )

    # Backfill: linhas cujo mode da coluna ainda não reflete o extra["mode"].
    async with db.execute("SELECT thread_id, extra, mode FROM vectora_sessions") as cur:
        rows = await cur.fetchall()
    for thread_id, extra_json, current in rows:
        try:
            extra = json.loads(extra_json or "{}")
        except Exception:
            extra = {}
        desired = _normalize_mode(extra.get("mode"))
        if desired != current:
            await db.execute(
                "UPDATE vectora_sessions SET mode = ? WHERE thread_id = ?",
                (desired, thread_id),
            )
    await db.commit()


async def _migrate_pinned_column(db: Any) -> None:
    """Adiciona a coluna ``pinned`` (0/1) se ausente — idempotente, mesmo
    padrão de ``_migrate_mode_column``. Sem backfill: não havia equivalente
    em ``extra`` antes desta feature."""
    async with db.execute("PRAGMA table_info(vectora_sessions)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "pinned" not in cols:
        await db.execute(
            "ALTER TABLE vectora_sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
        )
        await db.commit()


async def _get_db() -> Any:
    """Retorna conexão aiosqlite com o banco de checkpoints/sessões."""
    global _db_conn
    if _db_conn is None:
        # Check-then-act sem lock: duas coroutines chamando antes do primeiro
        # `await aiosqlite.connect()` terminar abriam DUAS conexões pro mesmo
        # arquivo, e a perdedora nunca recebia os PRAGMAs de WAL/busy_timeout
        # a tempo — "database is locked" instantâneo em vez de esperar.
        async with _db_conn_lock:
            if _db_conn is None:
                import aiosqlite

                from backend.settings import settings

                db_path = settings.vectora_home / "checkpoints.db"
                db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = await aiosqlite.connect(str(db_path))
                # Sem busy_timeout, escritas concorrentes de outras conexões
                # abertas pro mesmo checkpoints.db (agent_factory.py,
                # rbac/auth.py, etc.) batem em "database is locked" na hora
                # em vez de esperar — mesmos PRAGMAs do pool hardened de
                # backend/storage/sqlite/pool.py e do checkpointer em
                # agent_factory.py (D2).
                await conn.executescript(
                    "PRAGMA journal_mode=WAL;"
                    "PRAGMA busy_timeout=30000;"
                    "PRAGMA synchronous=NORMAL;"
                )
                _db_conn = conn
        await _ensure_schema(_db_conn)
    return _db_conn


async def ensure_sessions_table() -> None:
    """Cria a tabela ``vectora_sessions`` ao boot (chamada do lifespan)."""
    db = await _get_db()
    await _ensure_schema(db)


async def _get_session_store() -> Any:
    """``SessionStore`` do motor nativo — fonte de verdade sobre EXISTÊNCIA
    e DONO (``user_id``) de uma thread. ``vectora_sessions`` (acima) guarda
    só metadados de UI (título, contagem de mensagens, fixação); nunca decide
    sozinha se uma thread existe ou a quem pertence — todo endpoint protegido
    confirma posse aqui antes de ler/escrever metadados."""
    from backend.services import agent_factory

    return await agent_factory.get_session_store()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_mode(mode: str | None) -> str:
    """Normaliza o modo de sessão.

    Linhas gravadas com a chave ``"dev"`` (e o default ausente) são lidas
    como ``"code"``. O modo conversacional ``"chat"`` é preservado.
    """
    if mode in {"chat", "subagent"}:
        return mode
    return "code"


def _row_to_thread(row: tuple) -> Thread:
    """Converte uma linha da tabela vectora_sessions em Thread.

    A linha traz até 8 colunas (``mode`` e ``pinned`` de 1ª classe nas duas
    últimas posições). Ambas têm fallback pra ``None``/``0`` quando a SELECT
    de origem não as inclui (compatibilidade com chamadas mais antigas).
    """
    thread_id, _, created_at, last_activity, _, extra_json = row[:6]
    mode_col = row[6] if len(row) > 6 else None
    pinned_col = row[7] if len(row) > 7 else 0
    title = ""
    workspace_id = ""
    try:
        extra = json.loads(extra_json or "{}")
        title = extra.get("title", "")
        workspace_id = extra.get("workspace_id", "")
    except Exception:
        extra = {}
    mode = _normalize_mode(mode_col if mode_col is not None else extra.get("mode"))
    return Thread(
        id=str(thread_id),
        created_at=created_at,
        updated_at=last_activity,
        title=title,
        workspace_id=workspace_id,
        mode=mode,
        pinned=bool(pinned_col),
    )


# ---------------------------------------------------------------------------
# _upsert_session — registra/atualiza thread em vectora_sessions
# ---------------------------------------------------------------------------


async def _upsert_session(
    thread_id: str,
    title: str | None = None,
    workspace_id: str | None = None,
    mode: str | None = None,
    user_id: str | None = None,
) -> None:
    """Garante que thread_id existe em vectora_sessions (cria ou atualiza).

    Chamado por stream_chat() para que threads criadas via chat normal
    apareçam em ListThreads após reinicialização do servidor.

    O campo extra é mesclado: title, workspace_id e mode só são sobrescritos
    quando fornecidos, preservando os demais dados já gravados.

    Quando ``user_id`` é passado, garante (idempotente) que a thread também
    existe em ``SessionStore.sessions`` — fonte de verdade sobre posse — antes
    de gravar os metadados de UI aqui. Chamadores que já registraram a posse
    por conta própria (ex.: `stream_chat`, via `session_store.create_session`
    direto) não precisam passar `user_id` de novo.
    """
    if user_id is not None:
        session_store = await _get_session_store()
        await session_store.create_session(
            thread_id,
            user_id=user_id,
            workspace_id=workspace_id,
            mode=_normalize_mode(mode) if mode else "code",
        )

    db = await _get_db()
    now = datetime.now(UTC).isoformat()

    async with db.execute(
        "SELECT extra FROM vectora_sessions WHERE thread_id = ?",
        (thread_id,),
    ) as cur:
        row = await cur.fetchone()
    extra: dict[str, Any] = {}
    if row:
        try:
            extra = json.loads(row[0] or "{}")
        except Exception:
            extra = {}
    if title is not None:
        extra["title"] = title
    if workspace_id is not None:
        extra["workspace_id"] = workspace_id
    if mode is not None:
        extra["mode"] = mode
    extra_json = json.dumps(extra)
    # Coluna mode é 1ª classe; mantém extra["mode"] em sincronia por retrocompat.
    mode_col = _normalize_mode(extra.get("mode"))

    # ON CONFLICT preserva created_at original; atualiza last_activity, extra e mode.
    await db.execute(
        """
        INSERT INTO vectora_sessions
            (thread_id, created_at, last_activity, message_count, extra, mode)
        VALUES (?, ?, ?, 0, ?, ?)
        ON CONFLICT(thread_id) DO UPDATE SET
            last_activity = excluded.last_activity,
            extra        = excluded.extra,
            mode         = excluded.mode
        """,
        (thread_id, now, now, extra_json, mode_col),
    )
    await db.commit()


async def _increment_message_count(thread_id: str) -> None:
    """Incrementa ``message_count`` — chamado uma vez por turno real de chat
    (`stream_chat`), nunca por `_upsert_session` sozinho (que também é
    chamado por geração de título/jobs de fundo, sem mensagem nova nesses
    casos). É o sinal que `ListThreads`/`cleanup_empty_threads` usam pra
    distinguir uma thread com conversa de verdade de uma nunca usada.
    """
    db = await _get_db()
    await db.execute(
        "UPDATE vectora_sessions SET message_count = message_count + 1 WHERE thread_id = ?",
        (thread_id,),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Pins de sessão — arquivos fixados, persistidos em extra["pins"]
# ---------------------------------------------------------------------------


def _normalize_pins(pins: list[str]) -> list[str]:
    """Limpa a lista de pins: trim, separador POSIX, dedup, descarta vazios."""
    clean: list[str] = []
    seen: set[str] = set()
    for raw in pins:
        path = str(raw).strip().replace("\\", "/")
        if path and path not in seen:
            seen.add(path)
            clean.append(path)
    return clean


async def _get_session_pins(thread_id: str) -> list[str]:
    """Lê os pins gravados na sessão; thread inexistente ou extra inválido → []."""
    db = await _get_db()
    async with db.execute(
        "SELECT extra FROM vectora_sessions WHERE thread_id = ?", (thread_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return []
    try:
        extra = json.loads(row[0] or "{}")
    except Exception:
        return []
    pins = extra.get("pins", [])
    return [str(p) for p in pins] if isinstance(pins, list) else []


async def _set_session_pins(thread_id: str, pins: list[str]) -> list[str]:
    """Grava os pins na sessão (UPSERT), mesclando com o extra existente.

    Devolve a lista normalizada efetivamente persistida.
    """
    clean = _normalize_pins(pins)
    db = await _get_db()
    now = datetime.now(UTC).isoformat()

    async with db.execute(
        "SELECT extra FROM vectora_sessions WHERE thread_id = ?", (thread_id,)
    ) as cur:
        row = await cur.fetchone()
    extra: dict[str, Any] = {}
    if row:
        try:
            extra = json.loads(row[0] or "{}")
        except Exception:
            extra = {}
    extra["pins"] = clean
    extra_json = json.dumps(extra)

    await db.execute(
        """
        INSERT INTO vectora_sessions
            (thread_id, created_at, last_activity, message_count, extra)
        VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(thread_id) DO UPDATE SET
            last_activity = excluded.last_activity,
            extra        = excluded.extra
        """,
        (thread_id, now, now, extra_json),
    )
    await db.commit()
    return clean


# ---------------------------------------------------------------------------
# Contador de turnos do Remember — gatilho automático a cada N turnos,
# persistido em extra (mesmo padrão de pins acima) — sem coluna nova.
# ---------------------------------------------------------------------------


async def _mutate_session_extra(thread_id: str, mutate: Any) -> dict:
    """Lê o ``extra`` atual da sessão, aplica ``mutate(extra) -> extra`` e
    persiste (UPSERT) — helper comum aos mutadores de ``extra`` (pins,
    contador do Remember). Devolve o ``extra`` resultante."""
    db = await _get_db()
    now = datetime.now(UTC).isoformat()

    async with db.execute(
        "SELECT extra FROM vectora_sessions WHERE thread_id = ?", (thread_id,)
    ) as cur:
        row = await cur.fetchone()
    extra: dict[str, Any] = {}
    if row:
        try:
            extra = json.loads(row[0] or "{}")
        except Exception:
            extra = {}
    extra = mutate(extra)
    extra_json = json.dumps(extra)

    await db.execute(
        """
        INSERT INTO vectora_sessions
            (thread_id, created_at, last_activity, message_count, extra)
        VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(thread_id) DO UPDATE SET
            last_activity = excluded.last_activity,
            extra        = excluded.extra
        """,
        (thread_id, now, now, extra_json),
    )
    await db.commit()
    return extra


async def increment_remember_turn_count(thread_id: str) -> int:
    """Incrementa ``extra["remember_turn_count"]`` e devolve o novo valor."""
    count_holder: dict[str, int] = {}

    def _mutate(extra: dict) -> dict:
        count = int(extra.get("remember_turn_count", 0)) + 1
        extra["remember_turn_count"] = count
        count_holder["count"] = count
        return extra

    await _mutate_session_extra(thread_id, _mutate)
    return count_holder["count"]


async def get_remember_pending(thread_id: str) -> bool:
    """Lê se há uma proposta do Remember pendente (ainda não resolvida) pra
    essa thread — bloqueia um novo gatilho automático até ser resolvida."""
    db = await _get_db()
    async with db.execute(
        "SELECT extra FROM vectora_sessions WHERE thread_id = ?", (thread_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return False
    try:
        extra = json.loads(row[0] or "{}")
    except Exception:
        return False
    return bool(extra.get("remember_pending", False))


async def set_remember_pending(thread_id: str, pending: bool) -> None:
    """Marca (ou limpa) a proposta pendente do Remember para a thread."""

    def _mutate(extra: dict) -> dict:
        extra["remember_pending"] = pending
        return extra

    await _mutate_session_extra(thread_id, _mutate)


_PIN_CONTENT_CAP = 4000  # chars por arquivo fixado injetados no contexto


async def build_pinned_context(
    thread_id: str,
    workspace_cwd: str | Any | None,
    *,
    cap: int = _PIN_CONTENT_CAP,
) -> str:
    """Monta o bloco ``<pinned_files>`` com o conteúdo dos arquivos fixados.

    Lido per-request e injetado no turno do agente (chat.py) para que "fixar"
    realmente mantenha o arquivo no contexto. Defensivo (§11): arquivo ausente,
    binário, grande, fora do workspace (path traversal) ou ilegível é ignorado
    — nunca derruba o turno. Sem pins ou sem workspace → string vazia.
    """
    if not workspace_cwd:
        return ""
    from pathlib import Path

    try:
        pins = await _get_session_pins(thread_id)
    except Exception:
        return ""
    if not pins:
        return ""

    try:
        root = Path(workspace_cwd).resolve()
    except Exception:
        return ""

    blocks: list[str] = []
    for pin in pins:
        try:
            fp = (root / pin).resolve()
            if root != fp and root not in fp.parents:
                continue  # path traversal — fora do workspace
            if not fp.is_file():
                continue
            raw = fp.read_bytes()
            if b"\x00" in raw[:8192]:
                continue  # binário
            text = raw.decode("utf-8", errors="replace")
            if len(text) > cap:
                text = text[:cap] + "\n… (truncado)"
            blocks.append(f'<file path="{pin}">\n{text}\n</file>')
        except Exception:
            logger.debug("pins: ignorando arquivo fixado %r", pin, exc_info=True)
            continue

    if not blocks:
        return ""
    return "<pinned_files>\n" + "\n".join(blocks) + "\n</pinned_files>"


@router.post("/vectora.chat.v1.ThreadService/GetThreadPins")
async def get_thread_pins(request: GetThreadPinsRequest) -> ThreadPinsResponse:
    return ThreadPinsResponse(
        thread_id=request.thread_id,
        pins=await _get_session_pins(request.thread_id),
    )


@router.post("/vectora.chat.v1.ThreadService/SetThreadPins")
async def set_thread_pins(request: SetThreadPinsRequest) -> ThreadPinsResponse:
    pins = await _set_session_pins(request.thread_id, request.pins)
    return ThreadPinsResponse(thread_id=request.thread_id, pins=pins)


# ---------------------------------------------------------------------------
# CreateThread
# ---------------------------------------------------------------------------


@router.post("/vectora.chat.v1.ThreadService/CreateThread")
async def create_thread(body: CreateThreadRequest, http_request: Request) -> Thread:
    """Cria uma nova thread e a associa ao workspace escolhido pelo usuário.

    Quando `workspace_id` vem vazio, a thread nasce sem workspace — o
    backend cria e atribui o workspace dedicado da sessão
    (`~/Documents/vectora/<thread_id>`) na primeira mensagem, em
    `chat.py::_resolve_workspace_id`.
    """
    db = await _get_db()
    thread_id = str(uuid.uuid4())[:8]
    now = datetime.now(UTC).isoformat()
    user_id = _user_id(http_request)

    workspace_id = body.workspace_id
    if workspace_id:
        from backend.workspace.workspace import workspace_registry

        if workspace_registry.get(workspace_id) is not None:
            workspace_registry.set_active(workspace_id, user_id)
        else:
            workspace_id = ""

    session_store = await _get_session_store()
    await session_store.create_session(
        thread_id,
        user_id=user_id,
        workspace_id=workspace_id or None,
        mode=_normalize_mode(body.mode) if body.mode else "code",
    )

    extra = json.dumps({"workspace_id": workspace_id} if workspace_id else {})
    await db.execute(
        """
        INSERT INTO vectora_sessions (thread_id, created_at, last_activity, extra)
        VALUES (?, ?, ?, ?)
        """,
        (thread_id, now, now, extra),
    )
    await db.commit()
    return Thread(
        id=thread_id,
        created_at=now,
        updated_at=now,
        title="",
        workspace_id=workspace_id,
    )


# ---------------------------------------------------------------------------
# GetThread
# ---------------------------------------------------------------------------


async def _assert_owns_thread(thread_id: str, http_request: Request | None) -> None:
    """Confirma em ``SessionStore`` (fonte de verdade sobre posse) que
    ``thread_id``, SE registrada lá, pertence ao usuário autenticado em
    ``http_request``.

    ``http_request`` ausente (chamada interna, sem contexto HTTP) pula a
    checagem — mantém o comportamento pré-existente dos callers internos de
    ``get_thread`` (GetHistory, GenerateTitle, histórico paginado), que já
    resolvem a thread por outros meios. Thread sem registro nenhum em
    ``SessionStore`` (legado, criada antes da posse ser rastreada lá) também
    passa — ausência de registro não é prova de posse alheia. Levanta 404
    (nunca 403) quando HÁ registro e o dono não bate — não distingue
    "não existe" de "não é sua" pro caller, pra não vazar a existência de
    threads de outro usuário."""
    if http_request is None:
        return
    session_store = await _get_session_store()
    session = await session_store.get_session(thread_id)
    if session is None:
        return
    user_id = _user_id(http_request)
    if session["user_id"] != user_id:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id!r} not found")


@router.post("/vectora.chat.v1.ThreadService/GetThread")
async def get_thread(
    request: GetThreadRequest,
    http_request: Request = None,  # ty: ignore[invalid-parameter-default]
) -> Thread:
    db = await _get_db()
    async with db.execute(
        "SELECT thread_id, user_type, created_at, last_activity, message_count, extra, mode, pinned "
        "FROM vectora_sessions WHERE thread_id = ?",
        (request.thread_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Thread {request.thread_id!r} not found"
        )
    await _assert_owns_thread(request.thread_id, http_request)
    return _row_to_thread(row)


# ---------------------------------------------------------------------------
# ListThreads
# ---------------------------------------------------------------------------


@router.post("/vectora.chat.v1.ThreadService/ListThreads")
async def list_threads(
    request: ListThreadsRequest,
    http_request: Request = None,  # ty: ignore[invalid-parameter-default]
) -> ListThreadsResponse:
    """Lista threads visíveis ao usuário, excluindo sessões internas."""
    limit = max(1, min(request.limit or 50, 200))
    db = await _get_db()
    cols = (
        "SELECT thread_id, user_type, created_at, last_activity, message_count, extra, mode, pinned "
        "FROM vectora_sessions "
    )
    mode_filter = _normalize_mode(request.mode) if request.mode else ""
    if mode_filter:
        query = (
            cols + "WHERE mode = ? AND mode != 'subagent' AND message_count > 0 "
            "ORDER BY pinned DESC, last_activity DESC LIMIT ?"
        )
        params: tuple[Any, ...] = (mode_filter, limit)
    else:
        query = (
            cols + "WHERE mode != 'subagent' AND message_count > 0 "
            "ORDER BY pinned DESC, last_activity DESC LIMIT ?"
        )
        params = (limit,)
    async with db.execute(query, params) as cur:
        rows = await cur.fetchall()

    if http_request is not None:
        session_store = await _get_session_store()
        user_id = _user_id(http_request)
        foreign_ids = await session_store.foreign_thread_ids(
            [r[0] for r in rows], user_id
        )
        rows = [r for r in rows if r[0] not in foreign_ids]

    return ListThreadsResponse(threads=[_row_to_thread(r) for r in rows])


async def cleanup_empty_threads(max_age_hours: float = 1.0) -> int:
    """Apaga threads sem nenhuma mensagem, mais antigas que `max_age_hours`.

    Espelha o TTL de 5min do registro client-side (`new-thread-registry.ts`)
    com uma margem generosa — threads "novas" ainda em uso (usuário
    digitando) nunca chegam nem perto de 1h sem a primeira mensagem, então
    o cutoff é seguro. `ListThreads` já filtra `message_count > 0`, então
    isso é higiene do banco, não uma correção de comportamento visível.

    ATENÇÃO — bug real corrigido aqui (2026-08-30): esta função tinha uma
    segunda passada que apagava threads com `message_count > 0` mas sem
    registro na tabela LEGADA `checkpoints` (do antigo grafo compilado,
    substituído pelo motor nativo — `backend/engine/conversation_loop.py`).
    O motor nativo nunca escreve nessa tabela, então TODA thread real virava
    "órfã" pra essa lógica após 1h — confirmado ao vivo num banco de usuário
    real: `vectora_sessions` (o que `ListThreads` lê) com só 2 linhas contra
    dezenas de threads reais e intactas em `sessions.db` (SessionStore, a
    fonte de verdade do motor). Rodando a cada boot + a cada hora
    (`backend/api/server.py::_thread_cleanup_loop`), isso apagava
    silenciosamente qualquer conversa real da sidebar assim que completasse
    1h de idade — nunca perda de dados (o histórico seguia intacto em
    `sessions.db`), mas a thread "sumia" pro usuário sem nenhum sinal de
    erro. Removida por completo — não existe hoje nenhum sinal confiável
    equivalente pra "thread órfã" no motor nativo (checar contra
    `sessions.db` exigiria uma segunda conexão cross-database); se esse
    edge case (message_count incrementado sem o agente ter rodado) voltar a
    aparecer, a correção certa é investigar `stream_chat`/`_increment_message_count`
    diretamente, não reintroduzir uma checagem contra uma tabela morta.
    """
    from datetime import timedelta

    cutoff = (datetime.now(UTC) - timedelta(hours=max_age_hours)).isoformat()
    db = await _get_db()
    cur = await db.execute(
        "DELETE FROM vectora_sessions WHERE message_count = 0 AND created_at < ?",
        (cutoff,),
    )
    await db.commit()
    deleted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    if deleted:
        logger.info("threads: %d thread(s) vazia(s) removida(s) por hygiene", deleted)
    return deleted


async def reconcile_vectora_sessions() -> int:
    """Repovoa `vectora_sessions` (o que a sidebar lê) a partir de
    `sessions.db`/`SessionStore` (fonte de verdade do motor nativo) — rede
    de segurança contra qualquer divergência entre as duas tabelas, seja
    qual for a causa (upsert que falhou, binário antigo com um bug já
    corrigido, corrida de processos, etc.).

    Acionada tanto pela recuperação manual de dados quanto pelo
    `_thread_cleanup_loop` periódico (`backend/api/server.py`) — mesma
    rotina, um jeito só de reconciliar. Idempotente: rodar de novo sobre um
    banco já sincronizado não faz nada.

    Para cada thread real em `sessions.db` com pelo menos 1 mensagem:
    - Ausente de `vectora_sessions` → cria (repovoa a visibilidade na sidebar).
    - Presente mas com `message_count` divergente do real → corrige a contagem.
    - `workspace_id` ausente/divergente de `extra` → mescla (nunca apaga
      título/pins já gravados — usa `json_patch`, só adiciona/atualiza a
      chave `workspace_id`). Sem isso, uma thread repovoada aparecia na
      sidebar sem o workspace correto (achado real, 2026-09-04 — o INSERT
      gravava `extra` fixo como `'{}'`, nunca lendo `sessions.workspace_id`).
    Nunca apaga nada — só preenche o que está faltando ou errado.
    """
    session_store = await _get_session_store()
    real_sessions = await session_store.list_all_sessions()
    real_with_messages = [
        s for s in real_sessions if s["message_count"] > 0 and s["mode"] != "subagent"
    ]
    if not real_with_messages:
        return 0

    db = await _get_db()
    thread_ids = [s["thread_id"] for s in real_with_messages]
    placeholders = ",".join("?" for _ in thread_ids)

    # Lock cobrindo a seção crítica inteira (leitura do tombstone → upserts →
    # commit) — sem isso, os vários `await` entre a leitura de
    # `deleted_threads` e o commit final dão espaço pra `delete_thread`
    # (mesma conexão) intercalar: gravar o tombstone e apagar a thread bem no
    # meio deste loop, que então reinsere a thread usando o instantâneo já
    # obsoleto (ver `_reconcile_delete_lock`).
    async with _reconcile_delete_lock:
        async with db.execute(
            f"SELECT thread_id, message_count, extra FROM vectora_sessions WHERE thread_id IN ({placeholders})",  # noqa: S608  # nosec B608
            thread_ids,
        ) as cur:
            existing = {row[0]: (row[1], row[2]) for row in await cur.fetchall()}
        async with db.execute(
            f"SELECT thread_id FROM deleted_threads WHERE thread_id IN ({placeholders})",  # noqa: S608  # nosec B608
            thread_ids,
        ) as cur:
            tombstoned = {row[0] for row in await cur.fetchall()}

        reconciled = 0
        for session in real_with_messages:
            thread_id = session["thread_id"]
            if thread_id in tombstoned:
                continue
            real_count = session["message_count"]
            real_workspace_id = session["workspace_id"] or ""
            current_count, current_extra_json = existing.get(thread_id, (None, None))
            try:
                current_workspace_id = json.loads(current_extra_json or "{}").get(
                    "workspace_id", ""
                )
            except Exception:
                current_workspace_id = ""
            # Diverge tanto quando SessionStore tem um workspace que `extra`
            # ainda não reflete QUANTO quando SessionStore não tem workspace
            # nenhum mas `extra` guarda um valor obsoleto (thread que perdeu o
            # workspace, ou um valor stale de antes desta correção).
            workspace_diverges = current_workspace_id != real_workspace_id
            if current_count == real_count and not workspace_diverges:
                continue

            mode_col = _normalize_mode(session["mode"])
            # Dois statements, não um só INSERT...ON CONFLICT com extra
            # calculado em VALUES: um `null` de verdade em `patch_json` (pra
            # REMOVER workspace_id obsoleto via JSON Merge Patch, RFC 7396, é
            # o que `json_patch` do SQLite implementa) não pode virar o
            # `extra` de uma linha NOVA — `Thread.workspace_id` é `str`
            # estrito no schema (nunca `str | None`); um
            # `{"workspace_id": null}` literal ali quebraria a validação
            # Pydantic na próxima leitura. INSERT sempre nasce com extra
            # limpo (nunca null); o UPDATE seguinte — sempre executado,
            # idempotente por PK — aplica o merge-patch de verdade
            # (populando ou removendo a chave conforme o caso).
            #
            # `INSERT ... ON CONFLICT DO NOTHING` nunca lança em PK
            # duplicada, e o UPDATE por thread_id é seguro mesmo repetido —
            # a exclusão mútua acima é que impede a reconciliação e a
            # exclusão de intercalarem entre si.
            await db.execute(
                """
                INSERT INTO vectora_sessions
                    (thread_id, created_at, last_activity, message_count, extra, mode)
                VALUES (?, ?, ?, ?, '{}', ?)
                ON CONFLICT(thread_id) DO NOTHING
                """,
                (
                    thread_id,
                    session["created_at"],
                    session["updated_at"],
                    real_count,
                    mode_col,
                ),
            )
            patch_json = json.dumps({"workspace_id": real_workspace_id or None})
            # `json_valid` normaliza um `extra` corrompido pra `'{}'` antes do
            # merge — senão `json_patch` lança em JSON malformado e derruba o
            # lote inteiro antes do commit.
            await db.execute(
                """
                UPDATE vectora_sessions
                SET message_count = ?,
                    extra = json_patch(
                        CASE WHEN json_valid(extra) THEN extra ELSE '{}' END,
                        ?
                    )
                WHERE thread_id = ?
                """,
                (real_count, patch_json, thread_id),
            )
            reconciled += 1

        if reconciled:
            await db.commit()
            logger.info(
                "threads: %d thread(s) real(is) repovoada(s)/corrigida(s) em vectora_sessions",
                reconciled,
            )
    return reconciled


# ---------------------------------------------------------------------------
# DeleteThread
# ---------------------------------------------------------------------------


@router.post("/vectora.chat.v1.ThreadService/DeleteThread")
async def delete_thread(
    request: DeleteThreadRequest,
    http_request: Request = None,  # ty: ignore[invalid-parameter-default]
) -> dict:
    await _assert_owns_thread(request.thread_id, http_request)
    db = await _get_db()
    # Grava o tombstone ANTES de qualquer exclusão: fecha a corrida onde
    # uma reconciliação concorrente já tinha lido `list_all_sessions()`
    # antes desta chamada (ainda vendo a thread) e só escreveria de volta
    # em `vectora_sessions` DEPOIS — sem o tombstone já presente nesse
    # momento, esse UPSERT tardio ressuscitaria a thread recém-apagada.
    # `INSERT OR REPLACE` também cobre o caso raro de reexcluir uma thread
    # cujo tombstone já existisse (não deve acontecer, mas não é erro).
    #
    # Mesmo `_reconcile_delete_lock` de `reconcile_vectora_sessions`: as duas
    # rotinas escrevem na mesma conexão (`_db_conn`), então sem essa exclusão
    # mútua os `await` entre as três chamadas abaixo dão espaço pra uma
    # reconciliação em andamento intercalar leitura/escrita com este bloco.
    async with _reconcile_delete_lock:
        await db.execute(
            "INSERT OR REPLACE INTO deleted_threads (thread_id, deleted_at) VALUES (?, ?)",
            (request.thread_id, datetime.now(UTC).isoformat()),
        )
        await db.execute(
            "DELETE FROM vectora_sessions WHERE thread_id = ?",
            (request.thread_id,),
        )
        await db.commit()
    # Precisa apagar também de `sessions.db` (SessionStore) — é a fonte de
    # verdade que `reconcile_vectora_sessions` usa pra repovoar a sidebar;
    # sem isso, a próxima rodada de reconciliação (boot ou hora em hora)
    # via `_thread_cleanup_loop` encontra a thread ainda viva ali e a
    # recria em `vectora_sessions`, ressuscitando uma conversa apagada.
    session_store = await _get_session_store()
    await session_store.delete_session(request.thread_id)
    return {}


# ---------------------------------------------------------------------------
# UpdateThread
# ---------------------------------------------------------------------------


@router.post("/vectora.chat.v1.ThreadService/UpdateThread")
async def update_thread(
    request: UpdateThreadRequest,
    http_request: Request = None,  # ty: ignore[invalid-parameter-default]
) -> Thread:
    """Atualiza metadados (title/pinned) de uma thread existente.

    Cada campo só é alterado quando enviado (não-``None``) — permite
    atualizações parciais (ex.: só fixar, sem tocar no título).
    """
    await _assert_owns_thread(request.thread_id, http_request)
    db = await _get_db()
    async with db.execute(
        "SELECT thread_id, user_type, created_at, last_activity, message_count, extra, mode, pinned "
        "FROM vectora_sessions WHERE thread_id = ?",
        (request.thread_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Thread {request.thread_id!r} not found"
        )
    thread = _row_to_thread(row)
    try:
        extra = json.loads(row[5] or "{}")
    except Exception:
        extra = {}
    if request.title is not None:
        extra["title"] = request.title
    pinned = thread.pinned if request.pinned is None else request.pinned
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "UPDATE vectora_sessions SET extra = ?, pinned = ?, last_activity = ? "
        "WHERE thread_id = ?",
        (json.dumps(extra), int(pinned), now, request.thread_id),
    )
    await db.commit()
    return Thread(
        id=thread.id,
        created_at=thread.created_at,
        updated_at=now,
        title=extra.get("title", ""),
        workspace_id=thread.workspace_id,
        mode=thread.mode,
        pinned=pinned,
    )


# ---------------------------------------------------------------------------
# GetHistory
# ---------------------------------------------------------------------------


@router.post("/vectora.chat.v1.ThreadService/GetHistory")
async def get_history(request: GetHistoryRequest) -> GetHistoryResponse:
    """Retorna o histórico de mensagens de uma thread via ``SessionStore`` nativo.

    Reusa o singleton do agente (mesmo que o handler de chat) — evita rebuild
    do grafo + abertura de uma nova connection SQLite a cada request.
    """
    try:
        from backend.services import agent_factory

        thread = await get_thread(GetThreadRequest(thread_id=request.thread_id))
        pairs = await agent_factory.aget_thread_messages(
            request.thread_id,
            workspace_id=thread.workspace_id or None,
        )
        history = [
            HistoryMessage(role=role, content=text, checkpoint_id=checkpoint_id)
            for role, text, checkpoint_id, _att in pairs
        ]
        todos = await agent_factory.aget_thread_todos(
            request.thread_id,
            workspace_id=thread.workspace_id or None,
        )
        return GetHistoryResponse(
            messages=history,
            todos=[TodoItem.model_validate(todo) for todo in todos],
        )

    except Exception as exc:
        logger.exception("api/threads: erro ao carregar histórico")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GenerateTitle — título da sessão atribuído pela IA (uma vez, no 1º turno)
# ---------------------------------------------------------------------------


async def _ai_title(user_text: str, assistant_text: str) -> str:
    """Gera um título curto (≤6 palavras) para a sessão via LLM.

    Defensivo: qualquer falha do modelo cai no fallback (primeiras palavras da
    mensagem do usuário), nunca propaga. Sem pontuação final, sem aspas.
    """
    fallback = " ".join(user_text.split()[:6]).strip(" .\"'") or "Nova conversa"
    try:
        from backend.services.utils import load_native_llm
        from backend.vtypes.message import MessageRole, text_message

        llm = load_native_llm()
        prompt = (
            "Gere um título curto (no máximo 6 palavras, sem aspas e sem ponto "
            "final) que resuma o tema desta conversa, no mesmo idioma do "
            "usuário.\n\n"
            f"Usuário: {user_text[:500]}\n"
            f"Assistente: {assistant_text[:500]}\n\n"
            "Título:"
        )
        resp = await llm.agenerate(
            [
                text_message(
                    MessageRole.SYSTEM, "Você nomeia conversas de forma concisa."
                ),
                text_message(MessageRole.USER, prompt),
            ]
        )
        raw = resp.text()
        title = raw.strip().strip("\"'").splitlines()[0].strip()
        # Limita a 6 palavras e remove pontuação final.
        title = " ".join(title.split()[:6]).strip(" .\"'")
        return title or fallback
    except Exception:
        logger.warning("api/threads: falha ao gerar título via LLM; usando fallback")
        return fallback


@router.post("/vectora.chat.v1.ThreadService/GenerateTitle")
async def generate_title(
    request: GenerateTitleRequest,
    http_request: Request = None,  # ty: ignore[invalid-parameter-default]
) -> GenerateTitleResponse:
    """Atribui (uma vez) um título gerado pela IA à sessão e persiste.

    Idempotente: se a sessão já tem título, devolve o existente sem nova chamada
    de LLM. Caso contrário, lê o 1º par usuário/assistente do histórico, gera o
    título e grava em ``vectora_sessions``.
    """
    await _assert_owns_thread(request.thread_id, http_request)
    try:
        # Já tem título? Não regenera (título é estável após o 1º turno).
        db = await _get_db()
        async with db.execute(
            "SELECT extra FROM vectora_sessions WHERE thread_id = ?",
            (request.thread_id,),
        ) as cur:
            row = await cur.fetchone()
        if row:
            try:
                existing = json.loads(row[0] or "{}").get("title", "")
            except Exception:
                existing = ""
            if existing:
                return GenerateTitleResponse(title=existing)

        from backend.services import agent_factory

        thread = await get_thread(GetThreadRequest(thread_id=request.thread_id))
        pairs = await agent_factory.aget_thread_messages(
            request.thread_id,
            workspace_id=thread.workspace_id or None,
        )
        user_text = next((t for r, t, _cp, _att in pairs if r == "human"), "")
        assistant_text = next((t for r, t, _cp, _att in pairs if r == "assistant"), "")
        if not user_text:
            return GenerateTitleResponse(title="")

        title = await _ai_title(user_text, assistant_text)
        await _upsert_session(request.thread_id, title=title)
        return GenerateTitleResponse(title=title)

    except Exception as exc:
        logger.exception("api/threads: erro ao gerar título")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Rewind — schema + endpoints REST
# ---------------------------------------------------------------------------


class CheckpointArtifact(BaseModel):
    """Metadados de um snapshot de rewind gravado para uma thread."""

    id: str
    thread_id: str
    checkpoint_id: str
    strategy: str
    git_sha: str | None
    snapshot_path: str | None
    files_touched: list[str]
    created_at: str


class CheckpointsResponse(BaseModel):
    checkpoints: list[CheckpointArtifact]


class RewindRequest(BaseModel):
    checkpoint_id: str
    # `id` da mensagem no SessionStore (ver HistoryMessage.checkpoint_id/
    # Message.checkpointId), alvo de SessionStore.set_branch_head — trunca
    # a conversa junto com os arquivos do workspace. Opcional: mantém
    # compatibilidade com clientes antigos que só revertiam arquivos.
    message_checkpoint_id: str = ""


class RewindResponse(BaseModel):
    status: str
    message: str = ""


@router.get("/threads/{thread_id}/checkpoints", response_model=CheckpointsResponse)
async def list_thread_checkpoints(thread_id: str) -> CheckpointsResponse:
    """Lista os checkpoints de rewind gravados para uma thread.

    Retorna apenas artefatos com ``strategy='git'`` ou ``strategy='snapshot'``
    associados a turnos completos (gravados pelo orchestrator após cada turno).
    A filtragem por ``kind='turn'`` é feita pelo orchestrator ao gravar —
    aqui lemos apenas o que está em
    ``vectora_checkpoint_artifacts``.
    """
    db = await _get_db()
    async with db.execute(
        "SELECT id, thread_id, checkpoint_id, strategy, git_sha, snapshot_path, "
        "files_touched, created_at "
        "FROM vectora_checkpoint_artifacts "
        "WHERE thread_id = ? ORDER BY created_at DESC",
        (thread_id,),
    ) as cur:
        rows = await cur.fetchall()

    return CheckpointsResponse(
        checkpoints=[
            CheckpointArtifact(
                id=r[0],
                thread_id=r[1],
                checkpoint_id=r[2],
                strategy=r[3],
                git_sha=r[4],
                snapshot_path=r[5],
                files_touched=json.loads(r[6] or "[]"),
                created_at=r[7],
            )
            for r in rows
        ]
    )


@router.post("/threads/{thread_id}/rewind", response_model=RewindResponse)
async def rewind_thread(
    thread_id: str,
    body: RewindRequest,
    workspace_id: Annotated[str, Query()] = "",
) -> RewindResponse:
    """Restaura o workspace para o estado do checkpoint indicado.

    Requer que o ``workspace_id`` seja passado via query param (ou seja
    encontrado no banco pela thread) e que o workspace seja um repositório git.
    O mutex do workspace é adquirido durante a restauração — bloqueia escritas
    concorrentes de tools. Retorna 409 se o workspace estiver ocupado.

    Passos:
    1. Busca o artefato pelo ``checkpoint_id`` na tabela.
    2. Obtém o workspace via registry.
    3. Adquire ``acquire_workspace_lock(workspace_id, thread_id)``.
    4. Chama ``restore_git_checkpoint(repo, git_sha)``.
    """
    db = await _get_db()
    async with db.execute(
        "SELECT id, git_sha, snapshot_path, strategy "
        "FROM vectora_checkpoint_artifacts "
        "WHERE thread_id = ? AND checkpoint_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (thread_id, body.checkpoint_id),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Checkpoint {body.checkpoint_id!r} não encontrado para a thread.",
        )

    _artifact_id, git_sha, _snapshot_path, strategy = row

    # Resolve workspace: query param > banco
    wid = workspace_id or ""
    if not wid:
        async with db.execute(
            "SELECT extra FROM vectora_sessions WHERE thread_id = ?",
            (thread_id,),
        ) as cur2:
            session_row = await cur2.fetchone()
        if session_row:
            try:
                wid = json.loads(session_row[0] or "{}").get("workspace_id", "")
            except Exception:
                wid = ""

    if not wid:
        raise HTTPException(
            status_code=422,
            detail="workspace_id é obrigatório para o rewind (passe via query param).",
        )

    from backend.workspace.workspace import (
        WorkspaceLockTimeoutError,
        acquire_workspace_lock,
        workspace_registry,
    )

    ws = workspace_registry.get(wid)
    if ws is None:
        raise HTTPException(
            status_code=404, detail=f"Workspace {wid!r} não encontrado."
        )

    if strategy == "git":
        if not git_sha:
            raise HTTPException(
                status_code=422, detail="Artefato de checkpoint sem git_sha."
            )
        try:
            import git as gitpy

            repo = gitpy.Repo(ws.cwd, search_parent_directories=True)
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Não é um repositório git: {exc}"
            ) from exc

        try:
            async with acquire_workspace_lock(wid, thread_id, timeout=5.0):
                from backend.persistence.checkpoint import restore_git_checkpoint

                result = restore_git_checkpoint(repo, git_sha)
        except WorkspaceLockTimeoutError as lock_exc:
            raise HTTPException(
                status_code=409,
                detail="Workspace ocupado por outra operação — tente novamente em instantes.",
            ) from lock_exc
        if result["status"] != "ok":
            raise HTTPException(
                status_code=500, detail=result.get("message", "Falha no restore.")
            )
    elif strategy == "snapshot":
        if not _snapshot_path:
            raise HTTPException(
                status_code=422,
                detail="Artefato de checkpoint sem snapshot_path.",
            )
        try:
            async with acquire_workspace_lock(wid, thread_id, timeout=5.0):
                from backend.persistence.checkpoint import restore_snapshot_checkpoint

                result = restore_snapshot_checkpoint(_snapshot_path, ws.cwd)
        except WorkspaceLockTimeoutError as lock_exc:
            raise HTTPException(
                status_code=409,
                detail="Workspace ocupado por outra operação — tente novamente em instantes.",
            ) from lock_exc
        if result["status"] != "ok":
            raise HTTPException(
                status_code=500, detail=result.get("message", "Falha no restore.")
            )
    else:
        raise HTTPException(
            status_code=422,
            detail=f"Estratégia de checkpoint {strategy!r} ainda não suportada pelo rewind.",
        )

    # Trunca a conversa junto com os arquivos: reaponta a branch ativa da
    # thread pra mensagem alvo (mesmo mecanismo de fork usado por editar/
    # regenerar, SessionStore.set_branch_head) — não apaga nada (append-
    # only), só faz `get_history` parar de devolver o que vem depois.
    # Best-effort: se falhar, os arquivos já foram restaurados com sucesso
    # (não desfaz isso) — só loga, não derruba a resposta.
    if body.message_checkpoint_id:
        try:
            from backend.services import agent_factory

            store = await agent_factory.get_session_store()
            await store.set_branch_head(thread_id, int(body.message_checkpoint_id))
        except Exception:
            logger.exception(
                "rewind_thread: falha ao truncar histórico da conversa "
                "(arquivos já restaurados) thread=%s message_checkpoint_id=%s",
                thread_id,
                body.message_checkpoint_id,
            )

    return RewindResponse(status="ok")


# ---------------------------------------------------------------------------
# Activity endpoint: arquivos tocados + resumo de tool calls
MESSAGES_CAP = 200


@router.get("/threads/{thread_id}/history", response_model=PagedHistoryResponse)
async def get_thread_history_paginated(
    thread_id: str,
    limit: int = MESSAGES_CAP,
    offset: int = 0,
) -> PagedHistoryResponse:
    """Histórico paginado de mensagens de uma thread.

    Retorna as ``limit`` mensagens mais recentes (excluindo as ``offset`` mais
    recentes), em ordem cronológica. ``has_more=True`` quando existem mensagens
    mais antigas além das retornadas.
    """
    try:
        from backend.services import agent_factory

        thread = await get_thread(GetThreadRequest(thread_id=thread_id))
        pairs = await agent_factory.aget_thread_messages(
            thread_id,
            workspace_id=thread.workspace_id or None,
        )
    except Exception:
        logger.exception("api/threads: erro ao carregar histórico paginado")
        return PagedHistoryResponse(messages=[], has_more=False, total_count=0)

    total = len(pairs)
    effective_limit = min(limit, MESSAGES_CAP)

    # Fatia: pega as [effective_limit] mensagens anteriores às [offset] mais recentes
    # Índice do fim da janela (a partir do final da lista)
    end = total - offset
    start = max(0, end - effective_limit)
    page = pairs[start:end]

    has_more = start > 0

    messages = [
        HistoryMessage(
            role=role, content=text, checkpoint_id=checkpoint_id, attachments=att
        )
        for role, text, checkpoint_id, att in page
    ]

    if offset == 0:
        try:
            from backend.persistence.kv import get_kv

            kv = await get_kv()
            partial_text = await kv.get(f"partial:{thread_id}")
            if partial_text:
                # Usa um checkpoint_id falso (ex: "partial") p/ indicar que não está na DB
                messages.append(
                    HistoryMessage(
                        role="assistant", content=partial_text, checkpoint_id="partial"
                    )
                )
        except Exception:
            # Best-effort (preview de resposta em andamento) — nunca deve
            # derrubar a resposta da página de histórico, mas engolir sem
            # log nenhum viola CLAUDE.md §11 e escondia exatamente o tipo
            # de falha (NATS indisponível) que motivou a investigação real
            # de 2026-08-30.
            logger.warning(
                "threads/history: falha ao ler partial:%s do KV — preview de "
                "resposta em andamento não será mostrado",
                thread_id,
                exc_info=True,
            )

    return PagedHistoryResponse(
        messages=messages,
        has_more=has_more,
        total_count=total,
    )


@router.get("/threads/{thread_id}/attachments/{filename}")
async def get_thread_attachment(thread_id: str, filename: str) -> FileResponse:
    """Serve um anexo de imagem persistido por `_persist_image_attachment`
    (`chat.py`) — `attachments[].url` no histórico aponta pra cá. Sanitiza
    os dois segmentos (sem `..`/separador) antes de tocar o filesystem."""
    from backend.settings import settings

    safe_thread = thread_id.replace("/", "").replace("\\", "").replace("..", "")
    safe_filename = filename.replace("/", "").replace("\\", "").replace("..", "")
    path = settings.vectora_home / "chat-attachments" / safe_thread / safe_filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Anexo não encontrado.")
    return FileResponse(path)


# ---------------------------------------------------------------------------


class ActivityResponse(BaseModel):
    files_touched: list[str]
    tool_call_counts: dict[str, int]
    turn_count: int


@router.get("/threads/{thread_id}/activity", response_model=ActivityResponse)
async def thread_activity(thread_id: str) -> ActivityResponse:
    """Retorna um resumo da atividade da thread: arquivos modificados e
    contagem de tool calls agrupados por nome.

    Consolida ``files_touched`` de todos os checkpoints de turno da thread.
    """
    db = await _get_db()

    # Coleta files_touched de todos os checkpoints da thread
    async with db.execute(
        "SELECT files_touched FROM vectora_checkpoint_artifacts WHERE thread_id = ?",
        (thread_id,),
    ) as cur:
        ft_rows = await cur.fetchall()

    all_files: list[str] = []
    for (ft_json,) in ft_rows:
        with contextlib.suppress(Exception):
            all_files.extend(json.loads(ft_json or "[]"))
    unique_files = sorted(set(all_files))

    # Contagem de turnos (número de checkpoints)
    async with db.execute(
        "SELECT COUNT(*) FROM vectora_checkpoint_artifacts WHERE thread_id = ?",
        (thread_id,),
    ) as cur:
        turn_count_row = await cur.fetchone()
    turn_count: int = turn_count_row[0] if turn_count_row else 0

    # tool_call_counts: derivado de files_touched por convenção (sem acesso
    # ao histórico completo de tool calls aqui).
    return ActivityResponse(
        files_touched=unique_files,
        tool_call_counts={},
        turn_count=turn_count,
    )


class PendingInterruptResponse(BaseModel):
    interrupt: HITLEvent | None = None


@router.get(
    "/threads/{thread_id}/pending-interrupt", response_model=PendingInterruptResponse
)
async def thread_pending_interrupt(
    thread_id: str, workspace_id: str | None = None
) -> PendingInterruptResponse:
    """Reidrata o HITLPanel após um reload de página.

    O interrupt sobrevive a um restart do backend (checkpointer real, sem
    ``MemorySaver``), mas a UI só o mostra quando chega via streaming — um
    F5 no meio de uma pausa HITL perdia o card até o usuário mandar mensagem
    nova. Chamado pelo frontend ao montar a sessão.
    """
    from backend.services.agent_factory import aget_thread_pending_interrupt

    pending = await aget_thread_pending_interrupt(thread_id, workspace_id)
    if pending is None:
        return PendingInterruptResponse(interrupt=None)

    pre_approved = False
    with contextlib.suppress(Exception):
        from backend.services.smart_approval import evaluate_command

        pre_approved = await evaluate_command(
            pending["tool_name"], pending["args"], workspace_id=workspace_id or ""
        )

    return PendingInterruptResponse(
        interrupt=HITLEvent(
            tool_name=pending["tool_name"],
            args_json=json.dumps(pending["args"]),
            interrupt_id=pending["interrupt_id"],
            pre_approved=pre_approved,
        )
    )


# ---------------------------------------------------------------------------
# Aprovação inteligente — allowlist persistente por workspace
# ---------------------------------------------------------------------------


class SmartApprovalAllowlistRequest(BaseModel):
    workspace_id: str
    tool_name: str
    args: dict = {}


class SmartApprovalAllowlistRemoveRequest(BaseModel):
    workspace_id: str
    signature: str


class SmartApprovalAllowlistResponse(BaseModel):
    allowlist: list[str]


@router.post("/smart-approval/allowlist")
async def add_smart_approval_allowlist(
    body: SmartApprovalAllowlistRequest, request: Request
) -> SmartApprovalAllowlistResponse:
    """ "Sempre permitir isso" no HITLPanel — não muda o HITL em si, só faz a
    próxima ocorrência do mesmo comando chegar já marcada como reconhecida
    (ver `backend/services/smart_approval.py`)."""
    _user_id(request)
    from backend.services.smart_approval import add_to_allowlist

    try:
        allowlist = add_to_allowlist(body.workspace_id, body.tool_name, body.args)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SmartApprovalAllowlistResponse(allowlist=allowlist)


@router.delete("/smart-approval/allowlist")
async def remove_smart_approval_allowlist(
    body: SmartApprovalAllowlistRemoveRequest, request: Request
) -> SmartApprovalAllowlistResponse:
    """Revoga uma assinatura — a próxima ocorrência volta a exigir aprovação
    normal, sem o atalho visual."""
    _user_id(request)
    from backend.services.smart_approval import remove_from_allowlist

    allowlist = remove_from_allowlist(body.workspace_id, body.signature)
    return SmartApprovalAllowlistResponse(allowlist=allowlist)
