"""Workspace tools: describe, list, bucket summary, workbench context.

Ferramentas que expõem o estado do WorkspaceRegistry para os agents,
permitindo que o orchestrator e search respondam perguntas sobre o
conhecimento indexado sem precisar disparar uma busca vetorial.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from backend.persistence.kv import get_kv
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

if TYPE_CHECKING:
    from backend.vtypes import Workspace

logger = logging.getLogger(__name__)

WORKBENCH_CTX_TTL = 1800  # segundos (30 min)


def _workbench_ctx_key(workspace_id: str) -> str:
    return f"workbench:ctx:{workspace_id}"


def _append_context_files_summary(workspace_cwd: str, result: dict) -> None:
    """Anexa lista dos arquivos de contexto encontrados no workspace ao resultado."""
    try:
        from backend.services.context_files import collect_context_files

        files = collect_context_files(workspace_cwd)
        if not files:
            return
        result["context_files"] = [
            {
                "title": f.title,
                "path": str(f.path.relative_to(workspace_cwd)),
                "type": f.type,
                "weight": f.weight,
                "inject_when": f.inject_when,
                "tags": f.tags,
                "description": f.description,
            }
            for f in files
        ]
    except Exception:
        pass


def _append_graph_summary(workspace_cwd: str, result: dict) -> None:
    """Injeta resumo do Context Graph no resultado de workspace_describe, se existir."""
    from pathlib import Path

    graph_file = Path(workspace_cwd) / ".vectora/context-graph/graph.json"
    if not graph_file.exists():
        return
    try:
        data = json.loads(graph_file.read_text(encoding="utf-8"))
        n_nodes = len(data.get("nodes", []))
        n_edges = len(data.get("edges", []))
        report_file = Path(workspace_cwd) / ".vectora/context-graph/GRAPH_REPORT.md"
        god_nodes: list[str] = []
        if report_file.exists():
            report_text = report_file.read_text(encoding="utf-8")
            for line in report_text.splitlines():
                stripped = line.strip()
                if stripped.startswith("-") and god_nodes.__len__() < 3:
                    god_nodes.append(stripped[1:].strip())
        result["context_graph"] = {
            "available": True,
            "node_count": n_nodes,
            "edge_count": n_edges,
            "god_nodes": god_nodes[:3],
            "hint": "Use graph_query, graph_explain, graph_affected para explorar estruturalmente.",
        }
    except Exception:
        pass


def _resolve_workspace(workspace_id: str | None, ctx: ToolContext) -> Workspace | None:
    """Resolve workspace_id → Workspace, priorizando o argumento explícito
    sobre ``ctx.workspace_id`` quando ambos estão presentes."""
    from backend.workspace.workspace import workspace_registry

    wid = workspace_id or ctx.workspace_id or None

    if wid:
        ws = workspace_registry.get(wid)
        if ws is not None:
            return ws

    # Fallback: workspace do diretório atual
    return workspace_registry.get_or_create()


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="workspace",
        destructive=False,
        icon="layout-dashboard",
    )
)
async def workspace_describe(
    ctx: ToolContext,
    workspace_id: str | None = None,
) -> str:
    """Descreve o workspace ativo: base de conhecimento indexada, buckets e tópicos.

    Retorna o conteúdo do MANIFEST.md gerado pelo curator após ingestão de docs.
    Use para responder "o que você sabe sobre este projeto?" ou "o que está indexado?".

    Args:
        workspace_id: ID do workspace (usa o workspace do diretório atual se omitido)
    """
    ws = _resolve_workspace(workspace_id, ctx)
    if ws is None:
        return json.dumps(
            {"status": "not_found", "message": "Nenhum workspace encontrado."}
        )

    manifest_path = ws.manifest_path()
    if not manifest_path.exists():
        return json.dumps(
            {
                "status": "no_manifest",
                "workspace_id": ws.id,
                "name": ws.name,
                "cwd": ws.cwd,
                "message": (
                    "Manifest ainda não gerado. Use /rag add <pasta> para indexar "
                    "documentos. O curator cria o MANIFEST.md automaticamente após "
                    "a ingestão."
                ),
            }
        )

    try:
        content = manifest_path.read_text(encoding="utf-8")
        result: dict = {
            "status": "success",
            "workspace_id": ws.id,
            "name": ws.name,
            "manifest": content,
        }
        _append_graph_summary(ws.cwd, result)
        _append_context_files_summary(ws.cwd, result)
        return json.dumps(result)
    except Exception as e:
        logger.exception("workspace_describe: erro ao ler manifest %s", ws.id)
        return json.dumps({"status": "error", "error": str(e)})


@vtool(
    extras=ToolExtras(
        render_hint="table",
        category="workspace",
        destructive=False,
        icon="list",
    )
)
async def workspace_list() -> str:
    """Lista todos os workspaces Vectora registrados.

    Mostra id, nome, diretório e versão do manifest de cada workspace.
    Útil para auditar projetos indexados ou alternar contextos.
    """
    from backend.workspace.workspace import workspace_registry

    workspaces = workspace_registry.list_all()
    if not workspaces:
        return json.dumps(
            {"status": "empty", "message": "Nenhum workspace registrado ainda."}
        )

    items = [
        {
            "id": ws.id,
            "name": ws.name,
            "cwd": ws.cwd,
            "created_at": ws.created_at,
            "manifest_version": ws.manifest_version,
            "has_manifest": ws.manifest_path().exists(),
            "bucket_count": len(ws.bucket_names),
        }
        for ws in workspaces
    ]
    return json.dumps({"status": "success", "workspaces": items, "count": len(items)})


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="workspace",
        destructive=False,
        icon="database",
    )
)
async def bucket_summary(
    ctx: ToolContext,
    bucket: str,
    workspace_id: str | None = None,
) -> str:
    """Retorna o resumo de um bucket específico do workspace.

    Cada bucket (code, docs, notes, web_cache) tem seu próprio
    buckets/<bucket>.md com resumo do que foi indexado.

    Args:
        bucket: Nome do bucket (ex: "code", "docs", "notes", "web_cache")
        workspace_id: ID do workspace (usa o ativo se omitido)
    """
    ws = _resolve_workspace(workspace_id, ctx)
    if ws is None:
        return json.dumps(
            {"status": "not_found", "message": "Nenhum workspace encontrado."}
        )

    bucket_path = ws.bucket_manifest_path(bucket)
    if not bucket_path.exists():
        return json.dumps(
            {
                "status": "no_manifest",
                "bucket": bucket,
                "workspace_id": ws.id,
                "message": (
                    f"Nenhum manifest para o bucket '{bucket}'. "
                    "Indexe documentos neste bucket primeiro."
                ),
            }
        )

    try:
        content = bucket_path.read_text(encoding="utf-8")
        return json.dumps(
            {
                "status": "success",
                "bucket": bucket,
                "workspace_id": ws.id,
                "summary": content,
            }
        )
    except Exception as e:
        logger.exception("bucket_summary: erro ao ler manifest bucket=%s", bucket)
        return json.dumps({"status": "error", "error": str(e)})


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="workspace",
        destructive=False,
        icon="file-search",
    )
)
async def get_workbench_context(ctx: ToolContext) -> str:
    """Retorna o arquivo em foco e os arquivos abertos no editor do workbench.

    Lê o contexto do editor que o frontend publica via KV ao trocar de arquivo.
    Use para saber qual arquivo o usuário está editando antes de fazer alterações.
    """
    workspace_id = ctx.workspace_id or "default"
    try:
        key = _workbench_ctx_key(workspace_id)
        kv = await get_kv()
        raw = await kv.get(key)
        if raw is None:
            return json.dumps({"status": "no_context"})
        data = json.loads(raw)
        return json.dumps({"status": "success", **data})
    except json.JSONDecodeError:
        logger.warning(
            "get_workbench_context: KV corrompido para workspace=%s", workspace_id
        )
        return json.dumps({"status": "error", "error": "contexto corrompido no KV"})
    except Exception as e:
        logger.exception("get_workbench_context: erro inesperado")
        return json.dumps({"status": "error", "error": str(e)})
