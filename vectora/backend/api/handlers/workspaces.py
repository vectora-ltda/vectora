"""Handler do WorkspaceService — gestão de workspaces (pastas confiáveis).

Endpoints (Connect-style, espelham o proxy Hono do frontend):
    GET  /vectora.workspace.v1.WorkspaceService/ListWorkspaces
    GET  /vectora.workspace.v1.WorkspaceService/GetActiveWorkspace
    POST /vectora.workspace.v1.WorkspaceService/SetActiveWorkspace
    POST /vectora.workspace.v1.WorkspaceService/CreateWorkspace
    POST /vectora.workspace.v1.WorkspaceService/TrustWorkspace
    POST /vectora.workspace.v1.WorkspaceService/GitInitWorkspace
    GET  /vectora.workspace.v1.WorkspaceService/BrowseDir
    GET  /vectora.workspace.v1.WorkspaceService/ListWorktrees
    POST /vectora.workspace.v1.WorkspaceService/CreateWorktree

O user_id é extraído de ``request.state.user`` (injetado pelo AuthMiddleware);
em modo CLI/root local, usa ``"local"``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vectora.workspace.v1.WorkspaceService", tags=["workspaces"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class WorkspaceInfo(BaseModel):
    id: str
    name: str
    cwd: str
    trusted: bool = False
    hooks_approved: bool = False
    is_git_repo: bool = False
    git_remote: str | None = None
    git_current_branch: str | None = None
    git_default_branch: str | None = None
    # Campos de transport. `local` é o default; SSH e Codespace populam
    # os campos abaixo conforme criados.
    transport: str = "local"
    remote_host: str | None = None
    remote_path: str | None = None
    codespace_name: str | None = None


class ListWorkspacesResponse(BaseModel):
    workspaces: list[WorkspaceInfo]
    active_id: str | None = None


class ActiveWorkspaceResponse(BaseModel):
    workspace: WorkspaceInfo | None = None


class SetActiveRequest(BaseModel):
    workspace_id: str


class CreateWorkspaceRequest(BaseModel):
    path: str
    trust: bool = False
    git_init: bool = False


class MkdirRequest(BaseModel):
    path: str
    name: str


class CreateRemoteWorkspaceRequest(BaseModel):
    transport: Literal["ssh", "codespace"]
    name: str = ""
    remote_host: str | None = None
    remote_path: str | None = None
    ssh_key_id: str | None = None
    codespace_name: str | None = None


class TrustRequest(BaseModel):
    workspace_id: str


class ApproveHooksRequest(BaseModel):
    workspace_id: str


class GitInitRequest(BaseModel):
    workspace_id: str


class StatusResponse(BaseModel):
    status: str
    message: str = ""
    workspace: WorkspaceInfo | None = None


class DirEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    # `kind`: "dir" (default), "drive" (raiz de volume Windows ou mount
    # Linux/macOS). Frontend renderiza ícones diferentes por tipo e o
    # `drive` exibe label/capacity quando disponível.
    kind: str = "dir"
    label: str = ""


class BrowseResponse(BaseModel):
    path: str
    parent: str | None = None
    entries: list[DirEntry]
    # ID do SafeRoot que cobre o path atual, quando aplicável. Frontend
    # exibe a label do safe-root como contexto ("dentro de Documents").
    safe_root_id: str | None = None
    # `true` quando `entries` lista volumes do sistema em vez de
    # subdiretórios. Path nesse caso é o pseudo-path `"__drives__"`.
    at_drives_root: bool = False


class SafeRootInfo(BaseModel):
    id: str
    path: str
    label: str
    builtin: bool


class ListSafeRootsResponse(BaseModel):
    roots: list[SafeRootInfo]


class TestSshRequest(BaseModel):
    host: str
    key_id: str | None = None


class TestSshResponse(BaseModel):
    ok: bool
    message: str = ""


class CodespaceInfo(BaseModel):
    name: str
    repository: str = ""
    state: str = ""
    git_status: dict | None = None


class ListCodespacesResponse(BaseModel):
    codespaces: list[CodespaceInfo]
    available: bool = True
    message: str = ""


class WorktreeInfo(BaseModel):
    path: str
    branch: str | None = None
    head: str | None = None


class ListWorktreesResponse(BaseModel):
    worktrees: list[WorktreeInfo]


class CreateWorktreeRequest(BaseModel):
    workspace_id: str
    name: str
    branch: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_id(request: Request) -> str:
    """Extrai o user_id do request autenticado, ou 'local' em modo CLI."""
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "id", None):
        return str(user.id)
    return "local"


def _is_privileged(request: Request) -> bool:
    """True para root/admin (CLI local também é privilegiado).

    Usuários privilegiados podem navegar fora dos safe-roots
    (necessário para o admin escolher pastas a confiar).
    """
    user = getattr(request.state, "user", None)
    if user is None:
        return True  # CLI sem auth = root local
    role = str(getattr(user, "role", "")).lower()
    return role in {"root", "admin"}


def can_access_workspace(ws: Any, request: Request) -> bool:
    """True se o usuário do request pode acessar ``ws``.

    Regras: root/admin sempre podem; workspace sem ``owner_id`` (legado ou
    nunca reivindicado) nunca é restringido; caso contrário exige
    ``ws.owner_id == user_id``. Sem isso, qualquer usuário autenticado em
    modo servidor conseguia ler/mutar o workspace de outro só sabendo o id.
    """
    if _is_privileged(request):
        return True
    owner_id = getattr(ws, "owner_id", None)
    if owner_id is None:
        return True
    return owner_id == _user_id(request)


def require_workspace_access(workspace_id: str, request: Request) -> Any:
    """Garante que o request pode acessar ``workspace_id``, se ele existir.

    Levanta 403 se o workspace existe mas pertence a outro usuário. Workspace
    inexistente **não** levanta aqui — devolve ``None`` e deixa o handler
    chamador decidir como reportar "não encontrado" (a maioria destes
    handlers já tem seu próprio caminho gracioso, `StatusResponse(status=
    "error", ...)`, e duplicar isso aqui mudaria o contrato existente).
    Ponto único reusado por handlers de outros módulos (terminal, rag,
    background, context_graph) que resolvem workspace_id vindo do cliente.
    """
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return None
    if not can_access_workspace(ws, request):
        raise HTTPException(
            status_code=403, detail="Você não tem acesso a este workspace."
        )
    return ws


async def _enforce_workspace_ownership(workspace_id: str, request: Request) -> None:
    """Dependency do ``workspace_scoped_router`` — barra 403 antes de
    qualquer handler rodar, reusando ``require_workspace_access`` (zero
    lógica nova). Workspace inexistente não levanta aqui (mesmo contrato de
    ``require_workspace_access``) — cada handler mantém seu próprio
    comportamento de "não encontrado" (404 explícito, lista vazia, etc.),
    sem essa dependency duplicar ou mudar mensagens de erro já testadas."""
    require_workspace_access(workspace_id, request)


def _to_info(ws: Any) -> WorkspaceInfo:
    """Converte um ``vectora.types.Workspace`` (duck-typed para evitar import cycle)."""
    return WorkspaceInfo(
        id=ws.id,
        name=ws.name,
        cwd=ws.cwd,
        trusted=getattr(ws, "trusted", False),
        hooks_approved=getattr(ws, "hooks_approved", False),
        is_git_repo=getattr(ws, "is_git_repo", False),
        git_remote=getattr(ws, "git_remote", None),
        git_current_branch=getattr(ws, "git_current_branch", None),
        git_default_branch=getattr(ws, "git_default_branch", None),
        transport=getattr(ws, "transport", "local"),
        remote_host=getattr(ws, "remote_host", None),
        remote_path=getattr(ws, "remote_path", None),
        codespace_name=getattr(ws, "codespace_name", None),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/ListWorkspaces", response_model=ListWorkspacesResponse)
async def list_workspaces(request: Request) -> ListWorkspacesResponse:
    """Lista todos os workspaces registrados."""
    from backend.workspace.workspace import workspace_registry

    uid = _user_id(request)
    active = workspace_registry.get_active(uid)
    return ListWorkspacesResponse(
        workspaces=[
            _to_info(ws)
            for ws in workspace_registry.list_all()
            if can_access_workspace(ws, request)
        ],
        active_id=active.id if active else None,
    )


@router.get("/GetActiveWorkspace", response_model=ActiveWorkspaceResponse)
async def get_active_workspace(request: Request) -> ActiveWorkspaceResponse:
    """Retorna o workspace ativo do usuário, ou ``None`` se não houver."""
    from backend.workspace.workspace import workspace_registry

    uid = _user_id(request)
    active = workspace_registry.get_active(uid)
    if active is None:
        # Não auto-cria na leitura: o frontend polla este endpoint no load e
        # acabava materializando um workspace antes de qualquer ação do usuário.
        return ActiveWorkspaceResponse(workspace=None)
    return ActiveWorkspaceResponse(workspace=_to_info(active))


@router.post("/SetActiveWorkspace", response_model=StatusResponse)
async def set_active_workspace(
    request: Request, body: SetActiveRequest
) -> StatusResponse:
    """Troca o workspace ativo do usuário."""
    from backend.workspace.workspace import workspace_registry

    uid = _user_id(request)
    require_workspace_access(body.workspace_id, request)
    ok = workspace_registry.set_active(body.workspace_id, uid)
    if not ok:
        return StatusResponse(status="error", message="Workspace não encontrado.")
    ws = workspace_registry.get(body.workspace_id)
    return StatusResponse(status="ok", workspace=_to_info(ws) if ws else None)


@router.post("/CreateWorkspace", response_model=StatusResponse)
async def create_workspace(
    request: Request, body: CreateWorkspaceRequest
) -> StatusResponse:
    """Registra uma pasta como workspace, opcionalmente confiando e iniciando git."""
    from backend.workspace.workspace import workspace_registry

    path = Path(body.path).expanduser()
    if not path.exists() or not path.is_dir():
        return StatusResponse(
            status="error", message=f"Diretório não encontrado: {body.path}"
        )

    uid = _user_id(request)
    ws = workspace_registry.create(
        str(path), trust=body.trust, git_init=body.git_init, user_id=uid
    )
    workspace_registry.set_active(ws.id, uid)
    return StatusResponse(status="ok", workspace=_to_info(ws))


@router.post("/CreateRemoteWorkspace", response_model=StatusResponse)
async def create_remote_workspace(
    request: Request, body: CreateRemoteWorkspaceRequest
) -> StatusResponse:
    """Cria um workspace remoto (SSH ou Codespace)."""
    from backend.workspace.workspace import workspace_registry

    uid = _user_id(request)
    try:
        ws = workspace_registry.create_remote(
            name=body.name or (body.remote_host or body.codespace_name or "remote"),
            transport=body.transport,
            remote_host=body.remote_host,
            remote_path=body.remote_path,
            ssh_key_id=body.ssh_key_id,
            codespace_name=body.codespace_name,
            user_id=uid,
        )
    except ValueError as exc:
        return StatusResponse(status="error", message=str(exc))
    workspace_registry.set_active(ws.id, uid)
    return StatusResponse(status="ok", workspace=_to_info(ws))


@router.post("/TrustWorkspace", response_model=StatusResponse)
async def trust_workspace(request: Request, body: TrustRequest) -> StatusResponse:
    """Marca um workspace como confiável."""
    from backend.workspace.workspace import workspace_registry

    uid = _user_id(request)
    require_workspace_access(body.workspace_id, request)
    ok = workspace_registry.trust(body.workspace_id, uid)
    if not ok:
        return StatusResponse(status="error", message="Workspace não encontrado.")
    ws = workspace_registry.get(body.workspace_id)
    return StatusResponse(status="ok", workspace=_to_info(ws) if ws else None)


@router.post("/ApproveHooks", response_model=StatusResponse)
async def approve_hooks(request: Request, body: ApproveHooksRequest) -> StatusResponse:
    """Aprova a execução de ``[hooks].post_file_write`` deste workspace.

    Confiar na pasta (``TrustWorkspace``) autoriza leitura/escrita/terminal —
    hooks são um efeito colateral distinto (comando de shell definido em
    ``vectora.toml``, que pode vir de um repositório clonado) e exigem
    aprovação própria antes da 1ª execução.
    """
    from backend.workspace.workspace import workspace_registry

    uid = _user_id(request)
    require_workspace_access(body.workspace_id, request)
    ok = workspace_registry.approve_hooks(body.workspace_id, uid)
    if not ok:
        return StatusResponse(status="error", message="Workspace não encontrado.")
    ws = workspace_registry.get(body.workspace_id)
    return StatusResponse(status="ok", workspace=_to_info(ws) if ws else None)


@router.post("/GitInitWorkspace", response_model=StatusResponse)
async def git_init_workspace(request: Request, body: GitInitRequest) -> StatusResponse:
    """Inicializa um repositório git na pasta do workspace."""
    from backend.tools.git import detect_git_info, git_init_repo
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(body.workspace_id)
    if ws is None:
        return StatusResponse(status="error", message="Workspace não encontrado.")
    if not can_access_workspace(ws, request):
        raise HTTPException(
            status_code=403, detail="Você não tem acesso a este workspace."
        )

    result = git_init_repo(ws.cwd)
    if result.get("status") == "error":
        return StatusResponse(status="error", message=result.get("message", ""))

    info = detect_git_info(ws.cwd)
    ws.is_git_repo = info.get("is_git_repo", False)
    ws.git_current_branch = info.get("git_current_branch")
    ws.git_remote = info.get("git_remote")
    workspace_registry._save()
    return StatusResponse(status="ok", workspace=_to_info(ws))


_DRIVES_PSEUDO_PATH = "__drives__"


def _list_drives() -> list[DirEntry]:
    """Lista volumes do sistema para navegação pelo trust dialog.

    - **Windows**: enumera letras `A:` a `Z:` que estejam montadas via
      ``GetLogicalDrives`` (bitmask). Label vem do ``GetVolumeInformationW``
      quando disponível.
    - **Linux**: `/`, mais entradas de ``/mnt`` e ``/media``.
    - **macOS**: `/`, mais entradas de ``/Volumes`` (sem `Macintosh HD`
      duplicado quando aponta para `/`).
    """
    import sys as _sys

    out: list[DirEntry] = []

    if _sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            mask = kernel32.GetLogicalDrives()
            volume_buf = ctypes.create_unicode_buffer(261)
            fs_buf = ctypes.create_unicode_buffer(261)

            for i in range(26):
                if not (mask & (1 << i)):
                    continue
                letter = chr(ord("A") + i)
                root = f"{letter}:\\"
                label = ""
                with contextlib.suppress(OSError, OverflowError):
                    if kernel32.GetVolumeInformationW(
                        root,
                        volume_buf,
                        261,
                        None,
                        ctypes.byref(wintypes.DWORD()),
                        ctypes.byref(wintypes.DWORD()),
                        fs_buf,
                        261,
                    ):
                        label = volume_buf.value
                out.append(
                    DirEntry(
                        name=f"{letter}:",
                        path=root,
                        is_dir=True,
                        kind="drive",
                        label=label,
                    )
                )
        except Exception:
            logger.warning("workspaces: falha ao enumerar drives Windows")
        return out

    # Unix: raiz + pontos de montagem comuns.
    out.append(DirEntry(name="/", path="/", is_dir=True, kind="drive", label="Raiz"))
    for mount in ("/mnt", "/media", "/Volumes"):
        m = Path(mount)
        if not m.is_dir():
            continue
        try:
            out.extend(
                DirEntry(
                    name=child.name,
                    path=str(child),
                    is_dir=True,
                    kind="drive",
                    label="",
                )
                for child in sorted(m.iterdir(), key=lambda p: p.name.lower())
                if child.is_dir() and not child.name.startswith(".")
            )
        except (PermissionError, OSError):
            continue
    return out


def _resolve_and_authorize_dir(
    path: str, privileged: bool, registry: Any
) -> tuple[Path, str | None]:
    """Resolve ``path`` para um diretório existente e autoriza o acesso.

    Mesmo guard de safe-root usado por ``browse_dir``: usuário comum só
    acessa dentro das raízes configuradas (403 se pedir explicitamente
    algo fora); privilegiado, livre. Extraído para ser reaproveitado por
    ``mkdir_dir`` sem duplicar a lógica de autorização.
    """
    from fastapi import HTTPException

    base = Path(path).expanduser() if path else Path.home()
    try:
        base = base.resolve()
    except OSError:
        base = Path.home()

    if not base.exists() or not base.is_dir():
        base = Path.home()

    safe_root_id: str | None = None
    if not privileged:
        containing = registry.is_under_safe_root(str(base))
        if containing is None:
            if path:
                raise HTTPException(
                    status_code=403,
                    detail="Caminho fora das pastas seguras configuradas.",
                )
            fallback = registry.closest_safe_root_for(str(Path.home()))
            if fallback is None:
                raise HTTPException(
                    status_code=403,
                    detail="Nenhuma pasta segura configurada. "
                    "Peça ao admin para adicionar uma.",
                )
            base = Path(fallback.path)
            safe_root_id = fallback.id
        else:
            safe_root_id = containing.id

    return base, safe_root_id


@router.get("/BrowseDir", response_model=BrowseResponse)
async def browse_dir(
    request: Request,
    path: Annotated[str, Query()] = "",
) -> BrowseResponse:
    """Lista subdiretórios de um caminho, respeitando safe-roots.

    Usuários comuns (member/viewer): só navegam dentro das raízes
    configuradas pelo admin (``SafeRootRegistry``). Sem path, abre na
    raiz mais próxima do HOME (geralmente ``~/Documents/vectora``).
    Tentar sair gera 403; o botão "subir um nível" some na borda.

    Privilegiados (root/admin/CLI local): navegação livre — necessário
    para o admin escolher novas pastas a marcar como confiáveis.
    """
    from backend.rbac.safe_roots import get_safe_root_registry

    registry = get_safe_root_registry()
    privileged = _is_privileged(request)

    # Modo "lista de drives" — só faz sentido para usuários privilegiados.
    # User comum só navega dentro dos safe-roots, então drives ficariam
    # vazios e inacessíveis.
    if path == _DRIVES_PSEUDO_PATH and privileged:
        return BrowseResponse(
            path=_DRIVES_PSEUDO_PATH,
            parent=None,
            entries=_list_drives(),
            safe_root_id=None,
            at_drives_root=True,
        )

    base, safe_root_id = _resolve_and_authorize_dir(path, privileged, registry)

    entries: list[DirEntry] = []
    try:
        for item in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if item.name.startswith("."):
                continue
            try:
                is_dir = item.is_dir()
            except OSError:
                continue
            if not is_dir:
                continue
            entries.append(DirEntry(name=item.name, path=str(item), is_dir=True))
    except PermissionError:
        pass

    # `parent`:
    # - User privileged na raiz de um volume (`C:\\`, `/`): aponta para
    #   o pseudo-path `__drives__` para que "voltar" liste discos.
    # - User comum: só sobe se ainda dentro de algum safe-root.
    # - Else: caminho do pai real.
    parent: str | None
    if base.parent == base:
        parent = _DRIVES_PSEUDO_PATH if privileged else None
    elif privileged:
        parent = str(base.parent)
    else:
        parent_under = registry.is_under_safe_root(str(base.parent))
        parent = str(base.parent) if parent_under is not None else None

    return BrowseResponse(
        path=str(base),
        parent=parent,
        entries=entries,
        safe_root_id=safe_root_id,
    )


@router.post("/Mkdir", response_model=BrowseResponse)
async def mkdir_dir(request: Request, body: MkdirRequest) -> BrowseResponse:
    """Cria uma subpasta em ``body.path`` e relista o diretório resultante.

    Mesmo guard de safe-root de ``BrowseDir`` — usuário comum só cria
    dentro das raízes configuradas. ``name`` não pode conter separadores
    de path nem ser ``.``/``..`` (evita escapar do diretório pai via
    traversal)."""
    from fastapi import HTTPException

    from backend.rbac.safe_roots import get_safe_root_registry

    name = body.name.strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Nome de pasta inválido.")

    registry = get_safe_root_registry()
    privileged = _is_privileged(request)
    base, _ = _resolve_and_authorize_dir(body.path, privileged, registry)

    new_dir = base / name
    if new_dir.exists():
        raise HTTPException(
            status_code=409, detail="Já existe uma pasta com esse nome."
        )
    try:
        new_dir.mkdir()
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Não foi possível criar a pasta: {exc}"
        ) from exc

    return await browse_dir(request=request, path=str(base))


@router.get("/ListSafeRoots", response_model=ListSafeRootsResponse)
async def list_safe_roots() -> ListSafeRootsResponse:
    """Lista as raízes confiáveis configuradas (visível a qualquer user)."""
    from backend.rbac.safe_roots import get_safe_root_registry

    registry = get_safe_root_registry()
    return ListSafeRootsResponse(
        roots=[
            SafeRootInfo(id=r.id, path=r.path, label=r.label, builtin=r.builtin)
            for r in registry.all_roots()
        ],
    )


@router.get("/Codespaces", response_model=ListCodespacesResponse)
async def list_codespaces_endpoint() -> ListCodespacesResponse:
    """Lista codespaces do user via ``gh codespace list``.

    Requer ``gh`` CLI autenticado no host do Vectora. Quando o ``gh``
    está ausente ou não-autenticado, devolve lista vazia + flag
    ``available=False`` (UI orienta o user).
    """
    from backend.transport.codespace import list_codespaces

    try:
        raw = await list_codespaces()
    except FileNotFoundError:
        return ListCodespacesResponse(
            codespaces=[], available=False, message="gh CLI não encontrado."
        )
    return ListCodespacesResponse(
        codespaces=[
            CodespaceInfo(
                name=str(item.get("name", "")),
                repository=str(
                    (item.get("repository") or {}).get("nameWithOwner")
                    if isinstance(item.get("repository"), dict)
                    else item.get("repository") or ""
                ),
                state=str(item.get("state", "")),
                git_status=item.get("gitStatus")
                if isinstance(item.get("gitStatus"), dict)
                else None,
            )
            for item in raw
        ],
        available=True,
    )


@router.post("/TestSsh", response_model=TestSshResponse)
async def test_ssh(body: TestSshRequest, request: Request) -> TestSshResponse:
    """Tenta abrir uma conexão SSH com a chave do vault do usuário.

    Retorna ``{ok: false, message}`` em qualquer erro; nunca expõe
    detalhes internos pra evitar info disclosure.
    """
    user_id = _user_id(request)
    from backend.transport.ssh import SshTransport

    transport = SshTransport(
        remote_host=body.host,
        ssh_key_id=body.key_id,
        user_id=user_id,
    )
    try:
        result = await transport.run(["echo", "ok"], cwd=".", timeout=10.0)
        if result.exit_code == 0:
            return TestSshResponse(ok=True, message="OK")
        return TestSshResponse(ok=False, message=(result.stderr or "exit != 0").strip())
    except Exception as exc:
        return TestSshResponse(ok=False, message=str(exc))
    finally:
        await transport.close()


@router.get("/ListWorktrees", response_model=ListWorktreesResponse)
async def list_worktrees(
    workspace_id: Annotated[str, Query()] = "",
) -> ListWorktreesResponse:
    """Lista as worktrees de um workspace git."""
    from backend.tools.context import ctx_from_config
    from backend.tools.git import _git_worktree_impl, _open_repo

    repo, err = _open_repo(workspace_id or None, ctx_from_config(None))
    if err:
        return ListWorktreesResponse(worktrees=[])
    result = _git_worktree_impl(repo, workspace_id, action="list")
    items = result.get("worktrees", []) if result.get("status") == "ok" else []
    return ListWorktreesResponse(
        worktrees=[
            WorktreeInfo(
                path=w.get("path", ""),
                branch=w.get("branch"),
                head=w.get("head"),
            )
            for w in items
        ]
    )


@router.post("/CreateWorktree", response_model=StatusResponse)
async def create_worktree(body: CreateWorktreeRequest) -> StatusResponse:
    """Cria uma nova worktree para o workspace."""
    from backend.tools.context import ctx_from_config
    from backend.tools.git import _git_worktree_impl, _open_repo

    repo, err = _open_repo(body.workspace_id or None, ctx_from_config(None))
    if err:
        return StatusResponse(
            status="error", message=json.loads(err).get("message", "")
        )
    result = _git_worktree_impl(
        repo, body.workspace_id, action="add", name=body.name, branch=body.branch
    )
    return StatusResponse(
        status=result.get("status", "error"), message=result.get("message", "")
    )


# ---------------------------------------------------------------------------
# Workbench views — REST-style /workspaces/{id}/...
# ---------------------------------------------------------------------------
#
# Endpoints específicos consumidos pelas abas Arquivos e Diff do Workbench.
# Mantidos num router separado com prefixo /workspaces para conviver com o
# router Connect-style acima sem colisão.

view_router = APIRouter(prefix="/workspaces", tags=["workspaces-view"])

#: Todo endpoint sob `/workspaces/{workspace_id}/...` migra pra cá — a
#: dependency barra 403 (dono errado) antes do handler rodar, pra nenhuma
#: rota nova esquecer o check de ownership. Workspace inexistente não é
#: interceptado aqui — cada handler mantém seu próprio "não encontrado"
#: (ver `_enforce_workspace_ownership`).
workspace_scoped_router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["workspaces-view"],
    dependencies=[Depends(_enforce_workspace_ownership)],
)


@view_router.get("", response_model=ListWorkspacesResponse)
async def list_workspaces_rest(request: Request) -> ListWorkspacesResponse:
    return await list_workspaces(request)


@view_router.get("/browse", response_model=BrowseResponse)
async def browse_view(
    request: Request,
    path: Annotated[str, Query()] = "",
) -> BrowseResponse:
    """Atalho REST-friendly para ``BrowseDir`` — consumido pelo trust
    dialog. Mantemos o handler Connect-style para clients que falam o
    naming antigo e este alias serve a SPA do chat sem cruzar
    namespaces."""
    return await browse_dir(request=request, path=path)


@view_router.post("/browse/mkdir", response_model=BrowseResponse)
async def mkdir_view(request: Request, body: MkdirRequest) -> BrowseResponse:
    """Atalho REST-friendly para ``Mkdir`` — mesmo padrão de ``browse_view``."""
    return await mkdir_dir(request=request, body=body)


@view_router.get("/safe-roots", response_model=ListSafeRootsResponse)
async def list_safe_roots_rest() -> ListSafeRootsResponse:
    return await list_safe_roots()


@view_router.get("/codespaces", response_model=ListCodespacesResponse)
async def list_codespaces_rest() -> ListCodespacesResponse:
    return await list_codespaces_endpoint()


@view_router.post("/create", response_model=StatusResponse)
async def create_workspace_rest(
    request: Request, body: CreateWorkspaceRequest
) -> StatusResponse:
    return await create_workspace(request, body)


@view_router.post("/set-active", response_model=StatusResponse)
async def set_active_workspace_rest(
    request: Request, body: SetActiveRequest
) -> StatusResponse:
    return await set_active_workspace(request, body)


@view_router.post("/trust", response_model=StatusResponse)
async def trust_workspace_rest(request: Request, body: TrustRequest) -> StatusResponse:
    return await trust_workspace(request, body)


@view_router.post("/approve-hooks", response_model=StatusResponse)
async def approve_hooks_rest(
    request: Request, body: ApproveHooksRequest
) -> StatusResponse:
    return await approve_hooks(request, body)


@view_router.post("/git-init", response_model=StatusResponse)
async def git_init_workspace_rest(
    request: Request, body: GitInitRequest
) -> StatusResponse:
    return await git_init_workspace(request, body)


@view_router.get("/active", response_model=ActiveWorkspaceResponse)
async def get_active_workspace_rest(request: Request) -> ActiveWorkspaceResponse:
    return await get_active_workspace(request)


@view_router.post("/test-ssh", response_model=TestSshResponse)
async def test_ssh_rest(body: TestSshRequest, request: Request) -> TestSshResponse:
    return await test_ssh(body, request)


@view_router.post("/create-remote", response_model=StatusResponse)
async def create_remote_workspace_rest(
    request: Request, body: CreateRemoteWorkspaceRequest
) -> StatusResponse:
    return await create_remote_workspace(request, body)


class TreeEntry(BaseModel):
    name: str
    path: str  # caminho relativo ao workspace
    kind: str  # "dir" | "file"
    size: int | None = None


class TreeResponse(BaseModel):
    path: str
    entries: list[TreeEntry]


class FileResponse(BaseModel):
    path: str
    kind: str  # "text" | "binary"
    content: str | None = None
    size: int = 0
    truncated: bool = False
    # sha256 do conteúdo retornado — só presente quando não truncado, pois é
    # o que habilita a edição inline (controle de conflito otimista no PUT).
    sha256: str | None = None


class DiffFile(BaseModel):
    """Estado git de um único path no workspace.

    ``staged_change``, ``unstaged_change`` e ``untracked`` são flags
    independentes que representam fielmente o par ``XY`` do
    ``git status --porcelain=v1`` — incluindo o caso ``XY=MM`` em que
    o mesmo path tem mudanças staged E unstaged ao mesmo tempo.

    ``status`` traz o primeiro caracter não-vazio do par porcelain
    (``"M"`` / ``"A"`` / ``"D"`` / ``"R"`` / ``"?"``), oferecido como
    resumo single-char para consumidores que só precisam saber "houve
    alguma mudança neste arquivo".
    """

    path: str
    status: str  # "M" | "A" | "D" | "R" | "?" — primeiro flag não-vazio
    additions: int = 0
    deletions: int = 0
    staged_change: str | None = None  # "M" | "A" | "D" | "R" | None
    unstaged_change: str | None = None  # "M" | "D" | None
    untracked: bool = False


class DiffHunk(BaseModel):
    header: str
    lines: list[str]


class DiffSummary(BaseModel):
    is_git_repo: bool
    total_additions: int = 0
    total_deletions: int = 0
    files: list[DiffFile]


class DiffFileResponse(BaseModel):
    path: str
    hunks: list[DiffHunk]


# Tamanho máximo lido pela aba Arquivos (previne payload gigante).
_MAX_FILE_PREVIEW = 256 * 1024  # 256 kB

# Diretórios ignorados por padrão na árvore — reduz ruído típico de projetos.
_IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    ".turbo",
    ".cache",
    "dist",
    "build",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}


def _resolve_inside(workspace_id: str, rel_path: str) -> Path | None:
    """Resolve ``rel_path`` para um caminho dentro do workspace ou None.

    Reusa o helper de segurança `resolve_within_workspace`, garantindo que
    `..` e symlinks para fora não escapem da pasta confiável.
    """
    from backend.services.security import resolve_within_workspace
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return None
    base = Path(ws.cwd)
    candidate = base if not rel_path else base / rel_path
    return resolve_within_workspace(str(candidate), base)


class SandboxStatusResponse(BaseModel):
    enabled: bool
    diagnostic: str | None = None


@workspace_scoped_router.get("/sandbox/status", response_model=SandboxStatusResponse)
async def workspace_sandbox_status(workspace_id: str) -> SandboxStatusResponse:
    """Reflete se o worker jailado (Sandbox) está habilitado pra essa
    workspace — lê `vectora.toml`/`[sandbox]` na raiz do workspace, mesma
    fonte que `backend/tools/fs.py`/`PtySession` já consultam antes de
    rotear terminal/tools de arquivo pelo worker. Workspace inexistente
    retorna `enabled=False` (nunca alega proteção que não existe).

    `diagnostic` só é populado quando desabilitado (`None` quando já
    habilitado) — código curto explicando a causa real (ver
    `backend/sandbox/policy.py::wsl2_diagnostic`), pro frontend virar um
    aviso acionável em vez de só "sem sandbox"."""
    from backend.sandbox.policy import detect_wsl2, parse_policy, wsl2_diagnostic
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return SandboxStatusResponse(enabled=False, diagnostic="no_workspace")
    policy = parse_policy(Path(ws.cwd) / "vectora.toml")
    if policy.enabled:
        return SandboxStatusResponse(enabled=True)

    toml_path = Path(ws.cwd) / "vectora.toml"
    if not toml_path.is_file():
        distro = await detect_wsl2()
        diagnostic = "no_vectora_toml" if distro else await wsl2_diagnostic()
    else:
        diagnostic = "sandbox_disabled_in_config"
    return SandboxStatusResponse(enabled=False, diagnostic=diagnostic)


class SandboxInitResponse(BaseModel):
    ok: bool


@workspace_scoped_router.post("/sandbox/init", response_model=SandboxInitResponse)
async def workspace_sandbox_init(workspace_id: str) -> SandboxInitResponse:
    """Cria `vectora.toml` na raiz do workspace com `[sandbox] enabled = true`
    (config mínima, sem os comentários explicativos do template de
    referência — o usuário já está agindo a partir de um diagnóstico
    acionável na UI). Nunca sobrescreve um `vectora.toml` já existente
    (mesmo sem seção `[sandbox]`) — evita apagar config que o usuário já
    tenha escrito à mão."""
    from fastapi import HTTPException

    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace não encontrado.")

    toml_path = Path(ws.cwd) / "vectora.toml"
    if toml_path.exists():
        raise HTTPException(
            status_code=409,
            detail="vectora.toml já existe — edite manualmente pra habilitar [sandbox].",
        )

    toml_path.write_text(
        '[sandbox]\nenabled = true\nbackend = "local"\n', encoding="utf-8"
    )
    return SandboxInitResponse(ok=True)


@workspace_scoped_router.get("/tree", response_model=TreeResponse)
async def workspace_tree(
    workspace_id: str,
    path: Annotated[str, Query()] = "",
) -> TreeResponse:
    """Lista entradas de um diretório dentro do workspace ativo.

    `path` é relativo ao workspace. Sem path → raiz. Diretórios típicos de
    build/cache são ocultados para enxugar a árvore.
    """
    resolved = _resolve_inside(workspace_id, path)
    if resolved is None or not resolved.exists() or not resolved.is_dir():
        return TreeResponse(path=path, entries=[])

    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    base = Path(ws.cwd) if ws else resolved

    entries: list[TreeEntry] = []
    try:
        for item in sorted(
            resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
        ):
            try:
                is_dir = item.is_dir()
            except OSError:
                continue
            if is_dir and item.name in _IGNORED_DIR_NAMES:
                continue
            try:
                rel = str(item.relative_to(base))
            except ValueError:
                continue
            size: int | None = None
            if not is_dir:
                try:
                    size = item.stat().st_size
                except OSError:
                    size = None
            entries.append(
                TreeEntry(
                    name=item.name,
                    path=rel.replace("\\", "/"),
                    kind="dir" if is_dir else "file",
                    size=size,
                )
            )
    except PermissionError:
        pass

    return TreeResponse(path=path, entries=entries)


@workspace_scoped_router.get("/file", response_model=FileResponse)
async def workspace_file(
    workspace_id: str,
    path: Annotated[str, Query()],
) -> FileResponse:
    """Lê o conteúdo (texto truncado) de um arquivo dentro do workspace."""
    resolved = _resolve_inside(workspace_id, path)
    if resolved is None or not resolved.exists() or not resolved.is_file():
        return FileResponse(path=path, kind="text", content=None, size=0)

    try:
        size = resolved.stat().st_size
    except OSError:
        size = 0

    # Lê em modo binário (sem tradução de newline) para que o sha256 reflita
    # exatamente os bytes em disco — é contra esse hash que o PUT confere
    # `expected_sha256` antes de sobrescrever.
    try:
        with resolved.open("rb") as f:
            raw = f.read(_MAX_FILE_PREVIEW + 1)
    except OSError:
        return FileResponse(path=path, kind="text", content=None, size=size)

    if b"\x00" in raw[:8192]:
        return FileResponse(path=path, kind="binary", size=size)

    truncated = len(raw) > _MAX_FILE_PREVIEW
    if truncated:
        raw = raw[:_MAX_FILE_PREVIEW]
    content = raw.decode("utf-8", errors="replace")
    sha256 = None if truncated else hashlib.sha256(raw).hexdigest()
    return FileResponse(
        path=path,
        kind="text",
        content=content,
        size=size,
        truncated=truncated,
        sha256=sha256,
    )


@workspace_scoped_router.get("/fs/raw")
async def workspace_file_raw(
    workspace_id: str,
    path: Annotated[str, Query()],
) -> Response:
    """Serve os bytes crus de um arquivo do workspace com Content-Type real.

    Alimenta o preview de mídia (imagem/vídeo/áudio/pdf) e o download — o
    ``GET /file`` trunca e devolve texto, sem servir para isso. Suporta
    requisições Range (seek de vídeo/áudio) via ``FileResponse`` da Starlette.
    Anti-traversal pelo mesmo ``resolve_within_workspace`` do viewer de texto.
    """
    import mimetypes
    from pathlib import PurePosixPath

    from fastapi import HTTPException
    from fastapi.responses import FileResponse as _StarletteFileResponse

    resolved = _resolve_inside(workspace_id, path)
    if resolved is None or not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    name = PurePosixPath(path).name or resolved.name
    media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return _StarletteFileResponse(resolved, media_type=media_type)


def _parse_unified_diff(diff_text: str) -> list[DiffHunk]:
    """Quebra um diff unificado em hunks (sem a linha 'diff --git' inicial)."""
    hunks: list[DiffHunk] = []
    current: DiffHunk | None = None
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            if current is not None:
                hunks.append(current)
            current = DiffHunk(header=line, lines=[])
        elif current is not None:
            current.lines.append(line)
    if current is not None:
        hunks.append(current)
    return hunks


def _untracked_as_diff(content: str) -> list[DiffHunk]:
    """Gera hunks de diff sintético para arquivo untracked (puro adição).

    Arquivos não rastreados pelo git não aparecem em ``git diff HEAD``.
    Esta função formata o conteúdo como se fosse um diff ``+`` completo,
    permitindo que o diff-tab exiba o arquivo untracked como adição total.
    """
    if not content:
        return []
    lines = content.splitlines()
    n = len(lines)
    hunk = DiffHunk(
        header=f"@@ -0,0 +1,{n} @@",
        lines=[f"+{line}" for line in lines],
    )
    return [hunk]


def _parse_porcelain_v1(raw: str) -> list[tuple[str, str, str]]:
    """Faz parse de ``git status --porcelain=v1 -uall`` em (staged, unstaged, path).

    Cada linha tem 2 chars de status + espaço + path. ``??`` = untracked.
    Renames usam ``R  old -> new`` — devolvemos o caminho destino.
    """
    out: list[tuple[str, str, str]] = []
    for line in raw.splitlines():
        if len(line) < 4:
            continue
        x, y, path = line[0], line[1], line[3:].rstrip()
        # rename: "R  old -> new" — pegar o novo
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        # paths com espaços vêm sem quoting com `-z` mas estamos usando porcelain v1 sem -z;
        # git escapa com aspas quando há espaço — strip simples.
        path = path.strip('"').replace("\\", "/")
        out.append((x, y, path))
    return out


@workspace_scoped_router.get("/git/diff", response_model=DiffSummary)
async def workspace_git_diff(workspace_id: str, response: Response) -> DiffSummary:
    """Resumo do diff (uncommitted) do workspace.

    Faz duas passadas no repositório:
      - ``git status --porcelain=v1 -uall`` cobre staged, unstaged e
        untracked numa única invocação;
      - ``git diff HEAD --numstat`` fornece adições/remoções para os
        paths modificados (untracked ficam com ``0/0``).

    Retorna ``is_git_repo=False`` com lista vazia para workspaces sem
    ``.git`` ou quando ``git`` não está disponível no ambiente.
    """
    from backend.workspace.workspace import workspace_registry

    # Versão do schema de diff: cada arquivo traz flags independentes
    # staged_change/unstaged_change/untracked (cobre XY=MM e untracked).
    response.headers["X-Vectora-Diff-Schema"] = "2"

    ws = workspace_registry.get(workspace_id)
    if ws is None or not ws.cwd:
        return DiffSummary(is_git_repo=False, files=[])

    try:
        from git import Repo  # type: ignore[import-not-found]
        from git.exc import (  # type: ignore[import-not-found]
            InvalidGitRepositoryError,
            NoSuchPathError,
        )
    except Exception:
        return DiffSummary(is_git_repo=False, files=[])

    try:
        repo = Repo(ws.cwd, search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return DiffSummary(is_git_repo=False, files=[])

    # Single porcelain pass — cobre staged + unstaged + untracked.
    try:
        porcelain = repo.git.status("--porcelain=v1", "-uall") or ""
    except Exception:
        porcelain = ""

    flags_by_path: dict[str, tuple[str, str]] = {}
    for x, y, path in _parse_porcelain_v1(porcelain):
        flags_by_path[path] = (x, y)

    # Single numstat pass — contagens para arquivos modificados (não untracked).
    numstat_by_path: dict[str, tuple[int, int]] = {}
    try:
        diff_text = repo.git.diff("HEAD", numstat=True) or ""
    except Exception:
        diff_text = ""

    for raw in diff_text.splitlines():
        parts = raw.split("\t")
        if len(parts) < 3:
            continue
        adds_s, dels_s, path = parts[0], parts[1], parts[2]
        try:
            adds = int(adds_s) if adds_s != "-" else 0
            dels = int(dels_s) if dels_s != "-" else 0
        except ValueError:
            adds = dels = 0
        numstat_by_path[path.replace("\\", "/")] = (adds, dels)

    files: list[DiffFile] = []
    total_add = 0
    total_del = 0

    for path, (x, y) in flags_by_path.items():
        untracked = x == "?" and y == "?"
        staged = None if untracked or x in (" ", "?") else x
        unstaged = None if untracked or y in (" ", "?") else y
        # Resumo single-char: primeiro flag não-vazio, "?" para untracked.
        summary_status = staged or unstaged or ("?" if untracked else "M")
        adds, dels = numstat_by_path.get(path, (0, 0))

        files.append(
            DiffFile(
                path=path,
                status=summary_status,
                additions=adds,
                deletions=dels,
                staged_change=staged,
                unstaged_change=unstaged,
                untracked=untracked,
            )
        )
        total_add += adds
        total_del += dels

    return DiffSummary(
        is_git_repo=True,
        total_additions=total_add,
        total_deletions=total_del,
        files=files,
    )


@workspace_scoped_router.get("/git/diff/file", response_model=DiffFileResponse)
async def workspace_git_diff_file(
    workspace_id: str,
    path: Annotated[str, Query()],
) -> DiffFileResponse:
    """Hunks unificados de um arquivo específico (lazy load do diff-tab)."""
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return DiffFileResponse(path=path, hunks=[])

    try:
        from git import Repo  # type: ignore[import-not-found]
        from git.exc import (  # type: ignore[import-not-found]
            InvalidGitRepositoryError,
            NoSuchPathError,
        )
    except Exception:
        return DiffFileResponse(path=path, hunks=[])

    try:
        repo = Repo(ws.cwd, search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return DiffFileResponse(path=path, hunks=[])

    try:
        status_line = repo.git.status("--porcelain=v1", "--", path) or ""
        is_untracked = status_line.lstrip().startswith("??")
    except Exception:
        is_untracked = False

    if is_untracked:
        from pathlib import Path as _Path

        filepath = _Path(ws.cwd) / path
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        return DiffFileResponse(path=path, hunks=_untracked_as_diff(content))

    try:
        porcelain = repo.git.status("--porcelain=v1", "--", path) or ""
        is_staged_only = porcelain and porcelain[0] not in (" ", "?")
        diff_text = (
            repo.git.diff("--cached", "HEAD", "--", path) or ""
            if is_staged_only and not repo.git.diff("HEAD", "--", path)
            else repo.git.diff("HEAD", "--", path) or ""
        )
    except Exception:
        diff_text = ""

    return DiffFileResponse(path=path, hunks=_parse_unified_diff(diff_text))


# ---------------------------------------------------------------------------
# Histórico de arquivo e visualização de revisão específica
# ---------------------------------------------------------------------------

_MAX_SHOW_BYTES = 512 * 1024  # 512 KiB — limite de conteúdo retornado por git show


class FileLogEntry(BaseModel):
    sha: str
    sha_short: str
    author: str
    date: str  # ISO 8601
    message: str  # primeira linha do commit message


class FileLogResponse(BaseModel):
    path: str
    entries: list[FileLogEntry]


class ShowFileAtRevResponse(BaseModel):
    path: str
    sha: str
    content: str | None = None
    binary: bool = False
    truncated: bool = False


def _open_workspace_repo(workspace_id: str) -> Any | None:
    """Abre o repositório git do workspace ou retorna None.

    Retorna None quando o workspace não existe, não é um repositório git ou
    quando a biblioteca ``gitpython`` não está disponível.
    """
    try:
        from git import Repo  # type: ignore[import-not-found]

        from backend.workspace.workspace import workspace_registry

        ws = workspace_registry.get(workspace_id)
        if ws is None:
            return None
        return Repo(ws.cwd, search_parent_directories=True)
    except Exception:
        return None


@workspace_scoped_router.get("/git/log/file", response_model=FileLogResponse)
async def git_log_file(
    workspace_id: str,
    path: Annotated[str, Query()],
    n: Annotated[int, Query(ge=1, le=200)] = 50,
    follow: Annotated[bool, Query()] = True,
) -> FileLogResponse:
    """Lista os commits que tocaram ``path`` no histórico do repositório.

    Reaproveita ``git log`` via gitpython.  ``follow=true`` passa ``--follow``
    para rastrear renames.  Retorna lista vazia para workspaces sem git.
    """
    import git  # type: ignore[import-not-found]

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return FileLogResponse(path=path, entries=[])

    # Usa \x00 como separador para lidar com mensagens que contêm pipes.
    fmt = "%H%x00%h%x00%an <%ae>%x00%aI%x00%s"
    try:
        raw = repo.git.log(
            f"--max-count={n}",
            f"--format={fmt}",
            *(["--follow"] if follow else []),
            "--",
            path,
        )
    except git.GitCommandError:
        return FileLogResponse(path=path, entries=[])

    entries: list[FileLogEntry] = []
    for line in raw.strip().splitlines():
        parts = line.split("\x00", 4)
        if len(parts) == 5:
            sha, sha_short, author, date, message = parts
            entries.append(
                FileLogEntry(
                    sha=sha,
                    sha_short=sha_short,
                    author=author,
                    date=date,
                    message=message,
                )
            )
    return FileLogResponse(path=path, entries=entries)


@workspace_scoped_router.get("/git/show", response_model=ShowFileAtRevResponse)
async def git_show_file(
    workspace_id: str,
    sha: Annotated[str, Query(min_length=4, max_length=40)],
    path: Annotated[str, Query()],
) -> ShowFileAtRevResponse:
    """Retorna o conteúdo de ``path`` no commit ``sha``.

    Devolve ``binary=true`` quando o arquivo não é texto.  Trunca em
    ``_MAX_SHOW_BYTES`` com ``truncated=true`` para arquivos grandes.
    """
    import git  # type: ignore[import-not-found]

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return ShowFileAtRevResponse(path=path, sha=sha, content=None)

    try:
        content = repo.git.show(f"{sha}:{path}")
    except git.GitCommandError:
        return ShowFileAtRevResponse(path=path, sha=sha, content=None)
    except UnicodeDecodeError:
        return ShowFileAtRevResponse(path=path, sha=sha, binary=True)

    # Heurística de binário: null bytes no conteúdo.
    if "\x00" in content:
        return ShowFileAtRevResponse(path=path, sha=sha, binary=True)

    truncated = len(content) > _MAX_SHOW_BYTES
    return ShowFileAtRevResponse(
        path=path,
        sha=sha,
        content=content[:_MAX_SHOW_BYTES],
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Git Log visual
# ---------------------------------------------------------------------------

_MAX_COMMIT_DIFF_BYTES = 100 * 1024  # 100 KiB


class GitLogCommit(BaseModel):
    sha: str
    sha_short: str
    author: str
    date: str  # ISO 8601
    message: str
    refs: list[str] = []  # branch/tag/HEAD decorations


class GitLogResponse(BaseModel):
    branch: str
    commits: list[GitLogCommit]
    has_more: bool = False


class CommitDiffResponse(BaseModel):
    sha: str
    diff: str
    truncated: bool = False


@workspace_scoped_router.get("/git/log", response_model=GitLogResponse)
async def git_log(
    workspace_id: str,
    n: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    branch: Annotated[str, Query()] = "",
) -> GitLogResponse:
    """Lista ``n`` commits do repositório a partir de ``offset``, com refs.

    Reaproveita ``_open_workspace_repo`` e ``gitpython.iter_commits``.
    Inclui o campo ``refs`` com os nomes de branches/tags/HEAD que apontam
    para cada commit — equivalente ao ``--decorate`` do ``git log``. Busca
    ``n + 1`` internamente para saber se há mais commits além da página
    atual (``has_more``), sem precisar de um segundo round-trip só pra isso.
    """
    import git  # type: ignore[import-not-found]

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return GitLogResponse(branch="", commits=[])

    # Determina a ref de partida.
    ref = branch or ""
    try:
        current_branch = repo.active_branch.name
        ref = ref or current_branch
    except TypeError:
        current_branch = "(HEAD detached)"
        ref = ref or "HEAD"

    # Constrói mapa sha→refs para decorações.
    ref_map: dict[str, list[str]] = {}
    try:
        for r in repo.references:
            try:
                c_sha = r.commit.hexsha
                ref_map.setdefault(c_sha, []).append(r.name)
            except Exception:  # noqa: S112  # nosec B112
                continue
    except Exception:
        pass

    # Insere HEAD no mapa.
    try:
        head_sha = repo.head.commit.hexsha
        head_label = (
            f"HEAD -> {current_branch}" if not repo.head.is_detached else "HEAD"
        )
        ref_map.setdefault(head_sha, []).insert(0, head_label)
    except Exception:
        pass

    try:
        commits = list(repo.iter_commits(ref, max_count=n + 1, skip=offset))
    except git.GitCommandError:
        return GitLogResponse(branch=ref, commits=[])

    has_more = len(commits) > n
    commits = commits[:n]

    return GitLogResponse(
        branch=ref,
        has_more=has_more,
        commits=[
            GitLogCommit(
                sha=c.hexsha,
                sha_short=c.hexsha[:7],
                author=str(c.author),
                date=c.authored_datetime.isoformat(),
                message=c.message.strip().splitlines()[0],
                refs=ref_map.get(c.hexsha, []),
            )
            for c in commits
        ],
    )


@workspace_scoped_router.get("/git/commit/diff", response_model=CommitDiffResponse)
async def git_commit_diff(
    workspace_id: str,
    sha: Annotated[str, Query(min_length=4, max_length=40)],
) -> CommitDiffResponse:
    """Retorna o diff completo de um commit (``git show --unified=3 sha``).

    Trunca em 100 KiB com ``truncated=true``.  Retorna diff vazio quando o
    workspace não é um repositório git ou o SHA não existe.
    """
    import git  # type: ignore[import-not-found]

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return CommitDiffResponse(sha=sha, diff="")

    try:
        diff = repo.git.show("--unified=3", "--stat", sha)
    except (git.GitCommandError, UnicodeDecodeError):
        return CommitDiffResponse(sha=sha, diff="")

    truncated = len(diff) > _MAX_COMMIT_DIFF_BYTES
    return CommitDiffResponse(
        sha=sha,
        diff=diff[:_MAX_COMMIT_DIFF_BYTES],
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Stage / unstage / commit / discard inline
# ---------------------------------------------------------------------------


class GitPathRequest(BaseModel):
    path: str


class GitCommitRequest(BaseModel):
    message: str
    all: bool = False
    dry_run_hooks: bool = False
    body: str | None = None
    amend: bool = False


class GitSquashRequest(BaseModel):
    base_ref: str
    message: str
    body: str | None = None


class GitReorderRequest(BaseModel):
    commits: list[str]


class GitCherryPickRequest(BaseModel):
    sha: str
    no_commit: bool = False


@workspace_scoped_router.post("/git/stage", response_model=StatusResponse)
async def git_stage(workspace_id: str, body: GitPathRequest) -> StatusResponse:
    """Stageia um arquivo (`git add <path>`)."""
    from backend.tools.git import _git_stage_impl

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StatusResponse(status="error", message="Repositório git não encontrado.")
    result = _git_stage_impl(repo, body.path)
    return StatusResponse(status=result["status"], message=result.get("message", ""))


@workspace_scoped_router.post("/git/unstage", response_model=StatusResponse)
async def git_unstage(workspace_id: str, body: GitPathRequest) -> StatusResponse:
    """Remove um arquivo do stage (`git reset HEAD <path>`)."""
    from backend.tools.git import _git_unstage_impl

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StatusResponse(status="error", message="Repositório git não encontrado.")
    result = _git_unstage_impl(repo, body.path)
    return StatusResponse(status=result["status"], message=result.get("message", ""))


@workspace_scoped_router.post("/git/discard", response_model=StatusResponse)
async def git_discard(workspace_id: str, body: GitPathRequest) -> StatusResponse:
    """Descarta mudanças não staged (`git restore -- <path>`)."""
    from backend.tools.git import _git_restore_impl

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StatusResponse(status="error", message="Repositório git não encontrado.")
    result = _git_restore_impl(repo, body.path)
    return StatusResponse(status=result["status"], message=result.get("message", ""))


@workspace_scoped_router.post("/git/commit", response_model=StatusResponse)
async def git_commit_inline(
    workspace_id: str, body: GitCommitRequest
) -> StatusResponse:
    """Cria um commit com os arquivos staged.

    Quando ``dry_run_hooks=True`` executa os pre-commit hooks sem efetivar o
    commit e devolve ``{"status": "hooks_ok"}`` ou ``{"status": "hooks_failed",
    "message": "<saída dos hooks>"}``.
    """
    from backend.tools.git import _git_commit_impl, _run_pre_commit_hooks

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StatusResponse(status="error", message="Repositório git não encontrado.")
    if body.dry_run_hooks:
        hook_result = _run_pre_commit_hooks(repo)
        if hook_result["passed"]:
            return StatusResponse(
                status="hooks_ok", message=hook_result.get("output", "")
            )
        return StatusResponse(
            status="hooks_failed", message=hook_result.get("output", "")
        )
    result = _git_commit_impl(
        repo, body.message, body.all, body=body.body, amend=body.amend
    )
    return StatusResponse(status=result["status"], message=result.get("message", ""))


@workspace_scoped_router.post("/git/squash", response_model=StatusResponse)
async def git_squash_inline(
    workspace_id: str, body: GitSquashRequest
) -> StatusResponse:
    """Squasha commits de `base_ref` até HEAD numa mensagem só."""
    from backend.tools.git import _git_squash_impl

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StatusResponse(status="error", message="Repositório git não encontrado.")
    result = _git_squash_impl(repo, body.base_ref, body.message, body=body.body)
    return StatusResponse(status=result["status"], message=result.get("message", ""))


@workspace_scoped_router.post("/git/reorder", response_model=StatusResponse)
async def git_reorder_inline(
    workspace_id: str, body: GitReorderRequest
) -> StatusResponse:
    """Reordena commits locais ainda não pushados."""
    from backend.tools.git import _git_reorder_impl

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StatusResponse(status="error", message="Repositório git não encontrado.")
    result = _git_reorder_impl(repo, body.commits)
    return StatusResponse(status=result["status"], message=result.get("message", ""))


@workspace_scoped_router.post("/git/cherry-pick", response_model=StatusResponse)
async def git_cherry_pick_inline(
    workspace_id: str, body: GitCherryPickRequest
) -> StatusResponse:
    """Aplica um commit de outra branch/ref na branch ativa."""
    from backend.tools.git import _git_cherry_pick_impl

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StatusResponse(status="error", message="Repositório git não encontrado.")
    result = _git_cherry_pick_impl(repo, body.sha, no_commit=body.no_commit)
    return StatusResponse(status=result["status"], message=result.get("message", ""))


# ---------------------------------------------------------------------------
# Worktree manager (view_router)
# ---------------------------------------------------------------------------


@workspace_scoped_router.get("/worktrees", response_model=ListWorktreesResponse)
async def list_workspace_worktrees(workspace_id: str) -> ListWorktreesResponse:
    """Lista as worktrees do workspace (via view_router)."""
    from backend.tools.context import ctx_from_config
    from backend.tools.git import _git_worktree_impl, _open_repo

    repo, err = _open_repo(workspace_id, ctx_from_config(None))
    if err:
        return ListWorktreesResponse(worktrees=[])
    result = _git_worktree_impl(repo, workspace_id, action="list")
    items = result.get("worktrees", []) if result.get("status") == "ok" else []
    return ListWorktreesResponse(
        worktrees=[
            WorktreeInfo(
                path=w.get("path", ""),
                branch=w.get("branch"),
                head=w.get("head"),
            )
            for w in items
        ]
    )


@workspace_scoped_router.post("/worktrees", response_model=StatusResponse)
async def create_workspace_worktree(
    workspace_id: str, body: CreateWorktreeRequest
) -> StatusResponse:
    """Cria uma worktree no workspace."""
    from backend.tools.context import ctx_from_config
    from backend.tools.git import _git_worktree_impl, _open_repo

    repo, err = _open_repo(workspace_id, ctx_from_config(None))
    if err:
        return StatusResponse(
            status="error", message=json.loads(err).get("message", "")
        )
    result = _git_worktree_impl(
        repo, workspace_id, action="add", name=body.name, branch=body.branch
    )
    return StatusResponse(
        status=result.get("status", "error"), message=result.get("message", "")
    )


# ---------------------------------------------------------------------------
# Comparar refs (estilo VS Code: lista de arquivos + diff por arquivo)
# ---------------------------------------------------------------------------

_MAX_COMPARE_FILES = 1000


class CompareFile(BaseModel):
    path: str
    status: str  # "M" | "A" | "D" | "R"
    additions: int = 0
    deletions: int = 0


class CompareRefsResponse(BaseModel):
    base: str
    head: str
    ahead: int = 0  # commits em head que não estão em base
    behind: int = 0  # commits em base que não estão em head
    files: list[CompareFile] = []
    truncated: bool = False


@workspace_scoped_router.get("/git/compare", response_model=CompareRefsResponse)
async def git_compare_refs(
    workspace_id: str,
    base: Annotated[str, Query(min_length=1)],
    head: Annotated[str, Query(min_length=1)],
) -> CompareRefsResponse:
    """Lista os arquivos alterados entre dois refs (branch, tag, SHA).

    Usa ``git diff --numstat/--name-status base...head`` para a lista de
    arquivos e ``git rev-list --left-right --count`` para ahead/behind. O
    diff de cada arquivo vem de ``/git/compare/file`` (lazy).
    """
    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return CompareRefsResponse(base=base, head=head)

    try:
        numstat = repo.git.diff(f"{base}...{head}", "--numstat") or ""
        name_status = repo.git.diff(f"{base}...{head}", "--name-status") or ""
    except Exception:
        return CompareRefsResponse(base=base, head=head)

    status_by_path: dict[str, str] = {}
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status_by_path[parts[-1]] = (parts[0][:1] or "M").upper()

    files: list[CompareFile] = []
    truncated = False
    for line in numstat.splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        add_s, del_s, path = cols[0], cols[1], cols[2]
        files.append(
            CompareFile(
                path=path,
                status=status_by_path.get(path, "M"),
                additions=int(add_s) if add_s.isdigit() else 0,
                deletions=int(del_s) if del_s.isdigit() else 0,
            )
        )
        if len(files) >= _MAX_COMPARE_FILES:
            truncated = True
            break

    ahead = behind = 0
    try:
        counts = repo.git.rev_list("--left-right", "--count", f"{base}...{head}")
        left, right = counts.split()
        behind, ahead = int(left), int(right)
    except Exception:
        pass

    return CompareRefsResponse(
        base=base,
        head=head,
        ahead=ahead,
        behind=behind,
        files=files,
        truncated=truncated,
    )


class CompareFileDiffResponse(BaseModel):
    path: str
    hunks: list[DiffHunk] = []


@workspace_scoped_router.get(
    "/git/compare/file", response_model=CompareFileDiffResponse
)
async def git_compare_file(
    workspace_id: str,
    base: Annotated[str, Query(min_length=1)],
    head: Annotated[str, Query(min_length=1)],
    path: Annotated[str, Query()],
) -> CompareFileDiffResponse:
    """Hunks unificados de um arquivo entre dois refs (lazy load do compare)."""
    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return CompareFileDiffResponse(path=path)
    try:
        diff_text = repo.git.diff(f"{base}...{head}", "--", path) or ""
    except Exception:
        diff_text = ""
    return CompareFileDiffResponse(path=path, hunks=_parse_unified_diff(diff_text))


# ---------------------------------------------------------------------------
# Reverter commit
# ---------------------------------------------------------------------------


class RevertCommitRequest(BaseModel):
    sha: str
    no_commit: bool = True  # True = aplica as mudanças sem commitar


@workspace_scoped_router.post("/git/revert", response_model=StatusResponse)
async def git_revert_commit(
    workspace_id: str, body: RevertCommitRequest
) -> StatusResponse:
    """Aplica o reverso de um commit no worktree (HITL — não commitado por padrão)."""
    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StatusResponse(status="error", message="Repositório git não encontrado.")

    try:
        args = ["revert", "--no-edit"]
        if body.no_commit:
            args.append("--no-commit")
        args.append(body.sha)
        repo.git.execute(args)
        msg = f"Revert de {body.sha[:7]} aplicado ao worktree. Revise as mudanças e faça commit."
        return StatusResponse(status="ok", message=msg)
    except Exception as exc:
        return StatusResponse(status="error", message=str(exc))


# ---------------------------------------------------------------------------
# Git — status real, branches, checkout, sync (fetch/pull/push), merge
# ---------------------------------------------------------------------------


class GitStatusResponse(BaseModel):
    is_git_repo: bool = False
    branch: str = ""
    clean: bool = True
    ahead: int = 0
    behind: int = 0


class GitOperationResponse(BaseModel):
    operation: dict[str, object] | None = None


@workspace_scoped_router.get("/git/operation", response_model=GitOperationResponse)
async def git_operation(workspace_id: str) -> GitOperationResponse:
    """Retorna o snapshot terminal ou corrente mais recente do Workbench."""
    from backend.services.git import git_service

    return GitOperationResponse(operation=await git_service.latest(workspace_id))


@workspace_scoped_router.get("/git/status", response_model=GitStatusResponse)
async def git_status(workspace_id: str) -> GitStatusResponse:
    """Estado real do repo: branch, ahead/behind do tracking remoto, clean."""
    from backend.services.git import git_service

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return GitStatusResponse(is_git_repo=False)
    # Defesa em profundidade: o GitStatusBadge/painel Git bate aqui com
    # frequência; qualquer erro inesperado do gitpython (ex.: repo em estado
    # transitório) degrada pra uma resposta válida em vez de 500 no log.
    try:
        info = await git_service.status(workspace_id, repo)
    except Exception:
        logger.exception("api/workspaces: git_status falhou ws=%s", workspace_id)
        return GitStatusResponse(is_git_repo=True)
    branch = info.get("branch", "")
    ahead = info.get("ahead", 0)
    behind = info.get("behind", 0)
    return GitStatusResponse(
        is_git_repo=True,
        branch=branch if isinstance(branch, str) else "",
        clean=bool(info.get("clean", True)),
        ahead=int(ahead) if isinstance(ahead, int | float | str) else 0,
        behind=int(behind) if isinstance(behind, int | float | str) else 0,
    )


class GitBranchesResponse(BaseModel):
    current: str = ""
    branches: list[str] = []
    remotes: list[str] = []


@workspace_scoped_router.get("/git/branches", response_model=GitBranchesResponse)
async def git_branches(workspace_id: str) -> GitBranchesResponse:
    """Lista branches locais e remotas + a branch atual."""
    from backend.tools.git import _git_branch_impl

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return GitBranchesResponse()
    info = _git_branch_impl(repo, "list")
    remotes: list[str] = []
    try:
        raw = repo.git.branch("-r", "--format=%(refname:short)") or ""
        remotes = [
            ln.strip() for ln in raw.splitlines() if ln.strip() and "->" not in ln
        ]
    except Exception:
        pass
    return GitBranchesResponse(
        current=info.get("current", ""),
        branches=info.get("branches", []),
        remotes=remotes,
    )


class GitCheckoutRequest(BaseModel):
    ref: str
    create: bool = False


@workspace_scoped_router.post("/git/checkout", response_model=StatusResponse)
async def git_checkout(workspace_id: str, body: GitCheckoutRequest) -> StatusResponse:
    """Troca de branch/commit; com ``create=true`` cria a branch antes."""
    from backend.tools.git import _git_branch_impl, _git_checkout_impl

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StatusResponse(status="error", message="Repositório git não encontrado.")
    if body.create:
        created = _git_branch_impl(repo, "create", name=body.ref)
        if created.get("status") != "ok":
            return StatusResponse(status="error", message=created.get("message", ""))
    result = _git_checkout_impl(repo, body.ref)
    return StatusResponse(
        status=result.get("status", "error"), message=result.get("message", "")
    )


class GitSyncRequest(BaseModel):
    remote: str = "origin"
    branch: str | None = None


@workspace_scoped_router.post("/git/fetch", response_model=StatusResponse)
async def git_fetch(workspace_id: str, body: GitSyncRequest) -> StatusResponse:
    """``git fetch <remote>`` — atualiza refs remotos sem alterar o worktree."""
    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StatusResponse(status="error", message="Repositório git não encontrado.")
    from backend.services.git import GitOperationError, git_service

    try:
        await git_service.execute(
            workspace_id,
            repo,
            "fetch",
            lambda: repo.git.fetch(body.remote),
        )
        return StatusResponse(status="ok", message=f"fetch {body.remote}")
    except GitOperationError as exc:
        return StatusResponse(status=exc.code, message=str(exc))
    except Exception as exc:
        return StatusResponse(status="error", message=str(exc))


@workspace_scoped_router.post("/git/pull", response_model=StatusResponse)
async def git_pull(workspace_id: str, body: GitSyncRequest) -> StatusResponse:
    """``git pull`` do remote/branch (pode gerar conflitos — ver /git/conflicts)."""
    from backend.services.git import GitOperationError, git_service
    from backend.tools.git import _git_pull_impl

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StatusResponse(status="error", message="Repositório git não encontrado.")
    try:
        _, result = await git_service.execute(
            workspace_id,
            repo,
            "pull",
            lambda: _git_pull_impl(repo, body.remote, body.branch),
        )
        result = result or {}
        return StatusResponse(
            status=str(result.get("status", "error")),
            message=str(result.get("message", "")),
        )
    except GitOperationError as exc:
        return StatusResponse(status=exc.code, message=str(exc))


@workspace_scoped_router.post("/git/push", response_model=StatusResponse)
async def git_push(workspace_id: str, body: GitSyncRequest) -> StatusResponse:
    """``git push`` da branch atual (ou ``branch``) para o remote."""
    from backend.services.git import GitOperationError, git_service
    from backend.tools.git import _git_push_impl

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StatusResponse(status="error", message="Repositório git não encontrado.")
    try:
        _, result = await git_service.execute(
            workspace_id,
            repo,
            "push",
            lambda: _git_push_impl(repo, body.remote, body.branch),
        )
        result = result or {}
        return StatusResponse(
            status=str(result.get("status", "error")),
            message=str(result.get("message", "")),
        )
    except GitOperationError as exc:
        return StatusResponse(status=exc.code, message=str(exc))


class GitMergeRequest(BaseModel):
    branch: str


class GitMergeResponse(BaseModel):
    status: str  # "ok" | "conflict" | "error"
    message: str = ""
    conflicts: list[str] = []


@workspace_scoped_router.post("/git/merge", response_model=GitMergeResponse)
async def git_merge(workspace_id: str, body: GitMergeRequest) -> GitMergeResponse:
    """Faz merge de ``branch`` na branch atual.

    Em conflito, devolve ``status="conflict"`` + a lista de arquivos
    conflitantes (resolver via /git/resolve-conflict).
    """
    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return GitMergeResponse(
            status="error", message="Repositório git não encontrado."
        )
    try:
        repo.git.merge(body.branch)
        return GitMergeResponse(status="ok", message=f"merge {body.branch}")
    except Exception as exc:
        conflicts: list[str] = []
        try:
            raw = repo.git.diff("--name-only", "--diff-filter=U") or ""
            conflicts = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        except Exception:
            pass
        if conflicts:
            return GitMergeResponse(
                status="conflict",
                message=f"merge {body.branch}: conflitos em {len(conflicts)} arquivo(s)",
                conflicts=conflicts,
            )
        return GitMergeResponse(status="error", message=str(exc))


# ---------------------------------------------------------------------------
# Pull Requests (gh CLI) — só quando o remote é GitHub
# ---------------------------------------------------------------------------


class PullRequestInfo(BaseModel):
    number: int
    title: str
    state: str = ""
    author: str = ""
    head: str = ""
    base: str = ""


class PullRequestListResponse(BaseModel):
    available: bool = True  # False quando gh ausente / remote não-GitHub
    prs: list[PullRequestInfo] = []
    message: str = ""


@workspace_scoped_router.get("/pr", response_model=PullRequestListResponse)
async def pr_list(
    workspace_id: str,
    state: Annotated[str, Query()] = "open",
) -> PullRequestListResponse:
    """Lista PRs do repositório via ``gh pr list``."""
    from backend.tools.context import ToolContext
    from backend.tools.gh import _gh_run, _resolve_cwd

    cwd = _resolve_cwd(workspace_id, ToolContext())
    result = await _gh_run(
        [
            "pr",
            "list",
            "--state",
            state,
            "--json",
            "number,title,state,author,headRefName,baseRefName",
        ],
        cwd=cwd,
    )
    if result.get("status") != "ok":
        return PullRequestListResponse(
            available=False, message=result.get("message", "")
        )
    try:
        raw = json.loads(result.get("output") or "[]")
    except json.JSONDecodeError:
        return PullRequestListResponse(available=False, message="gh: saída inválida")
    prs = [
        PullRequestInfo(
            number=int(p.get("number", 0)),
            title=str(p.get("title", "")),
            state=str(p.get("state", "")),
            author=str((p.get("author") or {}).get("login", "")),
            head=str(p.get("headRefName", "")),
            base=str(p.get("baseRefName", "")),
        )
        for p in raw
    ]
    return PullRequestListResponse(prs=prs)


class PullRequestCreateRequest(BaseModel):
    title: str
    body: str = ""
    base: str = "main"
    draft: bool = False


@workspace_scoped_router.post("/pr", response_model=StatusResponse)
async def pr_create(
    workspace_id: str, body: PullRequestCreateRequest
) -> StatusResponse:
    """Cria um PR da branch atual via ``gh pr create``."""
    from backend.tools.context import ToolContext
    from backend.tools.gh import _gh_run, _resolve_cwd

    if not body.title.strip():
        return StatusResponse(status="error", message="Título do PR é obrigatório.")
    cwd = _resolve_cwd(workspace_id, ToolContext())
    args = [
        "pr",
        "create",
        "--title",
        body.title,
        "--body",
        body.body,
        "--base",
        body.base,
    ]
    if body.draft:
        args.append("--draft")
    result = await _gh_run(args, cwd=cwd)
    if result.get("status") == "ok":
        return StatusResponse(status="ok", message=result.get("output", ""))
    return StatusResponse(status="error", message=result.get("message", ""))


# ---------------------------------------------------------------------------
# Abrir no VS Code
# ---------------------------------------------------------------------------


class VscodeOption(BaseModel):
    strategy: str  # "local" | "ssh" | "devcontainer"
    label: str
    url: str  # vscode:// deep-link ou comando sugerido


class VscodeOptionsResponse(BaseModel):
    options: list[VscodeOption]


@workspace_scoped_router.get("/vscode-options", response_model=VscodeOptionsResponse)
async def vscode_options(workspace_id: str) -> VscodeOptionsResponse:
    """Retorna estratégias disponíveis para abrir o workspace no VS Code."""
    from urllib.parse import quote

    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return VscodeOptionsResponse(options=[])

    opts: list[VscodeOption] = []
    cwd = ws.cwd.replace("\\", "/")

    if getattr(ws, "transport", "local") == "local":
        # Estratégia A: vscode://file/<path>
        opts.append(
            VscodeOption(
                strategy="local",
                label="Open in VS Code (local)",
                url=f"vscode://file/{quote(cwd, safe='/:@')}",
            )
        )
        # Estratégia devcontainer (se houver .devcontainer)
        if (Path(ws.cwd) / ".devcontainer").is_dir():
            opts.append(
                VscodeOption(
                    strategy="devcontainer",
                    label="Open in Dev Container",
                    url=f"vscode://ms-vscode-remote.remote-containers/openFolder?folderUri=vscode-local%3A%2F%2F{quote(cwd, safe='/:@')}",
                )
            )
    elif getattr(ws, "transport", "") == "ssh":
        host = getattr(ws, "remote_host", "") or ""
        remote_path = getattr(ws, "remote_path", cwd) or cwd
        opts.append(
            VscodeOption(
                strategy="ssh",
                label=f"Open via SSH ({host})",
                url=f"vscode://vscode-remote/ssh-remote+{quote(host, safe='')}{quote(remote_path, safe='/:@')}",
            )
        )

    return VscodeOptionsResponse(options=opts)


# ---------------------------------------------------------------------------
# Gerenciador de .gitignore
# ---------------------------------------------------------------------------


class GitignorePreviewResponse(BaseModel):
    pattern: str
    matched: list[str]  # caminhos relativos afetados
    total: int


class GitignoreUpdateRequest(BaseModel):
    lines: list[str]  # conteúdo completo do .gitignore


class GitignoreAppendRequest(BaseModel):
    """Regra literal que será adicionada ao .gitignore do workspace."""

    path: str = Field(min_length=1, max_length=512)
    is_folder: bool = False


@workspace_scoped_router.get(
    "/fs/gitignore-preview",
    response_model=GitignorePreviewResponse,
)
async def gitignore_preview(
    workspace_id: str,
    pattern: Annotated[str, Query(min_length=1)],
) -> GitignorePreviewResponse:
    """Previsualiza quais arquivos um padrão .gitignore afetaria."""
    import pathspec

    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return GitignorePreviewResponse(pattern=pattern, matched=[], total=0)

    cwd = Path(ws.cwd)
    spec = pathspec.PathSpec.from_lines("gitignore", [pattern])

    matched: list[str] = []
    exclude = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})
    stack = [cwd]
    while stack and len(matched) < 200:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in exclude:
                    stack.append(entry)
            elif entry.is_file():
                try:
                    rel = str(entry.relative_to(cwd)).replace("\\", "/")
                except ValueError:
                    continue
                if spec.match_file(rel):
                    matched.append(rel)
                    if len(matched) >= 200:
                        break

    return GitignorePreviewResponse(
        pattern=pattern, matched=matched[:200], total=len(matched)
    )


@workspace_scoped_router.get("/fs/gitignore")
async def get_gitignore(workspace_id: str) -> dict:
    """Lê o conteúdo do .gitignore raiz do workspace."""
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return {"content": "", "exists": False}

    gitignore = Path(ws.cwd) / ".gitignore"
    if not gitignore.is_file():
        return {"content": "", "exists": False}

    return {
        "content": gitignore.read_text(encoding="utf-8", errors="replace"),
        "exists": True,
    }


@workspace_scoped_router.put("/fs/gitignore", response_model=StatusResponse)
async def update_gitignore(
    workspace_id: str, body: GitignoreUpdateRequest
) -> StatusResponse:
    """Sobrescreve o .gitignore raiz do workspace."""
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return StatusResponse(status="error", message="Workspace não encontrado.")

    gitignore = Path(ws.cwd) / ".gitignore"
    try:
        content = "\n".join(body.lines)
        if content and not content.endswith("\n"):
            content += "\n"
        gitignore.write_text(content, encoding="utf-8")
        return StatusResponse(status="ok", message=".gitignore atualizado.")
    except OSError as exc:
        return StatusResponse(status="error", message=str(exc))


@workspace_scoped_router.post("/fs/gitignore/append", response_model=StatusResponse)
async def append_gitignore(  # noqa: PLR0911
    workspace_id: str, body: GitignoreAppendRequest
) -> StatusResponse:
    """Acrescenta uma regra ancorada e idempotente ao .gitignore."""
    import pathspec

    from backend.services.security import resolve_within_workspace
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return StatusResponse(status="error", message="Workspace não encontrado.")

    normalized = body.path.replace("\\", "/").strip().strip("/")
    if (
        not normalized
        or "\x00" in normalized
        or any(ord(char) < 32 for char in normalized)
    ):
        return StatusResponse(status="error", message="Caminho inválido.")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return StatusResponse(status="error", message="Caminho inválido.")
    try:
        resolve_within_workspace(normalized, Path(ws.cwd))
    except (OSError, ValueError) as exc:
        return StatusResponse(status="error", message=str(exc))

    escaped = "/".join(
        segment.replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("?", "\\?")
        .replace("[", "\\[")
        for segment in parts
    )
    rule = f"/{escaped}/" if body.is_folder else f"/{escaped}"
    try:
        pathspec.PathSpec.from_lines("gitignore", [rule])
        gitignore = Path(ws.cwd) / ".gitignore"
        existing = gitignore.read_bytes() if gitignore.exists() else b""
        text = existing.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if rule in {line.strip() for line in lines}:
            return StatusResponse(status="ok", message="Regra já presente.")
        newline = "\r\n" if "\r\n" in text else "\n"
        prefix = text
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += newline
        gitignore.write_bytes((prefix + rule + newline).encode("utf-8"))
        return StatusResponse(status="ok", message="Regra adicionada ao .gitignore.")
    except (OSError, UnicodeError, ValueError) as exc:
        return StatusResponse(status="error", message=str(exc))


# ---------------------------------------------------------------------------
# Resolução de conflitos
# ---------------------------------------------------------------------------


class ConflictFile(BaseModel):
    path: str
    conflict_markers: bool = True


class ConflictListResponse(BaseModel):
    files: list[ConflictFile]


class ResolveConflictRequest(BaseModel):
    path: str
    resolution: str  # "ours" | "theirs" | "content"
    content: str | None = None  # usado quando resolution=="content"


@workspace_scoped_router.get("/git/conflicts", response_model=ConflictListResponse)
async def list_conflicts(workspace_id: str) -> ConflictListResponse:
    """Lista arquivos com marcadores de conflito (diff-filter=U)."""
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return ConflictListResponse(files=[])

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return ConflictListResponse(files=[])

    conflict_files: list[ConflictFile] = []
    try:
        raw = repo.git.diff("--name-only", "--diff-filter=U")
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped:
                conflict_files.append(ConflictFile(path=stripped))
    except Exception:
        pass

    return ConflictListResponse(files=conflict_files)


@workspace_scoped_router.post("/git/resolve-conflict", response_model=StatusResponse)
async def resolve_conflict(  # noqa: PLR0911
    workspace_id: str, body: ResolveConflictRequest
) -> StatusResponse:
    """Resolve conflito de merge: ours/theirs ou conteúdo manual."""
    from pathlib import Path

    from backend.services.security import resolve_within_workspace
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return StatusResponse(status="error", message="Workspace não encontrado.")

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StatusResponse(status="error", message="Repositório git não encontrado.")

    full_path = resolve_within_workspace(ws.cwd, body.path)
    if full_path is None:
        return StatusResponse(status="error", message="Caminho fora do workspace.")

    try:
        if body.resolution in ("ours", "theirs"):
            repo.git.checkout(f"--{body.resolution}", "--", body.path)
        elif body.resolution == "content":
            if body.content is None:
                return StatusResponse(
                    status="error",
                    message="Conteúdo obrigatório para resolution=content.",
                )
            Path(full_path).write_text(body.content, encoding="utf-8")
        else:
            return StatusResponse(
                status="error", message=f"Resolução inválida: {body.resolution}"
            )
        repo.git.add("--", body.path)
        return StatusResponse(status="ok", message=f"Conflito resolvido: {body.path}")
    except Exception as exc:
        return StatusResponse(status="error", message=str(exc))


# ---------------------------------------------------------------------------
# Stash Manager
# ---------------------------------------------------------------------------


class StashRequest(BaseModel):
    action: str  # push | pop | drop | list
    name: str | None = None
    index: int | None = None  # para drop de entrada específica


class StashEntry(BaseModel):
    index: int
    label: str  # ex: "stash@{0}: On main: minha mensagem"


class StashResponse(BaseModel):
    action: str
    entries: list[StashEntry] = []
    message: str = ""


@workspace_scoped_router.post("/git/stash", response_model=StashResponse)
async def git_stash(workspace_id: str, body: StashRequest) -> StashResponse:
    """Gerencia stash: push / pop / drop / list."""
    from backend.tools.git import _git_stash_impl

    repo = _open_workspace_repo(workspace_id)
    if repo is None:
        return StashResponse(
            action=body.action, message="Workspace sem repositório git."
        )

    if body.action == "drop" and body.index is not None:
        # drop de entrada específica
        try:
            repo.git.stash("drop", f"stash@{{{body.index}}}")
            result: dict = {"status": "ok", "action": "drop"}
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}
    else:
        result = _git_stash_impl(repo, body.action, body.name)

    if result.get("status") == "error":
        return StashResponse(action=body.action, message=result.get("message", ""))

    # Se list, parseia as entradas.
    if body.action == "list":
        raw_entries: list[str] = result.get("entries", [])
        entries = []
        for i, label in enumerate(raw_entries):
            entries.append(StashEntry(index=i, label=label))
        return StashResponse(action="list", entries=entries)

    return StashResponse(
        action=body.action,
        message=result.get("message", ""),
    )


# ---------------------------------------------------------------------------
# File system CRUD — criação e deleção de arquivos/pastas
# ---------------------------------------------------------------------------


class CreateFsNodeRequest(BaseModel):
    path: str
    content: str = ""


class MoveFsNodeRequest(BaseModel):
    """Requisição de rename/move de arquivo ou pasta dentro do workspace."""

    from_path: str
    to_path: str


@workspace_scoped_router.post("/fs/file", response_model=StatusResponse)
async def create_fs_file(
    workspace_id: str, body: CreateFsNodeRequest
) -> StatusResponse:
    """Cria um arquivo vazio (ou com conteúdo inicial) dentro do workspace."""
    resolved = _resolve_inside(workspace_id, body.path)
    if resolved is None:
        return StatusResponse(
            status="error", message="Caminho inválido ou fora do workspace."
        )
    if resolved.exists():
        return StatusResponse(status="error", message="Arquivo já existe.")
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(body.content, encoding="utf-8")
        return StatusResponse(status="ok")
    except OSError as exc:
        return StatusResponse(status="error", message=str(exc))


# Limite de tamanho para edição inline via textarea (ver `update_fs_file`).
_MAX_FILE_EDIT_SIZE = 2 * 1024 * 1024  # 2 MiB


class UpdateFsFileRequest(BaseModel):
    content: str
    expected_sha256: str | None = None


class FileWriteResponse(BaseModel):
    status: str
    message: str = ""
    sha256: str | None = None


@workspace_scoped_router.put("/fs/file", response_model=FileWriteResponse)
async def update_fs_file(
    workspace_id: str,
    path: Annotated[str, Query()],
    body: UpdateFsFileRequest,
) -> FileWriteResponse:
    """Sobrescreve o conteúdo de um arquivo de texto existente no workspace.

    Controle de conflito otimista: quando `expected_sha256` é informado e não
    bate com o sha256 do conteúdo atual em disco, devolve 412 sem escrever
    nada — o frontend oferece recarregar antes de tentar de novo. Aceita
    apenas texto utf-8/ascii até 2 MiB (acima disso, usar outras ferramentas).
    """
    from fastapi import HTTPException

    resolved = _resolve_inside(workspace_id, path)
    if resolved is None:
        return FileWriteResponse(
            status="error", message="Caminho inválido ou fora do workspace."
        )
    if not resolved.exists() or not resolved.is_file():
        return FileWriteResponse(status="error", message="Arquivo não encontrado.")

    try:
        current_bytes = resolved.read_bytes()
    except OSError as exc:
        return FileWriteResponse(status="error", message=str(exc))

    current_sha256 = hashlib.sha256(current_bytes).hexdigest()
    if body.expected_sha256 is not None and body.expected_sha256 != current_sha256:
        raise HTTPException(
            status_code=412,
            detail="O arquivo foi modificado por fora desde a última leitura.",
        )

    new_bytes: bytes | None = None
    invalid_reason: str | None = None
    try:
        new_bytes = body.content.encode("utf-8")
    except UnicodeEncodeError:
        invalid_reason = "Conteúdo deve ser texto utf-8/ascii."
    if new_bytes is not None and len(new_bytes) > _MAX_FILE_EDIT_SIZE:
        invalid_reason = "Arquivo excede o limite de 2 MiB para edição."
    if invalid_reason is not None or new_bytes is None:
        return FileWriteResponse(
            status="error", message=invalid_reason or "Conteúdo inválido."
        )

    try:
        resolved.write_bytes(new_bytes)
    except OSError as exc:
        return FileWriteResponse(status="error", message=str(exc))

    return FileWriteResponse(status="ok", sha256=hashlib.sha256(new_bytes).hexdigest())


@workspace_scoped_router.post("/fs/dir", response_model=StatusResponse)
async def create_fs_dir(workspace_id: str, body: CreateFsNodeRequest) -> StatusResponse:
    """Cria um diretório dentro do workspace."""
    resolved = _resolve_inside(workspace_id, body.path)
    if resolved is None:
        return StatusResponse(
            status="error", message="Caminho inválido ou fora do workspace."
        )
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        return StatusResponse(status="ok")
    except OSError as exc:
        return StatusResponse(status="error", message=str(exc))


@workspace_scoped_router.delete("/fs", response_model=StatusResponse)
async def delete_fs_node(
    workspace_id: str,
    path: Annotated[str, Query()],
    permanent: Annotated[bool, Query()] = False,
) -> StatusResponse:
    """Move para a lixeira (padrão) ou apaga permanentemente (permanent=true)."""
    import shutil as _shutil

    import send2trash

    resolved = _resolve_inside(workspace_id, path)
    if resolved is None:
        return StatusResponse(
            status="error", message="Caminho inválido ou fora do workspace."
        )
    if not resolved.exists():
        return StatusResponse(status="error", message="Caminho não encontrado.")
    try:
        if permanent:
            if resolved.is_dir():
                _shutil.rmtree(resolved)
            else:
                resolved.unlink()
        else:
            send2trash.send2trash(str(resolved))
        return StatusResponse(status="ok")
    except OSError as exc:
        return StatusResponse(status="error", message=str(exc))


@workspace_scoped_router.post("/fs/move", response_model=StatusResponse)
async def move_fs_node(workspace_id: str, body: MoveFsNodeRequest) -> StatusResponse:
    """Renomeia ou move um arquivo/pasta dentro do workspace.

    Rejeita operações que saiam do sandbox do workspace (ambos os caminhos são
    validados via ``_resolve_inside``) e recusa overwrite quando o destino já
    existe.  Usa ``shutil.move`` para suportar cross-device (ex.: workspace em
    disco diferente do sistema de arquivos padrão).
    """
    import shutil as _shutil

    src = _resolve_inside(workspace_id, body.from_path)
    dst = _resolve_inside(workspace_id, body.to_path)

    if src is None or dst is None:
        return StatusResponse(
            status="error", message="Caminho inválido ou fora do workspace."
        )
    if not src.exists():
        return StatusResponse(status="error", message="Origem não encontrada.")
    if dst.exists():
        return StatusResponse(
            status="error", message="Já existe um arquivo ou pasta com esse nome."
        )
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        _shutil.move(str(src), str(dst))
        return StatusResponse(status="ok")
    except OSError as exc:
        return StatusResponse(status="error", message=str(exc))


# ---------------------------------------------------------------------------
# Busca de texto em arquivos do workspace
# ---------------------------------------------------------------------------


class SearchHit(BaseModel):
    path: str
    line_number: int
    line_text: str


class SearchResponse(BaseModel):
    hits: list[SearchHit]
    truncated: bool = False


def _python_text_search(
    cwd: Path,
    query: str,
    max_hits: int = 200,
    max_columns: int = 200,
) -> tuple[list[SearchHit], bool]:
    """Busca textual (case-insensitive) com Python puro — fallback sem ripgrep.

    Percorre o workspace recursivamente ignorando diretórios de build/cache e
    retorna as linhas que contêm ``query``.  Arquivos maiores que 1 MiB são
    pulados silenciosamente.  Retorna ``(hits, truncated)``; ``truncated=True``
    quando o total de resultados foi cortado em ``max_hits``.
    """
    import re as _re

    exclude: frozenset[str] = frozenset(
        {
            ".git",
            ".hg",
            ".svn",
            "node_modules",
            "__pycache__",
            ".mypy_cache",
            ".ruff_cache",
            ".pytest_cache",
            ".tox",
            ".nox",
            "dist",
            "build",
            ".next",
            ".nuxt",
            ".svelte-kit",
            "target",
            "venv",
            ".venv",
            "env",
            ".vectora",
        }
    )
    max_file_bytes = 1 * 1024 * 1024  # 1 MiB

    hits: list[SearchHit] = []
    pattern = _re.compile(_re.escape(query), _re.IGNORECASE)

    stack = [cwd]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in exclude:
                    stack.append(entry)
            elif entry.is_file():
                try:
                    size = entry.stat().st_size
                except OSError:
                    continue
                if size > max_file_bytes:
                    continue
                try:
                    text = entry.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if pattern.search(line):
                        rel = str(entry.relative_to(cwd)).replace("\\", "/")
                        hits.append(
                            SearchHit(
                                path=rel,
                                line_number=lineno,
                                line_text=line[:max_columns],
                            )
                        )
                        if len(hits) >= max_hits:
                            return hits, True
    return hits, False


@workspace_scoped_router.get("/fs/search", response_model=SearchResponse)
async def search_workspace_files(
    workspace_id: str,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    path: Annotated[str, Query()] = "",
) -> SearchResponse:
    """Busca textual no conteúdo dos arquivos do workspace.

    Tenta ``rg`` (ripgrep) primeiro com ``--max-filesize 1M --max-count 50
    --max-columns 200`` (respeita ``.gitignore``, timeout 30s); cai para
    busca Python pura quando ``rg`` não está no PATH.  Retorna até 200
    resultados; ``truncated=true`` indica que o resultado foi cortado.
    """
    import json as _json
    import shutil as _shutil
    import subprocess as _subprocess  # nosec B404

    cwd_root = _resolve_inside(workspace_id, path)
    if cwd_root is None or not cwd_root.is_dir():
        return SearchResponse(hits=[], truncated=False)

    # Tenta ripgrep (rápido, respeita .gitignore).
    rg_bin = _shutil.which("rg")
    if rg_bin:
        try:
            proc = _subprocess.run(  # noqa: ASYNC221 S603  # nosec B603
                [
                    rg_bin,
                    "--json",
                    "--max-filesize",
                    "1M",
                    "--max-count",
                    "50",
                    "--max-columns",
                    "200",
                    "--",
                    q,
                    str(cwd_root),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            rg_hits: list[SearchHit] = []
            rg_truncated = False
            for rg_line in proc.stdout.splitlines():
                stripped = rg_line.strip()
                if not stripped:
                    continue
                try:
                    obj = _json.loads(stripped)
                except ValueError:
                    continue
                if obj.get("type") == "match":
                    data = obj.get("data", {})
                    fpath = data.get("path", {}).get("text", "")
                    try:
                        rel = str(Path(fpath).relative_to(cwd_root)).replace("\\", "/")
                    except ValueError:
                        rel = fpath
                    lineno: int = data.get("line_number", 0)
                    line_text = (
                        data.get("lines", {}).get("text", "").rstrip("\n\r")[:200]
                    )
                    rg_hits.append(
                        SearchHit(path=rel, line_number=lineno, line_text=line_text)
                    )
                    if len(rg_hits) >= 200:
                        rg_truncated = True
                        break
            return SearchResponse(hits=rg_hits, truncated=rg_truncated)
        except (_subprocess.TimeoutExpired, OSError):
            logger.warning("search_workspace_files: rg falhou, usando fallback Python")

    # Fallback: Python puro.
    py_hits, py_truncated = _python_text_search(cwd_root, q)
    return SearchResponse(hits=py_hits, truncated=py_truncated)


# ---------------------------------------------------------------------------
# Stack hint (detects project type for contextual empty-state prompts)
# ---------------------------------------------------------------------------

_STACK_MARKERS: list[tuple[str, str]] = [
    # (filename, stack_key)
    ("package.json", "nodejs"),
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("requirements.txt", "python"),
    ("go.mod", "go"),
    ("Cargo.toml", "rust"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    ("build.gradle.kts", "java"),
    ("composer.json", "php"),
    ("Gemfile", "ruby"),
    ("mix.exs", "elixir"),
    ("pubspec.yaml", "dart"),
    ("CMakeLists.txt", "cpp"),
    ("Makefile", "make"),
]


class StackHintResponse(BaseModel):
    stack: str  # e.g. "nodejs", "python", "go", "rust", "unknown"


@workspace_scoped_router.get("/stack-hint", response_model=StackHintResponse)
async def stack_hint(workspace_id: str) -> StackHintResponse:
    """Detects the primary technology stack of the workspace by inspecting
    well-known marker files in the root directory.

    Returns ``stack="unknown"`` when no marker is found.
    """
    from fastapi import HTTPException

    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    root = Path(ws.cwd)
    for filename, stack_key in _STACK_MARKERS:
        if (root / filename).exists():
            return StackHintResponse(stack=stack_key)

    return StackHintResponse(stack="unknown")


# ---------------------------------------------------------------------------
# File watcher SSE
# ---------------------------------------------------------------------------

_WATCHER_DEBOUNCE_S = 0.3  # 300ms
_WATCHER_CAP = 100  # máx paths por evento


@workspace_scoped_router.get("/events")
async def workspace_events(workspace_id: str, request: Request) -> StreamingResponse:
    """Emite eventos SSE ``fs_changed`` quando arquivos do workspace mudam.

    Usa ``watchdog`` para monitorar o diretório em thread separada e debounce
    de 300ms para não inundar o cliente com eventos individuais.
    Máximo de 100 paths por evento (os demais são descartados — client
    deve fazer diff completo).
    """
    import asyncio
    import json as _json
    import time
    from pathlib import Path

    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:

        async def _not_found() -> AsyncGenerator[str]:
            yield 'data: {"type": "error", "message": "Workspace não encontrado."}\n\n'

        return StreamingResponse(_not_found(), media_type="text/event-stream")

    cwd = str(Path(ws.cwd).resolve())

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[list[str]] = asyncio.Queue(maxsize=50)

    # Debounce: acumula paths alterados e envia em lote a cada 300ms
    _pending: list[str] = []
    _last_flush: float = 0.0

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event: FileSystemEvent) -> None:
            nonlocal _pending, _last_flush
            src = getattr(event, "src_path", "")
            # Ignora diretórios e arquivos ocultos de controle
            if not src or event.is_directory:  # type: ignore[attr-defined]
                return
            try:
                rel = str(Path(src).relative_to(cwd)).replace("\\", "/")
            except ValueError:
                rel = src
            _pending.append(rel)
            now = time.monotonic()
            if now - _last_flush >= _WATCHER_DEBOUNCE_S:
                _last_flush = now
                paths = _pending[:_WATCHER_CAP]
                _pending.clear()
                asyncio.run_coroutine_threadsafe(queue.put(paths), loop)

    observer = Observer()
    # daemon=True: se o cliente cair e o ``finally`` que faz observer.stop()
    # não rodar (ex.: stream não-drenado em testes), a thread do watchdog não
    # impede o shutdown do processo — caso contrário o interpretador trava no
    # ``threading._shutdown()`` aguardando essa thread (travava a CI).
    observer.daemon = True
    observer.schedule(_Handler(), cwd, recursive=True)
    observer.start()

    async def _stream() -> AsyncGenerator[str]:
        try:
            # keepalive heartbeat + event loop
            while True:
                if await request.is_disconnected():
                    break
                try:
                    paths = await asyncio.wait_for(queue.get(), timeout=15.0)
                    payload = _json.dumps({"type": "fs_changed", "paths": paths})
                    yield f"data: {payload}\n\n"
                except TimeoutError:
                    # heartbeat
                    yield ": keepalive\n\n"
        finally:
            observer.stop()
            observer.join(timeout=5)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Browser — launch.json + dev server management
# ---------------------------------------------------------------------------

import asyncio as _asyncio
import collections as _collections
import time as _time

from backend.services.subprocess_logging import pipe_to_logger

_browser_procs: dict[str, _asyncio.subprocess.Process] = {}
_browser_log_tasks: dict[str, _asyncio.Task] = {}
# Últimas linhas de stdout/stderr por dev server — sobrevive ao processo
# encerrar/morrer (só é sobrescrito por um deque novo no próximo start),
# pra diagnóstico continuar disponível mesmo depois de um crash. Lido tanto
# pelo endpoint HTTP (UI) quanto pelas tools do agente (mesma fonte de dado).
_BROWSER_LOG_MAXLEN = 500
_browser_log_buffers: dict[str, _collections.deque[str]] = {}


def _browser_key(workspace_id: str, name: str) -> str:
    return f"{workspace_id}::{name}"


async def _is_port_open(host: str, port: int, timeout_s: float = 0.3) -> bool:
    """Sonda TCP curta — só considera um dev server "no ar" quando a porta
    aceita conexão, não quando o processo apenas existe (compilação inicial
    de vite/next tipicamente ainda não está escutando)."""
    try:
        _, writer = await _asyncio.wait_for(
            _asyncio.open_connection(host, port), timeout=timeout_s
        )
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


async def _wait_port_open(
    host: str, port: int, *, total_timeout: float = 15.0, interval: float = 0.5
) -> bool:
    """Poll limitado até a porta abrir — usado só no `browser/start` pra não
    responder "ok" com o dev server ainda compilando."""
    deadline = _time.monotonic() + total_timeout
    while _time.monotonic() < deadline:
        if await _is_port_open(host, port):
            return True
        await _asyncio.sleep(interval)
    return await _is_port_open(host, port)


async def _wait_port_open_or_exit(
    proc: _asyncio.subprocess.Process,
    host: str,
    port: int,
    *,
    total_timeout: float = 15.0,
    interval: float = 0.5,
) -> tuple[bool, int | None]:
    """Como `_wait_port_open`, mas encerra cedo se o processo morrer —
    evita gastar os `total_timeout` segundos inteiros fazendo polling numa
    porta que já sabemos que nunca vai abrir. Retorna
    ``(porta_aberta, exit_code)`` — `exit_code` é `None` enquanto vivo."""
    deadline = _time.monotonic() + total_timeout
    while _time.monotonic() < deadline:
        if proc.returncode is not None:
            return False, proc.returncode
        if await _is_port_open(host, port):
            return True, None
        await _asyncio.sleep(interval)
    if proc.returncode is not None:
        return False, proc.returncode
    return await _is_port_open(host, port), None


def _launch_json_path(workspace_id: str) -> Path | None:
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return None
    return Path(ws.cwd) / ".vectora" / "launch.json"


class LaunchConfigModel(BaseModel):
    # Campos em camelCase espelham o formato do .vectora/launch.json
    # esperado pelo arquivo de configuração — não renomear.
    name: str
    runtimeExecutable: str  # noqa: N815
    runtimeArgs: list[str] = []  # noqa: N815
    port: int
    env: dict[str, str] = {}


class LaunchJsonModel(BaseModel):
    version: str = "0.0.1"
    configurations: list[LaunchConfigModel] = []


class BrowserServerStatus(BaseModel):
    name: str
    port: int
    running: bool
    pid: int | None = None


class BrowserStatusResponse(BaseModel):
    servers: list[BrowserServerStatus]


class BrowserStartRequest(BaseModel):
    name: str


class BrowserStopRequest(BaseModel):
    name: str


class BrowserLogsResponse(BaseModel):
    lines: list[str]


class DetectedServer(BaseModel):
    name: str
    runtimeExecutable: str  # noqa: N815
    runtimeArgs: list[str]  # noqa: N815
    port: int


class DetectResponse(BaseModel):
    configurations: list[DetectedServer]


@workspace_scoped_router.get("/browser/launch", response_model=LaunchJsonModel)
async def get_launch_json(workspace_id: str) -> LaunchJsonModel:
    """Lê .vectora/launch.json do workspace."""
    p = _launch_json_path(workspace_id)
    if p is None or not p.exists():
        return LaunchJsonModel()
    try:
        import json as _json

        data = _json.loads(p.read_text(encoding="utf-8"))
        return LaunchJsonModel.model_validate(data)
    except Exception:
        return LaunchJsonModel()


@workspace_scoped_router.post("/browser/launch", response_model=StatusResponse)
async def save_launch_json(workspace_id: str, body: LaunchJsonModel) -> StatusResponse:
    """Grava .vectora/launch.json no workspace."""
    p = _launch_json_path(workspace_id)
    if p is None:
        return StatusResponse(status="error", message="Workspace não encontrado.")
    try:
        import json as _json

        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            _json.dumps(body.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return StatusResponse(status="ok")
    except Exception as exc:
        return StatusResponse(status="error", message=str(exc))


@workspace_scoped_router.get("/browser/status", response_model=BrowserStatusResponse)
async def browser_status(workspace_id: str) -> BrowserStatusResponse:
    """Retorna o status dos dev servers do workspace."""
    launch = await get_launch_json(workspace_id)
    servers: list[BrowserServerStatus] = []
    for cfg in launch.configurations:
        key = _browser_key(workspace_id, cfg.name)
        proc = _browser_procs.get(key)
        alive = proc is not None and proc.returncode is None
        # "Rodando" é definido pela porta estar aceitando conexão, não pelo
        # PID rastreado ainda existir — comandos como `turbo run dev`/`pnpm
        # dev` no Windows frequentemente encerram o processo pai (o PID que
        # `browser_start` capturou) enquanto os processos reais (Next.js,
        # etc.) seguem vivos em processos/consoles próprios, desanexados.
        # Confiar só no PID fazia o ícone nunca virar "rodando" e a
        # navegação automática nunca disparar mesmo com o servidor no ar.
        running = await _is_port_open("127.0.0.1", cfg.port)
        pid = proc.pid if alive and proc else None
        servers.append(
            BrowserServerStatus(name=cfg.name, port=cfg.port, running=running, pid=pid)
        )
    return BrowserStatusResponse(servers=servers)


@workspace_scoped_router.get("/browser/logs", response_model=BrowserLogsResponse)
async def browser_logs(workspace_id: str, name: str) -> BrowserLogsResponse:
    """Últimas linhas de stdout/stderr do dev server `name` — disponível mesmo
    com o processo parado/morto (buffer não é limpo em browser_stop nem
    quando o processo encerra sozinho). Lista vazia = nunca foi iniciado."""
    key = _browser_key(workspace_id, name)
    buf = _browser_log_buffers.get(key)
    return BrowserLogsResponse(lines=list(buf) if buf else [])


@workspace_scoped_router.post("/browser/start", response_model=StatusResponse)
async def browser_start(workspace_id: str, body: BrowserStartRequest) -> StatusResponse:
    """Inicia o dev server com o nome indicado."""
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return StatusResponse(status="error", message="Workspace não encontrado.")

    launch = await get_launch_json(workspace_id)
    cfg = next((c for c in launch.configurations if c.name == body.name), None)
    if cfg is None:
        return StatusResponse(
            status="error", message=f"Configuração '{body.name}' não encontrada."
        )

    key = _browser_key(workspace_id, cfg.name)
    existing = _browser_procs.get(key)
    if existing and existing.returncode is None:
        return StatusResponse(status="ok", message="já em execução")

    env = {**__import__("os").environ, **cfg.env}
    cmd = [cfg.runtimeExecutable, *cfg.runtimeArgs]
    try:
        proc = await _asyncio.create_subprocess_exec(
            *cmd,
            cwd=ws.cwd,
            env=env,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.STDOUT,
        )
        _browser_procs[key] = proc
        buf: _collections.deque[str] = _collections.deque(maxlen=_BROWSER_LOG_MAXLEN)
        _browser_log_buffers[key] = buf
        _browser_log_tasks[key] = _asyncio.create_task(
            pipe_to_logger(
                proc.stdout,
                logger,
                prefix=f"browser:{cfg.name}",
                on_line=buf.append,
            )
        )
        port_ready, exit_code = await _wait_port_open_or_exit(
            proc, "127.0.0.1", cfg.port
        )
        if exit_code is not None:
            status, message = (
                "error",
                (
                    f"processo encerrou com código {exit_code} antes de abrir "
                    f"a porta {cfg.port} — ver saída no terminal do Vectora"
                ),
            )
        elif not port_ready:
            status, message = (
                "pending",
                f"processo iniciado na porta {cfg.port}, ainda compilando/subindo",
            )
        else:
            status, message = "ok", f"pronto na porta {cfg.port}"
    except Exception as exc:
        status, message = "error", str(exc)
    return StatusResponse(status=status, message=message)


@workspace_scoped_router.post("/browser/stop", response_model=StatusResponse)
async def browser_stop(workspace_id: str, body: BrowserStopRequest) -> StatusResponse:
    """Para o dev server com o nome indicado."""
    launch = await get_launch_json(workspace_id)
    cfg = next((c for c in launch.configurations if c.name == body.name), None)
    if cfg is None:
        return StatusResponse(
            status="error", message=f"Configuração '{body.name}' não encontrada."
        )

    key = _browser_key(workspace_id, cfg.name)
    proc = _browser_procs.pop(key, None)
    log_task = _browser_log_tasks.pop(key, None)
    if log_task is not None:
        log_task.cancel()
    if proc is None or proc.returncode is not None:
        return StatusResponse(status="ok", message="não estava em execução")

    try:
        proc.terminate()
        try:
            await _asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            proc.kill()
        return StatusResponse(status="ok", message="parado")
    except Exception as exc:
        return StatusResponse(status="error", message=str(exc))


@workspace_scoped_router.get("/browser/detect", response_model=DetectResponse)
async def browser_detect(workspace_id: str) -> DetectResponse:
    """Detecta dev servers comuns no workspace e sugere configurações para launch.json."""
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        return DetectResponse(configurations=[])

    root = Path(ws.cwd)
    configs: list[DetectedServer] = []

    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            import json as _json

            pkg = _json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts: dict = pkg.get("scripts", {})
            _dev_scripts = ["dev", "start", "serve", "preview"]
            for script_name in _dev_scripts:
                if script_name in scripts:
                    mgr = "pnpm"
                    from shutil import which as _which

                    if not _which("pnpm"):
                        mgr = "npm" if _which("npm") else "node"
                    configs.append(
                        DetectedServer(
                            name=f"npm {script_name}",
                            runtimeExecutable=mgr,
                            runtimeArgs=["run", script_name],
                            port=3000,
                        )
                    )
                    break
        except Exception:
            pass

    pyproject = root / "pyproject.toml"
    manage_py = root / "manage.py"
    if manage_py.exists():
        configs.append(
            DetectedServer(
                name="django",
                runtimeExecutable="python",
                runtimeArgs=["manage.py", "runserver", "8000"],
                port=8000,
            )
        )
    elif pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            if "fastapi" in content or "uvicorn" in content or "starlette" in content:
                configs.append(
                    DetectedServer(
                        name="fastapi",
                        runtimeExecutable="uvicorn",
                        runtimeArgs=["main:app", "--reload", "--port", "8000"],
                        port=8000,
                    )
                )
        except Exception:
            pass

    vite_cfg = root / "vite.config.ts"
    if not configs and vite_cfg.exists():
        configs.append(
            DetectedServer(
                name="vite",
                runtimeExecutable="pnpm",
                runtimeArgs=["dev"],
                port=5173,
            )
        )

    return DetectResponse(configurations=configs)


# ---------------------------------------------------------------------------
# Browser devtools — espelha backend/tools/browser_devtools.py em REST pro
# painel visual do workbench (frontend/components/workbench/tabs/
# browser-devtools-panel.tsx). Sessão é a do AGENTE (Playwright headless,
# backend/browser/session.py) — distinta da view que o usuário vê no resto
# da aba Browser (iframe/WebContentsView), que não expõe console/network.
# ---------------------------------------------------------------------------


class DevtoolsTab(BaseModel):
    tab_id: str
    url: str
    active: bool


class DevtoolsTabsResponse(BaseModel):
    tabs: list[DevtoolsTab]


class DevtoolsConsoleMessage(BaseModel):
    type: str
    text: str


class DevtoolsConsoleResponse(BaseModel):
    messages: list[DevtoolsConsoleMessage]


class DevtoolsNetworkRequest(BaseModel):
    request_id: str
    url: str
    method: str
    resource_type: str
    status: int | str | None = None
    error: str | None = None


class DevtoolsNetworkResponse(BaseModel):
    requests: list[DevtoolsNetworkRequest]


class DevtoolsEvaluateRequest(BaseModel):
    script: str
    tab_id: str | None = None


class DevtoolsEvaluateResponse(BaseModel):
    status: str
    result: Any = None
    error: str | None = None


@workspace_scoped_router.get(
    "/browser/devtools/tabs", response_model=DevtoolsTabsResponse
)
async def devtools_tabs(workspace_id: str) -> DevtoolsTabsResponse:
    """Abas da sessão de browser do agente — vazio se nenhuma sessão existe
    ainda (agente nunca navegou/abriu aba nesse workspace)."""
    from backend.browser.session import list_tabs

    tabs = await list_tabs(workspace_id)
    return DevtoolsTabsResponse(tabs=[DevtoolsTab(**t) for t in tabs])


@workspace_scoped_router.get(
    "/browser/devtools/console",
    response_model=DevtoolsConsoleResponse,
)
async def devtools_console(
    workspace_id: str, tab_id: str | None = None, limit: int = 200
) -> DevtoolsConsoleResponse:
    """Mensagens de console capturadas ao vivo da aba do agente."""
    from backend.browser.session import get_tab_state

    tab = get_tab_state(workspace_id, tab_id)
    if tab is None:
        return DevtoolsConsoleResponse(messages=[])
    messages = list(tab.console_log)[-limit:]
    return DevtoolsConsoleResponse(
        messages=[DevtoolsConsoleMessage(**m) for m in messages]
    )


@workspace_scoped_router.delete(
    "/browser/devtools/console", response_model=StatusResponse
)
async def devtools_clear_console(
    workspace_id: str, tab_id: str | None = None
) -> StatusResponse:
    """Limpa o buffer de console capturado da aba (não afeta o console real)."""
    from backend.browser.session import get_tab_state

    tab = get_tab_state(workspace_id, tab_id)
    if tab is None:
        return StatusResponse(status="error", message="nenhuma sessão/aba encontrada")
    tab.console_log.clear()
    return StatusResponse(status="ok")


@workspace_scoped_router.get(
    "/browser/devtools/network",
    response_model=DevtoolsNetworkResponse,
)
async def devtools_network(
    workspace_id: str,
    tab_id: str | None = None,
    resource_type: str | None = None,
    limit: int = 200,
) -> DevtoolsNetworkResponse:
    """Requisições de rede capturadas ao vivo da aba do agente."""
    from backend.browser.session import get_tab_state

    tab = get_tab_state(workspace_id, tab_id)
    if tab is None:
        return DevtoolsNetworkResponse(requests=[])
    entries = list(tab.network_log)
    if resource_type:
        entries = [e for e in entries if e.get("resource_type") == resource_type]
    return DevtoolsNetworkResponse(
        requests=[DevtoolsNetworkRequest(**e) for e in entries[-limit:]]
    )


@workspace_scoped_router.post(
    "/browser/devtools/evaluate",
    response_model=DevtoolsEvaluateResponse,
)
async def devtools_evaluate(
    workspace_id: str, body: DevtoolsEvaluateRequest
) -> DevtoolsEvaluateResponse:
    """Executa JS arbitrário na aba do agente — mesmo caminho que a tool
    `browser_evaluate`, exposto ao painel visual pra inspeção manual do DOM."""
    from backend.browser.session import get_tab_state

    tab = get_tab_state(workspace_id, body.tab_id)
    if tab is None:
        return DevtoolsEvaluateResponse(
            status="error", error="nenhuma sessão/aba encontrada"
        )
    try:
        result = await tab.page.evaluate(body.script)
        return DevtoolsEvaluateResponse(status="ok", result=result)
    except Exception as exc:
        logger.exception("devtools_evaluate failed")
        return DevtoolsEvaluateResponse(status="error", error=str(exc))


# ---------------------------------------------------------------------------
# RAG — indexação direta de pasta (sem passar pelo chat) + progresso por job
# ---------------------------------------------------------------------------


class RagIngestRequest(BaseModel):
    """Pedido de indexação de uma pasta no RAG."""

    path: str
    # "code" | "markdown" | "all", ou uma lista de extensões customizadas
    # (ex. ["xml"]) — indexa só arquivos com essas extensões.
    file_types: str | list[str] = "all"
    # Filtros de extensão (string CSV ou lista) que sobrepõem o atalho
    # file_types: include_exts restringe, exclude_exts sempre remove.
    include_exts: str | list[str] | None = None
    exclude_exts: str | list[str] | None = None
    bucket_name: str | None = None


class RagIngestResponse(BaseModel):
    job_id: str
    total_files: int
    total_chunks: int
    status: str
    bucket_id: str


class RagIngestPreviewResponse(BaseModel):
    total: int
    files: list[str]


class RagJobStatus(BaseModel):
    job_id: str
    path: str
    total: int
    processed: int
    failed: int
    status: str  # "indexing" | "done" | "failed" | "no_files" | "paused"
    # Preenchido quando o worker pausou por rate limit (motivo exibido ao usuário).
    error_reason: str | None = None


# Registro em memória dos jobs disparados neste processo (path + total para a
# barra de progresso). O progresso real vem da embedding queue (get_job_stats).
_RAG_JOBS: dict[str, dict[str, Any]] = {}
# Mantém referência às tasks de enqueue em andamento (evita GC prematuro).
_RAG_TASKS: set[Any] = set()
# Última assinatura "status:faixa_de_progresso" emitida por job — evita
# reemitir o mesmo evento em toda escrita da fila (mark_success dispara a
# cada chunk, mas só interessa notificar em mudanças de status ou saltos de
# ~5% de progresso).
_RAG_LAST_EMITTED_STATUS: dict[str, str] = {}


def _rag_job_status(job_id: str, stats: dict[str, int]) -> RagJobStatus:
    from backend.embedding.background import get_worker_pause_state

    meta = _RAG_JOBS.get(job_id, {})
    enqueue_done = bool(meta.get("enqueue_done"))
    declared = meta.get("total_chunks")
    # Enquanto o enqueue roda em background, o total cresce — usa a contagem
    # viva da fila; quando o enqueue termina, fixa no total declarado.
    total = int(declared) if declared is not None else int(stats.get("total") or 0)
    processed = int(stats.get("success", 0))
    failed = int(stats.get("failed", 0)) + int(stats.get("dlq", 0))

    paused, reason = get_worker_pause_state()
    if enqueue_done and total == 0:
        status = "no_files"
    elif enqueue_done and processed + failed >= total:
        status = "done" if failed < total else "failed"
    elif paused and processed < total:
        # Circuit breaker arquivou a fila — o job não termina sozinho.
        status = "paused"
    else:
        status = "indexing"
    return RagJobStatus(
        job_id=job_id,
        path=str(meta.get("path", "")),
        total=total,
        processed=processed,
        failed=failed,
        status=status,
        error_reason=reason if paused else None,
    )


async def _maybe_emit_job_event(job_id: str) -> None:
    """Recomputa o status agregado do job e emite via SSE (provider=`rag`) só
    quando muda em relação à última emissão — chamado nos pontos onde
    progresso real acontece na fila (worker grava sucesso/DLQ) ou o enqueue
    termina. O frontend troca o polling de 1.2s em `rag-jobs-store.ts` por
    esse evento (mesma ponte cross-réplica de `webhooks.py::_emit_sse_event`,
    já usada por `background_tasks.py` pras Tarefas do workbench).

    A dedupe usa status + faixa de 5% de progresso (não só status) — senão a
    barra de progresso ficaria congelada do início ao fim do "indexing" e só
    pularia pra "done", perdendo a granularidade que o polling tinha.
    """
    if job_id not in _RAG_JOBS:
        return
    from backend.embedding.queue import get_embedding_queue
    from backend.settings import settings

    try:
        queue = await get_embedding_queue(settings.embedding_queue_dsn)
        stats = await queue.get_job_stats(job_id)
    except Exception:
        logger.warning("rag_job_event_stats_failed", extra={"job_id": job_id})
        return

    status_obj = _rag_job_status(job_id, stats)
    done_count = status_obj.processed + status_obj.failed
    progress_bucket = (
        (done_count * 20) // status_obj.total if status_obj.total > 0 else 0
    )
    signature = f"{status_obj.status}:{progress_bucket}"
    if _RAG_LAST_EMITTED_STATUS.get(job_id) == signature:
        return
    _RAG_LAST_EMITTED_STATUS[job_id] = signature

    from backend.api.handlers.webhooks import _emit_sse_event

    _emit_sse_event(
        "rag",
        f"rag_job.{status_obj.status}",
        {
            "job_id": job_id,
            "workspace_id": _RAG_JOBS.get(job_id, {}).get("workspace_id"),
            "total": status_obj.total,
            "processed": status_obj.processed,
            "failed": status_obj.failed,
            "status": status_obj.status,
            "error_reason": status_obj.error_reason,
        },
    )


class RagBucketResponse(BaseModel):
    id: str
    name: str
    description_md: str
    source_path: str | None
    created_at: str
    active: bool


@workspace_scoped_router.get("/rag/buckets", response_model=list[RagBucketResponse])
async def list_rag_buckets(workspace_id: str) -> list[RagBucketResponse]:
    """Buckets do workspace — usado pelo seletor de publicação da Memory
    Library e pelo painel de buckets do Memory tab. `active` reflete se o
    bucket está na lista de busca ativa deste workspace
    (`get_active_bucket_ids`)."""
    from backend.services import rag_buckets
    from backend.workspace.runtime_settings import runtime_settings

    buckets = rag_buckets.list_buckets(runtime_settings, workspace_id)
    active_ids = set(rag_buckets.get_active_bucket_ids(runtime_settings, workspace_id))
    return [
        RagBucketResponse(
            id=b.id,
            name=b.name,
            description_md=b.description_md,
            source_path=b.source_path,
            created_at=b.created_at,
            active=b.id in active_ids,
        )
        for b in buckets
    ]


class RagBucketToggleRequest(BaseModel):
    active: bool


@workspace_scoped_router.patch(
    "/rag/buckets/{bucket_id}", response_model=RagBucketToggleRequest
)
async def toggle_rag_bucket(
    workspace_id: str, bucket_id: str, body: RagBucketToggleRequest
) -> RagBucketToggleRequest:
    """Ativa/desativa `bucket_id` na busca do workspace — reaproveita
    `rag_buckets.set_active`, sem lógica nova."""
    from backend.services import rag_buckets
    from backend.workspace.runtime_settings import runtime_settings

    rag_buckets.set_active(
        runtime_settings,
        workspace_id=workspace_id,
        bucket_id=bucket_id,
        active=body.active,
    )
    return body


@workspace_scoped_router.delete("/rag/buckets/{bucket_id}")
async def delete_rag_bucket(workspace_id: str, bucket_id: str) -> dict:
    """Remove o bucket do catálogo, de qualquer lista de ativos, E a tabela
    vetorial (`bucket_{bucket_id}`) — sem o purge, `/rag/workspace-summary`
    (aba Memória → Coleções) continuava listando a tabela órfã com todos os
    itens, mesmo com "Buckets indexados" já mostrando vazio. Erro de purge
    não impede a remoção do catálogo — a tabela pode já não existir
    (bucket criado mas nunca ingerido) ou o storage estar indisponível."""
    from backend.services import rag_buckets
    from backend.storage.factory import get_vector_store_backend
    from backend.workspace.runtime_settings import runtime_settings

    rag_buckets.delete_bucket(runtime_settings, bucket_id)
    try:
        backend = await get_vector_store_backend()
        await backend.purge(f"bucket_{bucket_id}")
    except Exception:
        logger.warning(
            "rag_buckets: falha ao purgar tabela do bucket %s", bucket_id, exc_info=True
        )
    return {"ok": True}


class MemoryIndexHitResponse(BaseModel):
    type: str
    id: str
    title: str
    snippet: str
    score: float | None = None


class MemorySearchResponse(BaseModel):
    hits: list[MemoryIndexHitResponse]


@workspace_scoped_router.get("/memory/search", response_model=MemorySearchResponse)
async def search_memory_unified(
    workspace_id: str,
    request: Request,
    q: str = "",
    types: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MemorySearchResponse:
    """Busca combinada em fatos + skills + buckets RAG — a mesma consulta
    que popula a busca da Memory Tab. `types` é uma lista separada por
    vírgula (`fact,skill,rag_bucket`); valores desconhecidos são ignorados,
    nunca derrubam a query."""
    from backend.services.memory_index import (
        ALL_MEMORY_HIT_TYPES,
        MemoryHitType,
        search_unified_memory,
    )

    ws = require_workspace_access(workspace_id, request)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace não encontrado")

    wanted: frozenset[MemoryHitType] | None = None
    if types:
        parsed = {t.strip() for t in types.split(",") if t.strip()}
        narrowed: frozenset[MemoryHitType] = frozenset(
            t for t in ALL_MEMORY_HIT_TYPES if t in parsed
        )
        wanted = narrowed or None

    hits = await search_unified_memory(
        q,
        user_id=_user_id(request),
        workspace_id=workspace_id,
        types=wanted,
        limit=limit,
    )
    return MemorySearchResponse(
        hits=[
            MemoryIndexHitResponse(
                type=h.type, id=h.id, title=h.title, snippet=h.snippet, score=h.score
            )
            for h in hits
        ]
    )


@workspace_scoped_router.post("/rag/ingest", response_model=RagIngestResponse)
async def rag_ingest(workspace_id: str, body: RagIngestRequest) -> RagIngestResponse:
    """Indexa uma pasta no RAG diretamente (walk + chunk + enqueue por job).

    Valida o caminho na hora (erro imediato se inválido) e enfileira os chunks
    em uma **task de fundo** — uma pasta grande gera centenas de inserts e não
    deve bloquear a request (nem morrer com ela). O progresso é acompanhado via
    ``GET /rag/jobs/{job_id}``.
    """
    from pathlib import Path
    from uuid import uuid4

    from fastapi import HTTPException

    from backend.embedding.rag_ingest import ingest_directory
    from backend.services import rag_buckets
    from backend.services.security import is_safe_file_path
    from backend.workspace.runtime_settings import runtime_settings

    if not is_safe_file_path(body.path) or not Path(body.path).is_dir():
        raise HTTPException(
            status_code=400, detail="Caminho inválido ou fora do escopo."
        )

    bucket_name = body.bucket_name or Path(body.path).name or body.path
    bucket = rag_buckets.create_bucket(
        runtime_settings,
        workspace_id=workspace_id,
        name=bucket_name,
        source_path=body.path,
    )
    rag_buckets.set_active(
        runtime_settings, workspace_id=workspace_id, bucket_id=bucket.id, active=True
    )
    collection = f"bucket_{bucket.id}"

    job_id = str(uuid4())
    _RAG_JOBS[job_id] = {
        "path": body.path,
        "total_chunks": None,
        "workspace_id": workspace_id,
        "enqueue_done": False,
    }

    async def _run() -> None:
        try:
            result = await ingest_directory(
                body.path,
                file_types=body.file_types,
                include_exts=body.include_exts,
                exclude_exts=body.exclude_exts,
                collection=collection,
                workspace_id=workspace_id or None,
                job_id=job_id,
            )
            meta = _RAG_JOBS.get(job_id)
            if meta is not None:
                meta["total_chunks"] = int(result["total_chunks"])
        except Exception:
            logger.exception("rag_ingest_task_failed", extra={"path": body.path})
            meta = _RAG_JOBS.get(job_id)
            if meta is not None:
                meta["total_chunks"] = 0
        finally:
            meta = _RAG_JOBS.get(job_id)
            if meta is not None:
                meta["enqueue_done"] = True
            await _maybe_emit_job_event(job_id)

    task = _asyncio.create_task(_run())
    _RAG_TASKS.add(task)
    task.add_done_callback(_RAG_TASKS.discard)

    return RagIngestResponse(
        job_id=job_id,
        total_files=0,
        total_chunks=0,
        status="indexing",
        bucket_id=bucket.id,
    )


@workspace_scoped_router.get(
    "/rag/ingest/preview", response_model=RagIngestPreviewResponse
)
async def rag_ingest_preview(
    workspace_id: str,
    path: str,
    file_types: str = "all",
    include_exts: str | None = None,
    exclude_exts: str | None = None,
) -> RagIngestPreviewResponse:
    """Lista os arquivos que os filtros atuais bateriam, sem indexar nada.

    Mesma validação de caminho do ``POST /rag/ingest`` (400 em path inválido)
    para dar feedback antes de disparar a indexação de fato.
    """
    from pathlib import Path

    from fastapi import HTTPException

    from backend.embedding.rag_ingest import list_matching_files
    from backend.services.security import is_safe_file_path

    if not is_safe_file_path(path) or not Path(path).is_dir():
        raise HTTPException(
            status_code=400, detail="Caminho inválido ou fora do escopo."
        )

    files, total = list_matching_files(
        path,
        file_types=file_types,
        include_exts=include_exts,
        exclude_exts=exclude_exts,
        limit=200,
    )
    return RagIngestPreviewResponse(total=total, files=files)


@workspace_scoped_router.get("/rag/jobs/{job_id}", response_model=RagJobStatus)
async def rag_job_status(workspace_id: str, job_id: str) -> RagJobStatus:
    """Progresso de um job de indexação (chunks processados / total)."""
    from backend.embedding.queue import get_embedding_queue
    from backend.settings import settings

    try:
        queue = await get_embedding_queue(settings.embedding_queue_dsn)
        stats = await queue.get_job_stats(job_id)
    except Exception:
        # `workspace_id` só existe pra manter a URL aninhada (consistência com
        # os demais endpoints de workspace) — o lookup real é por job_id
        # (chave única na fila), mas registra o par pra facilitar diagnóstico.
        logger.warning(
            "rag_job_status_failed",
            extra={"job_id": job_id, "workspace_id": workspace_id},
        )
        stats = {}
    return _rag_job_status(job_id, stats)


@workspace_scoped_router.get("/rag/jobs", response_model=list[RagJobStatus])
async def rag_jobs(workspace_id: str) -> list[RagJobStatus]:
    """Lista os jobs de indexação deste workspace com seu progresso atual."""
    from backend.embedding.queue import get_embedding_queue
    from backend.settings import settings

    out: list[RagJobStatus] = []
    try:
        queue = await get_embedding_queue(settings.embedding_queue_dsn)
    except Exception:
        return out
    for job_id, meta in list(_RAG_JOBS.items()):
        if workspace_id and meta.get("workspace_id") != workspace_id:
            continue
        try:
            stats = await queue.get_job_stats(job_id)
        except Exception:
            stats = {}
        out.append(_rag_job_status(job_id, stats))
    return out


# ---------------------------------------------------------------------------
# Context bridge — arquivo em foco
# ---------------------------------------------------------------------------


class ActiveContextRequest(BaseModel):
    open_file: str | None = None


@workspace_scoped_router.post("/context/active", response_model=StatusResponse)
async def set_active_context(
    workspace_id: str,
    body: ActiveContextRequest,
) -> StatusResponse:
    """Atualiza o arquivo em foco no editor para o agente via get_workbench_context."""
    try:
        from backend.persistence.kv import get_kv

        kv = await get_kv()
        import json

        key = f"workbench:context:{workspace_id}"
        payload: dict = {"open_file": body.open_file}
        await kv.set(key, json.dumps(payload), ttl_s=1800)
    except Exception:
        logger.debug(
            "set_active_context: falha ao gravar KV workspace=%s", workspace_id
        )
    return StatusResponse(status="ok")
