"""Handler REST de boards do Kanban (multi-board).

Endpoints (todos exigem autenticação, escopados ao usuário do request):
    GET    /boards               — lista boards do usuário
    POST   /boards                — cria board
    PATCH  /boards/{board_id}     — atualiza nome/workspace
    DELETE /boards/{board_id}     — remove (409 se tiver tasks)
    GET    /boards/{board_id}/board — board agregado (rescopo do
                                       `GET .../background/board`,
                                       por board em vez de session)

Coexiste com `/sessions/{thread_id}/background/*` — não substitui essas
rotas, que continuam servindo o board escopado por thread que a UI
atual usa.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.scheduling.boards import Board

if TYPE_CHECKING:
    from backend.api.handlers.background import BoardOut as BackgroundBoardOut

router = APIRouter(prefix="/boards", tags=["boards"])


def _user_id(request: Request) -> str:
    """Return the authenticated principal required by board operations."""
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "id", None):
        return str(user.id)
    raise HTTPException(status_code=401, detail="Não autenticado")


class BoardOut(BaseModel):
    id: str
    user_id: str
    slug: str
    name: str
    workspace_id: str | None
    created_at: str | None
    archived_at: str | None


class CreateBoardRequest(BaseModel):
    name: str
    workspace_id: str | None = None


class UpdateBoardRequest(BaseModel):
    name: str | None = None
    workspace_id: str | None = None


def _to_out(b: Board) -> BoardOut:
    return BoardOut(
        id=b.id,
        user_id=b.user_id,
        slug=b.slug,
        name=b.name,
        workspace_id=b.workspace_id,
        created_at=b.created_at,
        archived_at=b.archived_at,
    )


async def _require_own_board(board_id: str, user_id: str) -> Board:
    from backend.scheduling.boards import get_board

    board = await get_board(board_id)
    if board is None or board.user_id != user_id:
        raise HTTPException(status_code=404, detail="Board não encontrado")
    return board


@router.get("", response_model=list[BoardOut])
async def get_boards(request: Request) -> list[BoardOut]:
    from backend.scheduling.boards import list_boards

    boards = await list_boards(_user_id(request))
    return [_to_out(b) for b in boards]


@router.post("", response_model=BoardOut, status_code=201)
async def post_board(request: Request, body: CreateBoardRequest) -> BoardOut:
    from backend.scheduling.boards import create_board

    try:
        board = await create_board(
            _user_id(request), body.name, workspace_id=body.workspace_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_out(board)


@router.patch("/{board_id}", response_model=BoardOut)
async def patch_board(
    request: Request, board_id: str, body: UpdateBoardRequest
) -> BoardOut:
    from backend.scheduling.boards import update_board

    uid = _user_id(request)
    await _require_own_board(board_id, uid)
    try:
        updated = await update_board(
            board_id, name=body.name, workspace_id=body.workspace_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Board não encontrado")
    return _to_out(updated)


@router.delete("/{board_id}", status_code=204)
async def delete_board_endpoint(request: Request, board_id: str) -> None:
    from backend.scheduling.boards import delete_board

    uid = _user_id(request)
    await _require_own_board(board_id, uid)
    try:
        await delete_board(board_id)
    except ValueError as exc:
        # Board com tasks — 409 (conflito com o estado atual), não 400:
        # o payload da requisição está correto, o que impede a ação é o
        # estado do servidor.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{board_id}/board")
async def get_board_view(request: Request, board_id: str) -> BackgroundBoardOut:
    """Board agregado escopado por `board_id` — mesma forma de resposta
    de `GET /sessions/{thread_id}/background/board`, só que a
    fonte das tasks é `board_id`, não `session_id`. Reusa os batch
    helpers de `kanban.py` (progress/comentários/dependências), evitando
    reimplementar a agregação."""
    from backend.api.handlers.background import (
        BoardColumnOut,
        _build_task_out,
    )
    from backend.api.handlers.background import (
        BoardOut as BackgroundBoardOut,
    )
    from backend.scheduling.background_tasks import list_tasks_by_board
    from backend.scheduling.kanban import (
        KANBAN_STATUSES,
        get_comment_counts_batch,
        get_dependencies_batch,
        get_progress_batch,
    )

    uid = _user_id(request)
    await _require_own_board(board_id, uid)

    tasks = await list_tasks_by_board(board_id)
    ids = [t.id for t in tasks]

    progress_map = await get_progress_batch(ids)
    comments_map = await get_comment_counts_batch(ids)
    deps_map = await get_dependencies_batch(ids)

    by_status: dict[str, list] = {}
    tenants: set[str] = set()
    assignees: set[str] = set()
    for t in tasks:
        if t.workspace_id:
            tenants.add(t.workspace_id)
        if t.agent_profile_id:
            assignees.add(t.agent_profile_id)
        out = _build_task_out(
            t,
            dependencies=deps_map.get(t.id, []),
            progress=progress_map.get(t.id),
            comment_count=comments_map.get(t.id, 0),
        )
        by_status.setdefault(t.status, []).append(out)

    ordem = list(KANBAN_STATUSES) + [s for s in by_status if s not in KANBAN_STATUSES]
    columns = [BoardColumnOut(status=s, tasks=by_status.get(s, [])) for s in ordem]
    return BackgroundBoardOut(
        columns=columns,
        tenants=sorted(tenants),
        assignees=sorted(assignees),
    )
