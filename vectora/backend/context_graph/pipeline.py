"""Pipeline principal do Context Graph — build assíncrono por workspace.

Orquestra os passes: detect → AST (asyncio.to_thread) → semântico → build
→ cluster → analyze → report/export. Grava artefatos em .vectora/context-graph/
dentro do workspace.

Defensivo (§11): cada passo tem try/except com logging estruturado.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GRAPH_DIR = ".vectora/context-graph"


@dataclass
class GraphResult:
    workspace_id: str
    workspace_path: Path
    graph_path: Path
    report_path: Path
    html_path: Path
    node_count: int = 0
    edge_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    god_nodes: list[str] = field(default_factory=list)
    suggested_questions: list[str] = field(default_factory=list)
    error: str | None = None


def _graph_out_dir(workspace_path: Path) -> Path:
    return workspace_path / _GRAPH_DIR


_AST_CHECKPOINT = "checkpoint_ast.json"


def _write_ast_checkpoint(out_dir: Path, ast_results: dict[str, Any]) -> None:
    """Persiste o resultado do AST (passo determinístico e CPU-pesado).

    Permite que um build pausado por quota retome a partir da semântica, sem
    re-detectar nem re-extrair AST. Defensivo: falha de escrita não derruba o build.
    """
    try:
        (out_dir / _AST_CHECKPOINT).write_text(
            json.dumps(ast_results), encoding="utf-8"
        )
    except Exception:
        logger.warning("context_graph: falha ao gravar checkpoint AST", exc_info=True)


def _load_ast_checkpoint(out_dir: Path) -> dict[str, Any] | None:
    """Carrega o checkpoint do AST se existir e for válido, senão None."""
    path = out_dir / _AST_CHECKPOINT
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("context_graph: checkpoint AST inválido", exc_info=True)
        return None


async def build_workspace_graph(
    workspace_id: str,
    *,
    model: str = "",
    mode: str = "semantic",
    update: bool = False,
    resume: bool = False,
    file_types: list[str] | None = None,
    on_progress: Callable[[int, int, str, int, int, list[str] | None], None]
    | None = None,
) -> GraphResult:
    """Constrói (ou atualiza) o grafo de contexto de um workspace.

    Args:
        workspace_id: ID do workspace (sha256 truncado do cwd).
        model: model_id no formato "provider:model" — vazio = LLM padrão.
        mode: "semantic" (AST + LLM), "ast" (só AST).
        update: True = incremental (só arquivos novos/modificados).

    Returns:
        GraphResult com caminhos dos artefatos e métricas básicas.
    """
    from backend.llm.provider_fallback import QuotaExhaustedError
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id)
    if ws is None:
        msg = f"context_graph: workspace {workspace_id!r} não encontrado"
        logger.error(msg)
        return GraphResult(
            workspace_id=workspace_id,
            workspace_path=Path(),
            graph_path=Path(),
            report_path=Path(),
            html_path=Path(),
            error=msg,
        )

    workspace_path = Path(ws.cwd)
    out_dir = _graph_out_dir(workspace_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    graph_json = out_dir / "graph.json"
    report_md = out_dir / "GRAPH_REPORT.md"
    graph_html = out_dir / "graph.html"
    manifest_path = str(out_dir / "manifest.json")

    result = GraphResult(
        workspace_id=workspace_id,
        workspace_path=workspace_path,
        graph_path=graph_json,
        report_path=report_md,
        html_path=graph_html,
    )

    _files_total: int = 0

    def _progress(
        step: int,
        label: str,
        files_done: int = 0,
        *,
        files_list: list[str] | None = None,
    ) -> None:
        if on_progress is not None:
            on_progress(step, 9, label, files_done, _files_total, files_list)

    try:
        # ── Passo 1: detectar arquivos ────────────────────────────────────────
        from .detect import detect, detect_incremental, save_manifest

        _progress(1, "Detectando arquivos...")
        logger.info("context_graph: detectando arquivos em %s", workspace_path)
        incremental = update and graph_json.exists()
        if incremental:
            corpus = await asyncio.to_thread(
                detect_incremental,
                workspace_path,
                manifest_path,
                kind="semantic" if mode == "semantic" else "ast",
            )
            files_key = "new_files"
        else:
            corpus = await asyncio.to_thread(detect, workspace_path)
            files_key = "files"

        # Filtro de tipos (settings do Context Graph): indexa só os tipos pedidos
        # (ex.: só "document" p/ usar como Obsidian, deixando "code" para o RAG).
        # Vazio/None = todos os tipos.
        if file_types:
            allowed = set(file_types)
            fmap = corpus.get(files_key, {})
            corpus[files_key] = {k: v for k, v in fmap.items() if k in allowed}

        all_files: list[str] = [
            f for flist in corpus.get(files_key, {}).values() for f in flist
        ]

        if not all_files:
            logger.info("context_graph: nenhum arquivo novo detectado")
            return result

        _files_total = len(all_files)
        short_files: list[str] = []
        for f in all_files[:200]:
            try:
                short_files.append(
                    str(Path(f).relative_to(workspace_path)).replace("\\", "/")
                )
            except ValueError:
                short_files.append(Path(f).name)

        logger.info(
            "context_graph: %d arquivos para extração",
            _files_total,
            extra={"workspace_id": workspace_id, "mode": mode},
        )
        _progress(1, "Detectando arquivos", _files_total, files_list=short_files)

        # ── Passo 2: extração AST (CPU-bound → thread) ────────────────────────
        _progress(2, "Extraindo AST...")
        ast_results: dict[str, Any] = {"nodes": [], "edges": [], "hyperedges": []}
        cached_ast = _load_ast_checkpoint(out_dir) if resume else None
        if cached_ast is not None:
            # Retoma de um build pausado: reusa o AST já computado, pula direto
            # para a semântica (passo onde a quota costuma estourar).
            ast_results = cached_ast
            logger.info(
                "context_graph: retomando do checkpoint AST",
                extra={"workspace_id": workspace_id},
            )
        else:
            try:
                ast_results = await asyncio.to_thread(
                    _run_ast_extraction,
                    all_files,
                    workspace_path,
                    lambda done: _progress(2, "Extraindo AST...", done),
                )
                _write_ast_checkpoint(out_dir, ast_results)
            except Exception:
                logger.exception(
                    "context_graph: falha na extração AST",
                    extra={"workspace_id": workspace_id},
                )

        # ── Passo 3: extração semântica (async LLM) ───────────────────────────
        _progress(3, "Análise semântica...", _files_total)
        semantic_results: dict[str, Any] = {
            "nodes": [],
            "edges": [],
            "hyperedges": [],
            "input_tokens": 0,
            "output_tokens": 0,
        }
        if mode == "semantic":
            from .semantic import extract_semantic

            files_map = corpus.get(files_key) or {}
            text_paths = (
                [Path(f) for f in files_map.get("code", [])]
                + [Path(f) for f in files_map.get("document", [])]
                + [Path(f) for f in files_map.get("paper", [])]
            )

            if text_paths:
                try:
                    semantic_results = await extract_semantic(
                        text_paths,
                        workspace_path,
                        model_id=model,
                        deep_mode=False,
                    )
                    result.input_tokens = semantic_results.get("input_tokens", 0)
                    result.output_tokens = semantic_results.get("output_tokens", 0)
                except QuotaExhaustedError:
                    # Quota total: o checkpoint AST já está gravado. Propaga para o
                    # handler marcar "paused" — o usuário retoma com resume=True.
                    logger.warning(
                        "context_graph: quota esgotada na semântica — build pausado",
                        extra={"workspace_id": workspace_id},
                    )
                    raise
                except Exception:
                    logger.exception(
                        "context_graph: falha na extração semântica",
                        extra={"workspace_id": workspace_id},
                    )

        # ── Passo 4: build (funde AST + semântico → grafo NetworkX) ──────────
        _progress(4, "Construindo grafo...", _files_total)
        from .build import build

        try:
            graph = await asyncio.to_thread(
                build,
                [ast_results, semantic_results],
                root=workspace_path,
            )
        except Exception:
            logger.exception(
                "context_graph: falha no build do grafo",
                extra={"workspace_id": workspace_id},
            )
            return result

        result.node_count = graph.number_of_nodes()
        result.edge_count = graph.number_of_edges()

        # ── Passo 5: cluster (comunidades Leiden/Louvain) ─────────────────────
        _progress(5, "Agrupando comunidades...", _files_total)
        from .cluster import cluster, label_communities_by_hub, score_all

        communities: dict[int, list[str]] = {}
        cohesion: dict[int, float] = {}
        community_labels: dict[int, str] = {}
        try:
            communities = await asyncio.to_thread(cluster, graph)
            cohesion = await asyncio.to_thread(score_all, graph, communities)
            community_labels = await asyncio.to_thread(
                label_communities_by_hub, graph, communities
            )
        except Exception:
            logger.exception(
                "context_graph: falha no clustering",
                extra={"workspace_id": workspace_id},
            )

        # ── Passo 6: analyze (god nodes, conexões surpreendentes, perguntas) ──
        _progress(6, "Analisando padrões...", _files_total)
        from .analyze import god_nodes, suggest_questions, surprising_connections

        god_node_list: list[dict] = []
        surprise_list: list[dict] = []
        questions: list[dict] = []

        try:
            god_node_list = await asyncio.to_thread(god_nodes, graph)
            surprise_list = await asyncio.to_thread(
                surprising_connections, graph, communities
            )
            questions = await asyncio.to_thread(
                suggest_questions, graph, communities, community_labels
            )
            result.god_nodes = [
                n.get("label", n.get("id", "")) for n in god_node_list[:5]
            ]
            result.suggested_questions = [q.get("question", "") for q in questions[:5]]
        except Exception:
            logger.exception(
                "context_graph: falha na análise", extra={"workspace_id": workspace_id}
            )

        # ── Passo 7: relatório ────────────────────────────────────────────────
        _progress(7, "Gerando relatório...", _files_total)
        from .export import to_html, to_json
        from .report import generate as generate_report

        token_cost = {"input": result.input_tokens, "output": result.output_tokens}

        try:
            report_text = await asyncio.to_thread(
                generate_report,
                graph,
                communities,
                cohesion,
                community_labels,
                god_node_list,
                surprise_list,
                corpus,
                token_cost,
                str(workspace_path),
                questions,
            )
            report_md.write_text(report_text, encoding="utf-8")
        except Exception:
            logger.exception(
                "context_graph: falha ao gerar relatório",
                extra={"workspace_id": workspace_id},
            )

        # ── Passo 8: exportar grafo + manifesto ───────────────────────────────
        _progress(8, "Exportando...", _files_total)
        try:
            await asyncio.to_thread(to_json, graph, communities, str(graph_json))
        except Exception:
            logger.exception(
                "context_graph: falha ao exportar graph.json",
                extra={"workspace_id": workspace_id},
            )

        try:
            await asyncio.to_thread(to_html, graph, communities, str(graph_html))
        except Exception:
            logger.exception(
                "context_graph: falha ao exportar graph.html",
                extra={"workspace_id": workspace_id},
            )

        try:
            await asyncio.to_thread(
                save_manifest,
                corpus.get("files", {}),
                manifest_path,
                kind="both",
                root=workspace_path,
            )
        except Exception:
            logger.exception(
                "context_graph: falha ao salvar manifesto",
                extra={"workspace_id": workspace_id},
            )

        # ── Passo 9: indexar nós do grafo no LanceDB (GraphRAG) ──────────────
        _progress(9, "Indexando...", _files_total)
        try:
            from .graph_index import index_graph_nodes, purge_graph_index

            graph_data = json.loads(graph_json.read_text(encoding="utf-8"))
            if not update:
                await purge_graph_index(workspace_id)
            await index_graph_nodes(workspace_id, graph_data)
        except Exception:
            logger.exception(
                "context_graph: falha ao indexar nós no LanceDB",
                extra={"workspace_id": workspace_id},
            )

        logger.info(
            "context_graph: build completo — %d nós, %d arestas, %d tokens",
            result.node_count,
            result.edge_count,
            result.input_tokens + result.output_tokens,
            extra={"workspace_id": workspace_id},
        )
        return result

    except QuotaExhaustedError:
        # Build pausado por quota — propaga para o handler marcar "paused" e o
        # checkpoint AST preservado permite retomar (resume=True).
        raise
    except Exception:
        logger.exception(
            "context_graph: falha catastrófica no pipeline",
            extra={"workspace_id": workspace_id},
        )
        result.error = "Falha no pipeline do context graph — veja os logs."
        return result


def _run_ast_extraction(
    files: list[str],
    workspace_path: Path,
    on_file: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    """Roda extração AST síncrona (chamada via asyncio.to_thread)."""
    from .cache import load_cached, save_cached
    from .extract import _get_extractor, _safe_extract

    nodes: list[dict] = []
    edges: list[dict] = []
    hyperedges: list[dict] = []

    for i, file_str in enumerate(files):
        path = Path(file_str)
        try:
            cached = load_cached(path, workspace_path)
            if cached is not None:
                nodes.extend(cached.get("nodes", []))
                edges.extend(cached.get("edges", []))
                hyperedges.extend(cached.get("hyperedges", []))
            else:
                extractor = _get_extractor(path)
                if extractor is not None:
                    data = _safe_extract(extractor, path)
                    if "error" not in data:
                        save_cached(path, data, workspace_path)
                    nodes.extend(data.get("nodes", []))
                    edges.extend(data.get("edges", []))
                    hyperedges.extend(data.get("hyperedges", []))
        except Exception:
            logger.exception("context_graph: falha ao extrair %s", file_str)
        if on_file is not None:
            on_file(i + 1)

    return {"nodes": nodes, "edges": edges, "hyperedges": hyperedges}
