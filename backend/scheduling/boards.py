"""Multi-board.

Um board é agrupamento NOMEADO por cima das tasks (`vectora_boards` +
`vectora_background_tasks.board_id`); a session continua sendo o contexto
de execução (`session_id`), não é substituída por board nenhum. Ver o
comentário em `schema.sql` sobre por que `workspace_id` do board é só um
default herdado, não um filtro rígido.

Nenhum backfill em massa: `board_id` nullable absorve tasks sem board
associado sem exigir migração de dados — `get_or_create_default_board`
cria o board "Default" do usuário sob demanda, na primeira vez que ele
precisa de um.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class Board:
    id: str
    user_id: str
    slug: str
    name: str
    workspace_id: str | None
    created_at: str | None
    archived_at: str | None


async def _get_db() -> Any:
    from backend.scheduling.background_tasks import _get_db as _tasks_db

    return await _tasks_db()


def _row_to_board(row: dict[str, Any]) -> Board:
    return Board(
        id=row["id"],
        user_id=row["user_id"],
        slug=row["slug"],
        name=row["name"],
        workspace_id=row.get("workspace_id"),
        created_at=row.get("created_at"),
        archived_at=row.get("archived_at"),
    )


def _slugify(name: str) -> str:
    """Deriva um slug a partir do nome — minúsculas, `[a-z0-9-]`, sem
    hífens repetidos/nas pontas. Nome vazio (ou sem caractere
    aproveitável) cai em `"board"`, nunca numa string vazia (que
    quebraria a UNIQUE(user_id, slug) ao colidir entre boards sem nome)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "board"


async def create_board(
    user_id: str,
    name: str,
    *,
    workspace_id: str | None = None,
) -> Board:
    """Cria um board. Nome vazio é recusado — diferente de uma task, um
    board sem nome não tem como aparecer no switcher de forma legível."""
    nome = name.strip()
    if not nome:
        msg = "nome do board não pode ser vazio"
        raise ValueError(msg)

    base_slug = _slugify(nome)
    db = await _get_db()
    async with db.execute(
        "SELECT slug FROM vectora_boards WHERE user_id = ?", (user_id,)
    ) as cur:
        existentes = {r["slug"] for r in await cur.fetchall()}
    slug = base_slug
    n = 2
    while slug in existentes:
        slug = f"{base_slug}-{n}"
        n += 1

    board_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO vectora_boards (id, user_id, slug, name, workspace_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (board_id, user_id, slug, nome, workspace_id),
    )
    await db.commit()
    board = await get_board(board_id)
    if board is None:
        # Só alcançável se o INSERT acima commitou mas o SELECT logo depois
        # não achou a linha — sinal de bug real (conexão errada, etc.), não
        # um estado esperado que o caller deveria tratar.
        msg = f"board {board_id!r} sumiu logo após ser criado"
        raise RuntimeError(msg)
    return board


async def get_board(board_id: str) -> Board | None:
    db = await _get_db()
    async with db.execute(
        "SELECT * FROM vectora_boards WHERE id = ?", (board_id,)
    ) as cur:
        row = await cur.fetchone()
    return _row_to_board(row) if row else None


async def list_boards(user_id: str, *, include_archived: bool = False) -> list[Board]:
    db = await _get_db()
    query = "SELECT * FROM vectora_boards WHERE user_id = ?"
    if not include_archived:
        query += " AND archived_at IS NULL"
    query += " ORDER BY created_at ASC"
    async with db.execute(query, (user_id,)) as cur:
        rows = await cur.fetchall()
    return [_row_to_board(r) for r in rows]


async def update_board(
    board_id: str,
    *,
    name: str | None = None,
    workspace_id: str | None = None,
) -> Board | None:
    board = await get_board(board_id)
    if board is None:
        return None

    sets: list[str] = []
    args: list[Any] = []
    if name is not None:
        nome = name.strip()
        if not nome:
            msg = "nome do board não pode ser vazio"
            raise ValueError(msg)
        sets.append("name = ?")
        args.append(nome)
    if workspace_id is not None:
        sets.append("workspace_id = ?")
        args.append(workspace_id)
    if not sets:
        return board

    args.append(board_id)
    db = await _get_db()
    await db.execute(
        f"UPDATE vectora_boards SET {', '.join(sets)} WHERE id = ?",  # noqa: S608  # nosec B608
        args,
    )
    await db.commit()
    return await get_board(board_id)


async def count_tasks_in_board(board_id: str) -> int:
    db = await _get_db()
    async with db.execute(
        "SELECT COUNT(*) AS n FROM vectora_background_tasks WHERE board_id = ?",
        (board_id,),
    ) as cur:
        row = await cur.fetchone()
    return row["n"] if row else 0


async def delete_board(board_id: str) -> bool:
    """Apaga o board. Recusa (`ValueError`) se ele ainda tem tasks —
    mover cards silenciosamente ou deletá-los junto é irreversível por
    acidente; o caller decide o que fazer primeiro (mover/arquivar as
    tasks), esta função nunca faz essa escolha por ele."""
    n = await count_tasks_in_board(board_id)
    if n > 0:
        msg = f"board tem {n} task(s) — mova ou arquive antes de apagar"
        raise ValueError(msg)
    db = await _get_db()
    cur = await db.execute("DELETE FROM vectora_boards WHERE id = ?", (board_id,))
    await db.commit()
    return cur.rowcount > 0


async def get_or_create_default_board(user_id: str) -> Board:
    """Board "Default" do usuário — criado sob demanda, nunca por
    backfill em massa. Idempotente: chamadas repetidas devolvem o mesmo
    board (procura por slug fixo `"default"` antes de criar)."""
    db = await _get_db()
    async with db.execute(
        "SELECT * FROM vectora_boards WHERE user_id = ? AND slug = 'default'",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return _row_to_board(row)
    return await create_board(user_id, "Default")
