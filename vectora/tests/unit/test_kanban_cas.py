"""Testes unitários para concorrência CAS e atomicidade transacional no Kanban."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import aiosqlite
import pytest

from backend.scheduling import kanban
from backend.scheduling.kanban import (
    add_dependency,
    approve_review,
    get_task_status,
    manual_transition,
    recompute_ready,
)


@pytest.fixture
async def db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[aiosqlite.Connection]:
    """Banco SQLite isolado com schema do Kanban."""
    caminho = tmp_path / "kanban_cas.db"
    conn = await aiosqlite.connect(caminho)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(
        """
        CREATE TABLE vectora_background_tasks (
            id             TEXT PRIMARY KEY,
            session_id     TEXT NOT NULL,
            workspace_id   TEXT,
            user_id        TEXT NOT NULL,
            kind           TEXT NOT NULL,
            name           TEXT NOT NULL,
            instruction    TEXT NOT NULL,
            trigger_type   TEXT NOT NULL,
            trigger_config TEXT NOT NULL DEFAULT '{}',
            enabled        INTEGER NOT NULL DEFAULT 1,
            last_run_at    TEXT,
            next_run_at    TEXT,
            status         TEXT NOT NULL DEFAULT 'todo',
            block_kind     TEXT,
            block_reason   TEXT,
            claim_lock     TEXT,
            claim_expires_at TEXT,
            block_count    INTEGER NOT NULL DEFAULT 0,
            board_id       TEXT,
            version        INTEGER NOT NULL DEFAULT 1,
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE vectora_task_links (
            parent_id  TEXT NOT NULL,
            child_id   TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (parent_id, child_id)
        );
        CREATE TABLE vectora_task_comments (
            id         TEXT PRIMARY KEY,
            task_id    TEXT NOT NULL,
            user_id    TEXT NOT NULL,
            body       TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE vectora_task_events (
            id           TEXT PRIMARY KEY,
            task_id      TEXT NOT NULL,
            from_status  TEXT,
            to_status    TEXT NOT NULL,
            block_kind   TEXT,
            block_reason TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    await conn.commit()

    async def _get_db() -> aiosqlite.Connection:
        """Executa a operação  get db."""
        return conn

    monkeypatch.setattr(kanban, "_get_db", _get_db)
    yield conn
    await conn.close()


async def _insere_task(
    db: aiosqlite.Connection,
    task_id: str,
    status: str = "todo",
    updated_at: str = "2026-10-20T10:00:00",
    version: int = 1,
) -> None:
    """Executa a operação  insere task."""
    await db.execute(
        "INSERT INTO vectora_background_tasks "
        "(id, session_id, user_id, kind, name, instruction, trigger_type, status, updated_at, version) "
        "VALUES (?, 's1', 'u1', 'subagent', 'Task', 'inst', 'manual', ?, ?, ?)",
        (task_id, status, updated_at, version),
    )
    await db.commit()


@pytest.mark.asyncio
async def test_manual_transition_with_expected_status(
    db: aiosqlite.Connection,
) -> None:
    """Verifica o comportamento de manual transition with expected status."""
    await _insere_task(db, "t1", status="todo")

    # Transição com expected_status correspondente
    await manual_transition("t1", "ready", expected_status="todo")
    st = await get_task_status("t1")
    assert st["status"] == "ready"

    # Tentativa com expected_status divergente (simulando alteração concorrente)
    with pytest.raises(
        ValueError, match="concorrência detectada: status atual 'ready'"
    ):
        await manual_transition("t1", "triage", expected_status="todo")


@pytest.mark.asyncio
async def test_manual_transition_with_expected_version(
    db: aiosqlite.Connection,
) -> None:
    """Verifica o comportamento de manual transition with expected version."""
    await _insere_task(db, "t2", status="todo", version=1)

    # Versão esperada bate com a versão monotônica
    await manual_transition("t2", "ready", expected_version=1)
    st = await get_task_status("t2")
    assert st["status"] == "ready"
    assert st["version"] == 2

    # Tentativa com versão antiga deve falhar por conflito de versão
    with pytest.raises(ValueError, match="concorrência detectada: versão atual"):
        await manual_transition("t2", "triage", expected_version=1)


@pytest.mark.asyncio
async def test_manual_transition_cas_write_time_collision(
    db: aiosqlite.Connection,
) -> None:
    """Verifica o comportamento de manual transition cas write time collision."""
    await _insere_task(db, "t_cas", status="todo", version=1)

    # Chamada com versão desatualizada no momento da escrita dispara conflito de concorrência
    with pytest.raises(
        ValueError,
        match="concorrência detectada: registro foi modificado por outra operação",
    ):
        await kanban.set_status("t_cas", "ready", expected_version=99)


@pytest.mark.asyncio
async def test_add_dependency_cycle_detection(
    db: aiosqlite.Connection,
) -> None:
    """Verifica o comportamento de add dependency cycle detection."""
    await _insere_task(db, "parent1", status="todo")
    await _insere_task(db, "child1", status="todo")

    await add_dependency("parent1", "child1")

    # Tentativa de criar ciclo child1 -> parent1
    with pytest.raises(ValueError, match="dependência circular"):
        await add_dependency("child1", "parent1")


@pytest.mark.asyncio
async def test_approve_review_atomic(db: aiosqlite.Connection) -> None:
    """Verifica o comportamento de approve review atomic."""
    await _insere_task(db, "rev1", status="review")

    await approve_review("rev1", user_id="approver1")

    st = await get_task_status("rev1")
    assert st["status"] == "done"

    # Verifica se o comentário foi inserido na mesma transação
    async with db.execute(
        "SELECT body, user_id FROM vectora_task_comments WHERE task_id = ?",
        ("rev1",),
    ) as cur:
        comments = list(await cur.fetchall())
        assert len(comments) == 1
        assert comments[0]["body"] == "✓ Review aprovada."
        assert comments[0]["user_id"] == "approver1"


@pytest.mark.asyncio
async def test_recompute_ready_atomic_promotion(
    db: aiosqlite.Connection,
) -> None:
    """Verifica o comportamento de recompute ready atomic promotion."""
    await _insere_task(db, "dep_parent", status="done")
    await _insere_task(db, "dep_child", status="todo")
    await add_dependency("dep_parent", "dep_child")

    promoted = await recompute_ready()
    assert promoted == 1

    st = await get_task_status("dep_child")
    assert st["status"] == "ready"
