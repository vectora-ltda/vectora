"""RAG Curator — síntese de conhecimento e atualização do MANIFEST.md.

``curate_workspace_knowledge(workspace_id)`` é chamado pelo
BackgroundEmbeddingWorker após cada batch de ingestão (debounce ≥ 30s). Faz 1
LLM call por flush para resumir o conhecimento adicionado e atualizar o
MANIFEST.md do workspace, then bump da versão para o agente recarregar o
contexto no próximo turno.

A recuperação/decisão/reranking do RAG em runtime mora nas tools
(``backend/tools/rag.py``: ``vector_search``, ``embedding``, ``ingest_docs``,
``manage_retriever``), consumidas pelo deep-agent.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.llm.base import ChatClient

logger = logging.getLogger(__name__)

__all__ = ["curate_workspace_knowledge"]


async def _list_collections() -> list[str]:
    """Lista todas as tabelas LanceDB existentes. Retorna [] em qualquer falha."""
    from backend.settings import settings

    try:
        import lancedb
    except ImportError:
        return []
    if settings.lancedb_dir is None:
        return []
    try:
        db = await lancedb.connect_async(str(settings.lancedb_dir))
        return list((await db.list_tables()).tables)
    except Exception:
        logger.debug("_list_collections: falha ao listar tabelas", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# RAG Curator (B4)
# ---------------------------------------------------------------------------

_curator_llm: ChatClient | None = None


def _get_curator_llm() -> ChatClient:
    """LLM singleton para síntese do curator (plain, ChatClient nativo)."""
    global _curator_llm
    if _curator_llm is None:
        from backend.services.utils import load_native_llm

        _curator_llm = load_native_llm()
        logger.debug("curator LLM inicializado")
    return _curator_llm


async def curate_workspace_knowledge(workspace_id: str) -> str:
    """Sintetiza o conhecimento novo de um workspace e atualiza o MANIFEST.md.

    Chamado pelo BackgroundEmbeddingWorker após cada batch de ingestão
    (debounce ≥ 30s). Faz exatamente 1 LLM call por flush — ingestar
    1000 arquivos = 1 call de síntese, não 1000.

    Fluxo:
    1. Lê o MANIFEST.md existente (se houver) para contexto acumulado
    2. Amostra docs recentemente indexados do LanceDB
    3. LLM sintetiza o que foi adicionado em linguagem natural
    4. Escreve MANIFEST.md atualizado
    5. Chama workspace_registry.bump_version() → agente recarrega contexto

    Args:
        workspace_id: ID do workspace a curar

    Returns:
        Mensagem de status da curadoria
    """
    try:
        from backend.settings import settings
        from backend.workspace.workspace import workspace_registry

        if not settings.configured_llm_providers():
            return (
                f"workspace {workspace_id}: nenhum provider de LLM configurado, "
                "curadoria pulada"
            )

        ws = workspace_registry.get(workspace_id)
        if ws is None:
            return f"workspace {workspace_id} não encontrado"

        # Cria diretório de manifests se necessário
        manifest_dir = ws.manifest_dir()
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "buckets").mkdir(parents=True, exist_ok=True)

        # Amostra docs indexados para o LLM sintetizar
        sample_docs = await _sample_recent_docs(workspace_id)
        if not sample_docs:
            return f"workspace {workspace_id}: nenhum doc amostrado, curadoria pulada"

        # Contexto acumulado (manifest existente, se houver)
        existing_manifest = ""
        manifest_path = ws.manifest_path()
        if manifest_path.exists():
            import contextlib

            with contextlib.suppress(Exception):
                existing_manifest = manifest_path.read_text(
                    encoding="utf-8", errors="ignore"
                )

        # Prompt de síntese
        prompt = _build_curator_prompt(ws.name, ws.cwd, sample_docs, existing_manifest)

        # Chamada ao LLM (1 call por flush)
        llm = _get_curator_llm()
        from backend.vtypes.message import MessageRole, text_message

        result = await llm.agenerate(
            [
                text_message(
                    MessageRole.SYSTEM,
                    (
                        "Você é o curator de conhecimento do Vectora. "
                        "Sua tarefa é manter o MANIFEST.md do workspace atualizado "
                        "com um resumo claro do que está indexado na base de conhecimento. "
                        "Seja conciso, preciso e em português."
                    ),
                ),
                text_message(MessageRole.USER, prompt),
            ]
        )
        manifest_content = result.text().strip()
        if not manifest_content:
            return f"workspace {workspace_id}: LLM não retornou conteúdo"

        # Escreve o MANIFEST.md
        from datetime import UTC, datetime

        frontmatter = (
            "---\n"
            f"workspace_id: {ws.id}\n"
            f"name: {ws.name}\n"
            f"cwd: {ws.cwd}\n"
            f"last_updated: {datetime.now(UTC).isoformat()}\n"
            f"manifest_version: {ws.manifest_version + 1}\n"
            "---\n\n"
        )
        manifest_path.write_text(frontmatter + manifest_content, encoding="utf-8")

        # Bump de versão — o agente detecta e recarrega contexto
        new_version = workspace_registry.bump_version(workspace_id)
        logger.info(
            "curator: MANIFEST.md atualizado para workspace %s (v%d)",
            workspace_id,
            new_version,
        )
        return f"workspace {workspace_id} curado com sucesso (manifest_version={new_version})"

    except Exception as e:
        logger.exception(
            "curate_workspace_knowledge: falha no workspace %s", workspace_id
        )
        return f"erro: {e}"


async def _sample_recent_docs(workspace_id: str, max_docs: int = 20) -> list[dict]:
    """Amostra documentos indexados do workspace via LanceDB."""
    try:
        import json

        collections = await _list_collections()
        if not collections:
            return []

        # Busca amostras de cada coleção sem query específica
        # (para o curator ter uma visão geral, não uma busca direcionada)
        samples: list[dict] = []
        for coll_name in collections:
            try:
                import lancedb

                from backend.settings import settings

                if settings.lancedb_dir is None:
                    continue
                db = await lancedb.connect_async(str(settings.lancedb_dir))
                table = await db.open_table(coll_name)
                df = (await table.to_pandas()).head(5)
                for _, row in df.iterrows():
                    try:
                        meta = json.loads(row.get("metadata", "{}") or "{}")
                        # Filtra pelo workspace_id
                        if meta.get("workspace_id") != workspace_id:
                            continue
                        samples.append(
                            {
                                "collection": coll_name,
                                "source": meta.get("source", ""),
                                "text": str(row.get("text", ""))[:200],
                            }
                        )
                        if len(samples) >= max_docs:
                            break
                    except Exception:
                        pass
            except Exception:
                logger.debug(
                    "_sample_recent_docs: falha em coleção %s", coll_name, exc_info=True
                )
                continue
            if len(samples) >= max_docs:
                break

        return samples
    except Exception:
        logger.debug("_sample_recent_docs: falha ao amostrar", exc_info=True)
        return []


def _build_curator_prompt(
    workspace_name: str,
    workspace_cwd: str,
    sample_docs: list[dict],
    existing_manifest: str,
) -> str:
    """Monta o prompt para o LLM gerar o MANIFEST.md."""
    doc_lines = [
        f"- [{doc['collection']}] {doc['source']}: {doc['text'][:150]}"
        for doc in sample_docs[:20]
    ]
    docs_block = "\n".join(doc_lines) if doc_lines else "(nenhum doc amostrado)"

    existing_block = ""
    if existing_manifest:
        # Remove frontmatter do manifest existente
        m = existing_manifest
        if m.startswith("---"):
            end = m.find("---", 3)
            if end != -1:
                m = m[end + 3 :].strip()
        existing_block = (
            f"\n\n## Manifest atual (para contexto acumulado)\n\n{m[:1500]}"
        )

    return f"""Workspace: {workspace_name}
Diretório: {workspace_cwd}

## Docs amostrados recentemente indexados

{docs_block}{existing_block}

## Sua tarefa

Com base nos docs acima, gere um MANIFEST.md atualizado para este workspace.
O manifest deve ter:

1. **Parágrafo de introdução** (2-3 frases): o que é este projeto
2. **Seção "Conhecimento indexado"**: o que está indexado (coleções, fontes, temas)
3. **Seção "Tópicos cobertos"**: lista dos principais temas e conceitos

Seja conciso (máx 600 palavras). Escreva apenas o conteúdo Markdown, sem frontmatter.
"""
