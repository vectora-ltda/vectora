"""Handler REST do Context Graph — build, query e export por workspace.

Endpoints (todos exigem autenticação; workspace deve existir):
    POST   /workspaces/{workspace_id}/context-graph/build      — inicia build
    DELETE /workspaces/{workspace_id}/context-graph/build      — cancela build
    GET    /workspaces/{workspace_id}/context-graph/status     — status do build
    GET    /workspaces/{workspace_id}/context-graph            — graph.json
    GET    /workspaces/{workspace_id}/context-graph/report     — GRAPH_REPORT.md
    GET    /workspaces/{workspace_id}/context-graph/html       — graph.html
    POST   /workspaces/{workspace_id}/context-graph/query      — pergunta livre
    POST   /workspaces/{workspace_id}/context-graph/explain    — explica nó
    POST   /workspaces/{workspace_id}/context-graph/path       — caminho entre nós
    POST   /workspaces/{workspace_id}/context-graph/affected   — impacto de mudança
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_active_builds: dict[str, asyncio.Task[None]] = {}


def _check_workspace_access(request: Request, workspace_id: str) -> None:
    """Dependência de router: 404/403 antes de qualquer handler rodar.

    Garante que o Context Graph de um workspace só é lido/mutado por quem
    tem acesso a esse workspace — o workspace_id sozinho não basta.
    """
    from backend.api.handlers.workspaces import require_workspace_access

    require_workspace_access(workspace_id, request)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/context-graph",
    tags=["context-graph"],
    dependencies=[Depends(_check_workspace_access)],
)

_GRAPH_DIR = ".vectora/context-graph"
_STATUS_FILE = "build_status.json"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BuildRequest(BaseModel):
    model: str = ""
    mode: str = "semantic"
    update: bool = False
    resume: bool = False
    # Tipos de arquivo a indexar ("code"/"document"/"paper"); vazio = todos.
    file_types: list[str] = []


class BuildResponse(BaseModel):
    status: str
    message: str


class StatusResponse(BaseModel):
    status: str
    node_count: int | None = None
    edge_count: int | None = None
    error: str | None = None
    step: int | None = None
    step_total: int | None = None
    step_label: str | None = None
    files_total: int | None = None
    files_done: int | None = None
    files_list: list[str] | None = None
    partial: bool = False
    provider_switched_to: str | None = None


class QueryRequest(BaseModel):
    question: str
    top_k: int = 10


class ExplainRequest(BaseModel):
    node_id: str
    depth: int = 1


class PathRequest(BaseModel):
    source: str
    target: str


class AffectedRequest(BaseModel):
    node_query: str
    depth: int = 2


class GraphQueryResponse(BaseModel):
    answer: str
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return str(user.id)


def _graph_dir(workspace_id: str) -> Path | None:
    from backend.api.handlers.workspaces import _resolve_inside

    return _resolve_inside(workspace_id, _GRAPH_DIR)


def _require_graph_dir(workspace_id: str) -> Path:
    d = _graph_dir(workspace_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Workspace não encontrado")
    return d


def _require_graph_json(workspace_id: str) -> dict[str, Any]:
    d = _require_graph_dir(workspace_id)
    graph_file = d / "graph.json"
    if not graph_file.exists():
        raise HTTPException(
            status_code=404,
            detail="Grafo não encontrado — execute o build primeiro",
        )
    try:
        return json.loads(graph_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.exception("context-graph: falha ao ler graph.json")
        raise HTTPException(status_code=500, detail="Grafo corrompido") from exc


def _write_status(graph_dir: Path, status: str, **extra: Any) -> None:
    payload: dict[str, Any] = {
        "status": status,
        "built_at": datetime.now(UTC).isoformat(),
        **extra,
    }
    try:
        (graph_dir / _STATUS_FILE).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        logger.exception("context-graph: falha ao escrever build_status.json")


def _read_status_file(graph_dir: Path) -> StatusResponse | None:
    status_file = graph_dir / _STATUS_FILE
    if not status_file.exists():
        return None
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
        return StatusResponse(
            status=data.get("status", "unknown"),
            node_count=data.get("node_count"),
            edge_count=data.get("edge_count"),
            error=data.get("error"),
            step=data.get("step"),
            step_total=data.get("step_total"),
            step_label=data.get("step_label"),
            files_total=data.get("files_total"),
            files_done=data.get("files_done"),
            files_list=data.get("files_list"),
            partial=bool(data.get("partial", False)),
            provider_switched_to=data.get("provider_switched_to"),
        )
    except Exception:
        return None


async def _run_build(workspace_id: str, req: BuildRequest) -> None:
    d = _graph_dir(workspace_id)
    if d is None:
        return
    d.mkdir(parents=True, exist_ok=True)
    _write_status(
        d,
        "running",
        step=0,
        step_total=9,
        step_label="Iniciando...",
        files_total=0,
        files_done=0,
    )

    _state: dict[str, Any] = {
        "files_list": [],
        "step": None,
        "step_total": None,
        "step_label": None,
        "files_total": 0,
        "files_done": 0,
    }

    def on_progress(
        step: int,
        step_total: int,
        label: str,
        files_done: int,
        files_total: int,
        files_list: list[str] | None = None,
    ) -> None:
        _state["step"] = step
        _state["step_total"] = step_total
        _state["step_label"] = label
        _state["files_done"] = files_done
        _state["files_total"] = files_total
        if files_list is not None:
            _state["files_list"] = files_list
        kw: dict[str, Any] = {
            "step": step,
            "step_total": step_total,
            "step_label": label,
            "files_total": files_total,
            "files_done": files_done,
        }
        if _state["files_list"]:
            kw["files_list"] = _state["files_list"]
        _write_status(d, "running", **kw)

    try:
        from backend.context_graph.pipeline import build_workspace_graph

        result = await build_workspace_graph(
            workspace_id,
            model=req.model,
            mode=req.mode,
            update=req.update,
            resume=req.resume,
            file_types=req.file_types or None,
            on_progress=on_progress,
        )
        if result.error:
            _write_status(d, "error", error=result.error)
        else:
            _write_status(
                d,
                "done",
                node_count=result.node_count,
                edge_count=result.edge_count,
            )
    except Exception as exc:
        from backend.llm.provider_fallback import QuotaExhaustedError

        if isinstance(exc, QuotaExhaustedError):
            # Quota esgotada em TODOS os providers — não é erro de pipeline:
            # pausa o build (grafo parcial servido) p/ o usuário renovar e retomar.
            logger.warning(
                "context-graph: quota esgotada em todos os providers — build pausado",
                extra={"workspace_id": workspace_id},
            )
            paused_kw: dict[str, Any] = {"error": str(exc), "partial": True}
            if _state["step"] is not None:
                paused_kw["step"] = _state["step"]
                paused_kw["step_total"] = _state["step_total"]
                paused_kw["step_label"] = _state["step_label"]
                paused_kw["files_total"] = _state["files_total"]
                paused_kw["files_done"] = _state["files_done"]
            _write_status(d, "paused", **paused_kw)
            return
        logger.exception(
            "context-graph: falha no build em background",
            extra={"workspace_id": workspace_id},
        )
        _write_status(d, "error", error="Falha interna no pipeline")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/build", response_model=BuildResponse)
async def post_build(
    request: Request,
    workspace_id: str,
    body: BuildRequest,
) -> BuildResponse:
    _user_id(request)
    d = _require_graph_dir(workspace_id)

    status_resp = _read_status_file(d)
    if status_resp and status_resp.status == "running":
        return BuildResponse(status="running", message="Build já em andamento")

    task = asyncio.create_task(_run_build(workspace_id, body))
    _active_builds[workspace_id] = task
    task.add_done_callback(lambda _: _active_builds.pop(workspace_id, None))
    return BuildResponse(status="queued", message="Build enfileirado")


@router.delete("/build", status_code=204)
async def delete_build(request: Request, workspace_id: str) -> None:
    _user_id(request)
    task = _active_builds.pop(workspace_id, None)
    if task and not task.done():
        task.cancel()
    d = _graph_dir(workspace_id)
    if d is not None:
        status_file = d / _STATUS_FILE
        if status_file.exists():
            status_file.unlink()


@router.get("/status", response_model=StatusResponse)
async def get_status(request: Request, workspace_id: str) -> StatusResponse:
    _user_id(request)
    d = _graph_dir(workspace_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Workspace não encontrado")

    from_file = _read_status_file(d)
    if from_file is not None:
        # Status "running"/"queued" sem task ativa neste processo é órfão (o build
        # morreu — restart/OOM). Não devolve spinner travado: se há checkpoint AST,
        # oferece resume ("paused"); senão, volta a "not_built" (botão Construir).
        if from_file.status in ("running", "queued") and (
            workspace_id not in _active_builds
        ):
            if (d / "checkpoint_ast.json").exists():
                _write_status(
                    d, "paused", error="Build interrompido — retome do checkpoint."
                )
                stale = _read_status_file(d)
                return stale if stale is not None else StatusResponse(status="paused")
            _write_status(d, "not_built")
            return StatusResponse(status="not_built")
        return from_file

    graph_file = d / "graph.json"
    if not graph_file.exists():
        return StatusResponse(status="not_built")
    try:
        data = json.loads(graph_file.read_text(encoding="utf-8"))
        return StatusResponse(
            status="done",
            node_count=len(data.get("nodes", [])),
            edge_count=len(data.get("edges", [])),
        )
    except Exception:
        return StatusResponse(status="error", error="graph.json ilegível")


@router.get("", response_model=dict)
async def get_graph(request: Request, workspace_id: str) -> dict:
    _user_id(request)
    return _require_graph_json(workspace_id)


@router.get("/report")
async def get_report(request: Request, workspace_id: str) -> dict[str, str]:
    _user_id(request)
    d = _require_graph_dir(workspace_id)
    report_file = d / "GRAPH_REPORT.md"
    if not report_file.exists():
        raise HTTPException(status_code=404, detail="Relatório não encontrado")
    return {"report": report_file.read_text(encoding="utf-8")}


@router.get("/html", response_class=HTMLResponse)
async def get_html(request: Request, workspace_id: str) -> HTMLResponse:
    _user_id(request)
    d = _require_graph_dir(workspace_id)
    html_file = d / "graph.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="HTML do grafo não encontrado")
    return HTMLResponse(
        content=html_file.read_text(encoding="utf-8"),
        headers={
            "Content-Security-Policy": (
                "sandbox allow-scripts; default-src 'none'; "
                "script-src 'unsafe-inline' https://unpkg.com; "
                "style-src 'unsafe-inline' https://unpkg.com; "
                "img-src data:; font-src data: https://unpkg.com; "
                "connect-src 'none'; form-action 'none'; base-uri 'none'; "
                "object-src 'none'; frame-ancestors 'self'"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/query", response_model=GraphQueryResponse)
async def post_query(
    request: Request, workspace_id: str, body: QueryRequest
) -> GraphQueryResponse:
    _user_id(request)
    data = _require_graph_json(workspace_id)

    from backend.context_graph.query import query_nodes

    matched_nodes, neighborhood_edges = query_nodes(
        data, body.question, top_k=body.top_k
    )
    matched_ids = {n.get("id") for n in matched_nodes}
    neighbor_ids = (
        {e.get("source") for e in neighborhood_edges}
        | {e.get("target") for e in neighborhood_edges}
    ) - matched_ids
    all_nodes: list[dict] = data.get("nodes", [])
    neighbor_nodes = [n for n in all_nodes if n.get("id") in neighbor_ids]

    summary = f"Encontrei {len(matched_nodes)} nó(s) correspondente(s) à consulta."
    return GraphQueryResponse(
        answer=summary,
        nodes=matched_nodes + neighbor_nodes,
        edges=neighborhood_edges,
    )


@router.post("/explain", response_model=GraphQueryResponse)
async def post_explain(
    request: Request, workspace_id: str, body: ExplainRequest
) -> GraphQueryResponse:
    _user_id(request)
    data = _require_graph_json(workspace_id)

    from backend.context_graph.query import explain_node

    target, neighbors, connected = explain_node(data, body.node_id, depth=body.depth)
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"Nó '{body.node_id}' não encontrado"
        )

    summary = (
        f"Nó: {target.get('label', body.node_id)} | "
        f"{len(connected)} arestas | {len(neighbors)} vizinhos"
    )
    return GraphQueryResponse(
        answer=summary, nodes=[target, *neighbors], edges=connected
    )


@router.post("/path", response_model=GraphQueryResponse)
async def post_path(
    request: Request, workspace_id: str, body: PathRequest
) -> GraphQueryResponse:
    _user_id(request)
    data = _require_graph_json(workspace_id)

    from backend.context_graph.query import path_between

    path_nodes, path_edges = path_between(data, body.source, body.target)
    if not path_nodes:
        raise HTTPException(
            status_code=404,
            detail=f"Caminho não encontrado entre '{body.source}' e '{body.target}'",
        )

    summary = f"Caminho de {body.source} → {body.target}: {len(path_nodes)} nós"
    return GraphQueryResponse(answer=summary, nodes=path_nodes, edges=path_edges)


@router.post("/affected", response_model=GraphQueryResponse)
async def post_affected(
    request: Request, workspace_id: str, body: AffectedRequest
) -> GraphQueryResponse:
    _user_id(request)
    data = _require_graph_json(workspace_id)

    from backend.context_graph.query import affected_summary

    answer = affected_summary(data, body.node_query, depth=body.depth)
    return GraphQueryResponse(answer=answer, nodes=[], edges=[])


@router.get("/export/{export_format}")
def get_export(
    request: Request,
    workspace_id: str,
    export_format: Literal["graphml", "cypher", "canvas"],
) -> FileResponse:
    """Gera um arquivo de exportação temporário a partir do grafo autorizado."""
    import shutil
    import tempfile

    import networkx as nx

    from backend.context_graph import export as graph_export

    _user_id(request)
    supported = {
        "graphml": ("graph.graphml", "application/graphml+xml"),
        "cypher": ("graph.cypher", "text/plain; charset=utf-8"),
        "canvas": ("graph.canvas", "application/json"),
    }
    if export_format not in supported:
        raise HTTPException(status_code=422, detail="Formato de exportação inválido")
    graph_dir = _require_graph_dir(workspace_id)
    graph_file = graph_dir / "graph.json"
    if not graph_file.is_file():
        raise HTTPException(status_code=404, detail="Grafo não encontrado")
    from backend.context_graph.security import check_graph_file_size_cap

    try:
        check_graph_file_size_cap(graph_file)
    except ValueError as exc:
        raise HTTPException(status_code=413, detail="Grafo excede o limite") from exc
    data = _require_graph_json(workspace_id)
    nodes = data.get("nodes", [])
    links = data.get("links", data.get("edges", []))
    if not isinstance(nodes, list) or not isinstance(links, list):
        raise HTTPException(status_code=422, detail="Estrutura do grafo inválida")
    if len(nodes) > 100_000 or len(links) > 500_000:
        raise HTTPException(
            status_code=413, detail="Grafo excede o limite de exportação"
        )

    graph_data = {**data, "links": links}
    try:
        graph = nx.node_link_graph(graph_data, edges="links")
    except TypeError:
        graph = nx.node_link_graph(graph_data)
    communities: dict[int, list[str]] = {}
    community_labels: dict[int, str] = {}
    for node_id, attrs in graph.nodes(data=True):
        cid = attrs.get("community")
        if cid is not None:
            try:
                cid = int(cid)
            except (TypeError, ValueError):
                continue
            communities.setdefault(cid, []).append(str(node_id))
            if attrs.get("community_name"):
                community_labels[cid] = str(attrs["community_name"])

    filename, media_type = supported[export_format]
    temp_dir = Path(tempfile.mkdtemp(prefix="vectora-context-graph-export-"))
    output = temp_dir / filename
    try:
        if export_format == "graphml":
            # GraphML só aceita atributos escalares; preserve estruturas como JSON.
            for key, value in list(graph.graph.items()):
                if value is None:
                    graph.graph[key] = ""
                elif not isinstance(value, (str, int, float, bool)):
                    graph.graph[key] = json.dumps(
                        value, ensure_ascii=False, default=str
                    )
            for _, attrs in graph.nodes(data=True):
                for key, value in list(attrs.items()):
                    if value is None:
                        attrs[key] = ""
                    elif not isinstance(value, (str, int, float, bool)):
                        attrs[key] = json.dumps(value, ensure_ascii=False, default=str)
            for _, _, attrs in graph.edges(data=True):
                for key, value in list(attrs.items()):
                    if value is None:
                        attrs[key] = ""
                    elif not isinstance(value, (str, int, float, bool)):
                        attrs[key] = json.dumps(value, ensure_ascii=False, default=str)
            graph_export.to_graphml(graph, communities, str(output))
        elif export_format == "cypher":
            graph_export.to_cypher(graph, str(output))
        else:
            graph_export.to_canvas(
                graph, communities, str(output), community_labels=community_labels
            )
        if not output.is_file():
            raise RuntimeError("O exportador não produziu um arquivo")
    except ImportError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=501, detail=f"Dependência de exportação indisponível: {exc}"
        ) from exc
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.exception(
            "context-graph: falha ao exportar",
            extra={"format": export_format},
        )
        raise HTTPException(
            status_code=500, detail="Falha ao gerar exportação"
        ) from exc

    from starlette.background import BackgroundTask

    return FileResponse(
        output,
        media_type=media_type,
        filename=filename,
        background=BackgroundTask(lambda: shutil.rmtree(temp_dir, ignore_errors=True)),
    )
