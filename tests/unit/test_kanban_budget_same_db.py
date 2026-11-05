"""Regressão: `kanban._get_db`/`budget._get_db` precisam apontar pro MESMO
banco que `background_tasks._get_db` (onde `schema.sql` de fato cria
`vectora_background_tasks`), não pro `checkpoints.db` de threads.

Achado ao vivo: `kanban.py`/`budget.py` importavam `_get_db` de
`backend.api.handlers.threads`, um banco sem essas tabelas — todo tick do
scheduler quebrava com `sqlite3.OperationalError: no such table:
vectora_background_tasks`, silenciado pelo try/except do `tick()`. Os
testes de `test_kanban_model.py`/`test_task_budget.py` nunca pegaram isso
porque suas fixtures fazem `monkeypatch.setattr(kanban, "_get_db", ...)`
diretamente — mascarando exatamente a divergência de arquivo que só existe
quando `_get_db` roda sem monkeypatch, como em produção.

Este teste **não** monkeypatcha `kanban._get_db`/`budget._get_db` — só
`settings.db_dsn`, exatamente como o backend real faz — e prova que os
três módulos convergem sozinhos pro mesmo arquivo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import backend

_SCHEMA = (
    Path(backend.__file__).parent / "storage" / "migrations" / "sqlite" / "schema.sql"
)


@pytest.fixture
async def db_real(tmp_path, monkeypatch):
    """Aplica schema.sql no arquivo que `settings.db_dsn` aponta — igual
    `run_migrations` faz no boot real — sem tocar em `_get_db` de nenhum
    módulo do Kanban/budget."""
    import aiosqlite

    from backend.settings import settings

    db_path = tmp_path / "backend.db"
    monkeypatch.setattr(settings, "db_dsn", str(db_path))

    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
    await conn.commit()
    await conn.close()

    # threads.py aponta pro checkpoints.db — banco de verdade diferente,
    # sem `vectora_background_tasks`. Não tocar aqui é o ponto do teste:
    # provar que kanban/budget não passam por ele mais.
    return db_path


@pytest.mark.asyncio
async def test_kanban_e_budget_convergem_pro_banco_de_background_tasks(db_real):
    from backend.scheduling import background_tasks as bg
    from backend.scheduling import budget, kanban

    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="A",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )

    # kanban._get_db, sem monkeypatch nenhum — precisa achar a mesma tabela
    # que create_task acabou de escrever.
    assert await kanban.claim_task(task.id, "run-1") is True
    assert (await kanban.get_task_status(task.id))["status"] == "running"

    # budget._get_db idem — accumulated_cents lê vectora_background_runs.
    total, desconhecidas = await budget.accumulated_cents(task.id)
    assert (total, desconhecidas) == (0.0, 0)


@pytest.mark.asyncio
async def test_kanban_no_checkpoints_db_falharia_com_tabela_ausente(
    tmp_path, monkeypatch
):
    """Erro/borda que documenta o bug original: se `_get_db` apontar pro
    banco de threads/checkpoints (sem `schema.sql` aplicado), a mesma
    chamada lança `OperationalError` — prova que o teste acima realmente
    exercitaria a falha sem a correção."""
    import aiosqlite

    from backend.scheduling import kanban

    checkpoints_like = tmp_path / "checkpoints.db"

    async def _get_db_errado():
        conn: Any = await aiosqlite.connect(str(checkpoints_like))
        conn.row_factory = lambda c, r: dict(
            zip([col[0] for col in c.description], r, strict=False)
        )
        return conn

    monkeypatch.setattr(kanban, "_get_db", _get_db_errado)

    with pytest.raises(Exception, match="no such table"):
        await kanban.claim_task("qualquer-id", "run-1")
