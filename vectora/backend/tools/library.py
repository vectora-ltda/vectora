"""Tools de auto-instalação da Library: MCP marketplace, catálogo de Skills e
Memory Buckets — mesma lógica que os handlers HTTP já usam (`_impl`
reaproveitado, nunca duplicado). Todas exigem aprovação humana
(`REQUIRE_APPROVAL`, `backend/engine/hitl.py`): instalar é uma
mudança persistente no ambiente do usuário.
"""

from __future__ import annotations

import asyncio
import json
import logging

from backend.services.env import get_env
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)


@vtool(
    extras=ToolExtras(
        invalidates=["mcp"],
        destructive=True,
        category="library",
        icon="puzzle",
    )
)
async def install_mcp_from_registry(connector_id: str, ctx: ToolContext) -> str:
    """Instala um conector MCP do registry (curados + registry oficial de
    MCP) — mesma lista que a aba Library mostra. Se o conector exigir
    variáveis de ambiente ainda não configuradas, não instala e lista o que
    falta em vez de instalar incompleto.

    Args:
        connector_id: id do conector, como aparece em `GET /mcp/registry`.
    """
    try:
        from backend.api.handlers.mcp_marketplace import (
            InstallRequest,
            install_mcp,
            list_registry,
        )

        registry = await list_registry()
        connector = next((c for c in registry if c.id == connector_id), None)
        if connector is None:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"conector '{connector_id}' não encontrado no registry",
                }
            )

        missing = [v for v in connector.env_vars if not get_env(v, strict=False)]
        if missing:
            return json.dumps(
                {
                    "status": "error",
                    "error": "variáveis de ambiente ausentes para instalar",
                    "missing_env_vars": missing,
                }
            )

        result = await install_mcp(InstallRequest(mcp_id=connector_id), ctx.user_id)
        return json.dumps(result)
    except Exception as exc:
        logger.exception(
            "install_mcp_from_registry failed", extra={"connector_id": connector_id}
        )
        return json.dumps({"status": "error", "error": str(exc)})


@vtool(
    extras=ToolExtras(
        invalidates=["skills"],
        destructive=True,
        category="library",
        icon="sparkles",
    )
)
async def install_skill_from_catalog(skill_id: str, ctx: ToolContext) -> str:
    """Instala uma skill curada do catálogo remoto (`GET /skills/catalog`) —
    distinto de escrever uma skill nova via `install_learned_skill`.

    Args:
        skill_id: id da skill no catálogo.
    """
    try:
        from backend.services import registry_client
        from backend.workspace.skills import install_skill, list_wellknown_catalog

        remote = await registry_client.fetch_catalog("skills")
        enterprise = await registry_client.fetch_enterprise_catalog("skills")
        local_entries = await asyncio.to_thread(list_wellknown_catalog)
        local = [entry.model_dump() for entry in local_entries]
        local_ids = {str(entry.get("id")) for entry in local if entry.get("id")}
        by_id = {
            str(entry.get("id")): entry
            for entry in [*enterprise, *remote, *local]
            if entry.get("id")
        }
        entries = list(by_id.values())
        entry = next((e for e in entries if e.get("id") == skill_id), None)
        if entry is None:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"skill '{skill_id}' não encontrada no catálogo",
                }
            )

        if skill_id in local_ids:
            return json.dumps(
                {
                    "status": "error",
                    "error": "skill local exige confirmação explícita de conteúdo não assinado; use a instalação interativa com confirm_unverified",
                }
            )

        skill = install_skill(ctx.user_id, entry["source"])
        logger.info(
            "install_skill_from_catalog completed",
            extra={
                "tool": "install_skill_from_catalog",
                "skill_id": skill_id,
                "caller_id": ctx.user_id,
                "source": entry.get("source", ""),
            },
        )
        return json.dumps({"status": "installed", "skill_id": skill.id})
    except Exception as exc:
        logger.exception(
            "install_skill_from_catalog failed", extra={"skill_id": skill_id}
        )
        return json.dumps({"status": "error", "error": str(exc)})


@vtool(
    extras=ToolExtras(
        invalidates=["memory"],
        destructive=True,
        category="library",
        icon="database",
    )
)
async def install_memory_bucket(bucket_id: str) -> str:
    """Baixa e instala um bucket da Vectora Memory Buckets como coleção
    LanceDB isolada (`shared_{bucket_id}`) — mesmo fluxo de
    `POST /memory-buckets/install`.

    Args:
        bucket_id: id do bucket, como aparece em `GET /memory-buckets/catalog`.
    """
    from backend.services.memory_buckets import (
        MemoryBucketsError,
        download_memory_bucket,
    )

    try:
        collection = await download_memory_bucket(bucket_id)
        return json.dumps({"status": "installed", "collection": collection})
    except MemoryBucketsError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    except Exception as exc:
        logger.exception("install_memory_bucket failed", extra={"bucket_id": bucket_id})
        return json.dumps({"status": "error", "error": str(exc)})


@vtool(
    extras=ToolExtras(
        invalidates=["mcp"],
        destructive=True,
        category="library",
        icon="puzzle",
    )
)
async def uninstall_mcp(connector_id: str, ctx: ToolContext) -> str:
    """Desinstala um conector MCP previamente instalado (o inverso de
    `install_mcp_from_registry`).

    Args:
        connector_id: id do conector a remover.
    """
    try:
        from backend.api.handlers.mcp_marketplace import UninstallRequest
        from backend.api.handlers.mcp_marketplace import (
            uninstall_mcp as _http_uninstall,
        )

        result = await _http_uninstall(
            UninstallRequest(mcp_id=connector_id), ctx.user_id
        )
        return json.dumps(result)
    except Exception as exc:
        logger.exception("uninstall_mcp failed", extra={"connector_id": connector_id})
        return json.dumps({"status": "error", "error": str(exc)})


@vtool(
    extras=ToolExtras(
        invalidates=["skills"],
        destructive=True,
        category="library",
        icon="trash-2",
    )
)
async def delete_skill(skill_id: str, ctx: ToolContext) -> str:
    """Remove uma skill instalada do usuário.

    Args:
        skill_id: id da skill (de `list_skills`/aba Library).
    """
    try:
        from backend.workspace.skills import remove_skill

        removed = remove_skill(ctx.user_id, skill_id)
        if not removed:
            return json.dumps(
                {"status": "error", "error": f"skill '{skill_id}' não encontrada"}
            )
        return json.dumps({"status": "removed", "skill_id": skill_id})
    except Exception as exc:
        logger.exception("delete_skill failed", extra={"skill_id": skill_id})
        return json.dumps({"status": "error", "error": str(exc)})


@vtool(
    extras=ToolExtras(
        invalidates=["skills"],
        destructive=False,
        category="library",
        icon="check-circle",
    )
)
async def verify_skill(skill_id: str, ctx: ToolContext) -> str:
    """Revalida o SKILL.md de uma skill instalada (útil após edição manual
    do arquivo no disco).

    Args:
        skill_id: id da skill a revalidar.
    """
    try:
        from backend.workspace.skills import verify_skill as _verify

        result = _verify(ctx.user_id, skill_id)
        return json.dumps(result)
    except Exception as exc:
        logger.exception("verify_skill failed", extra={"skill_id": skill_id})
        return json.dumps({"status": "error", "error": str(exc)})


@vtool(
    extras=ToolExtras(
        invalidates=["memory"],
        destructive=True,
        category="library",
        icon="upload",
    )
)
async def publish_memory_bucket_tool(
    bucket_id: str,
    name: str,
    description: str,
    license: str = "CC-BY-4.0",  # noqa: A002 — nome de campo do domínio (licença)
) -> str:
    """Publica um bucket RAG local na Vectora Memory Buckets remota — exige
    conta vectora.company conectada (`VECTORA_TOKEN`).

    Args:
        bucket_id: id do bucket local a publicar (de `list_buckets`).
        name: nome de exibição no catálogo remoto.
        description: descrição em markdown do conteúdo do bucket.
        license: licença de distribuição (ex.: "CC-BY-4.0", "MIT").
    """
    from backend.services import license as license_service
    from backend.services.memory_buckets import (
        MemoryBucketsError,
        publish_memory_bucket,
    )

    token = license_service._get_token()
    if not token:
        return json.dumps(
            {
                "status": "error",
                "error": "Nenhuma conta vectora.company conectada (VECTORA_TOKEN ausente).",
            }
        )
    try:
        remote_bucket_id = await publish_memory_bucket(
            bucket_id, name, description, license, session_token=token
        )
        return json.dumps({"status": "published", "bucket_id": remote_bucket_id})
    except MemoryBucketsError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    except Exception as exc:
        logger.exception(
            "publish_memory_bucket_tool failed", extra={"bucket_id": bucket_id}
        )
        return json.dumps({"status": "error", "error": str(exc)})


@vtool(
    extras=ToolExtras(
        invalidates=["mcp"],
        destructive=True,
        category="library",
        icon="key",
    )
)
async def save_mcp_env_var(
    connector_id: str, key: str, value: str, ctx: ToolContext
) -> str:
    """Salva uma variável de ambiente exigida por um conector MCP (ex.: a
    que `install_mcp_from_registry` listou como faltante em
    `missing_env_vars`) — mesmo mecanismo de `POST /auth/envs`. Não instala
    o conector sozinho; chame `install_mcp_from_registry` de novo depois.

    Args:
        connector_id: id do conector que precisa da variável (só para o log).
        key: nome da variável de ambiente (ex.: "GITHUB_TOKEN").
        value: valor a salvar.
    """
    try:
        from backend.rbac.auth import set_env_override

        await set_env_override(ctx.user_id, key, value)
        return json.dumps({"status": "saved", "connector_id": connector_id, "key": key})
    except Exception as exc:
        logger.exception(
            "save_mcp_env_var failed", extra={"connector_id": connector_id, "key": key}
        )
        return json.dumps({"status": "error", "error": str(exc)})


#: Teto de itens devolvidos por consulta ao catálogo. O registry oficial de MCP
#: tem centenas de entradas; despejar tudo no contexto do LLM gasta a janela
#: sem melhorar a sugestão.
_CATALOG_PAGE_SIZE = 40


def _match(entry_text: str, query: str) -> bool:
    return not query or query.lower() in entry_text.lower()


@vtool(extras=ToolExtras(category="library", icon="puzzle"))
async def list_mcp_catalog(query: str = "") -> str:
    """Lista conectores MCP disponíveis para instalar (curados + registry
    oficial), com id, nome e descrição — use antes de sugerir uma instalação,
    para citar conectores que existem de verdade.

    Args:
        query: filtro opcional por nome/descrição; vazio lista o começo do
            catálogo.
    """
    try:
        from backend.api.handlers.mcp_marketplace import list_registry

        registry = await list_registry()
        items = [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "env_vars": list(c.env_vars),
            }
            for c in registry
            if _match(f"{c.id} {c.name} {c.description}", query)
        ]
        return json.dumps(
            {"items": items[:_CATALOG_PAGE_SIZE], "total": len(items)},
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("list_mcp_catalog failed", extra={"query": query})
        return json.dumps({"items": [], "total": 0, "error": str(exc)})


@vtool(extras=ToolExtras(category="library", icon="sparkles"))
async def list_skills_catalog(query: str = "") -> str:
    """Lista skills disponíveis no catálogo, com id, nome e descrição — use
    antes de sugerir a instalação de uma skill.

    Args:
        query: filtro opcional por nome/descrição.
    """
    try:
        from backend.services import registry_client
        from backend.workspace.skills import list_wellknown_catalog

        remote = await registry_client.fetch_catalog("skills")
        enterprise = await registry_client.fetch_enterprise_catalog("skills")
        local_entries = await asyncio.to_thread(list_wellknown_catalog)
        local = [entry.model_dump() for entry in local_entries]
        by_id = {
            str(entry.get("id")): entry
            for entry in [*enterprise, *remote, *local]
            if entry.get("id")
        }
        entries = list(by_id.values())
        items = [
            {
                "id": e.get("id", ""),
                "name": e.get("name", ""),
                "description": e.get("description", ""),
            }
            for e in entries
            if _match(
                f"{e.get('id', '')} {e.get('name', '')} {e.get('description', '')}",
                query,
            )
        ]
        logger.info(
            "list_skills_catalog completed",
            extra={
                "tool": "list_skills_catalog",
                "query": query,
                "result_count": len(items),
            },
        )
        return json.dumps(
            {"items": items[:_CATALOG_PAGE_SIZE], "total": len(items)},
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("list_skills_catalog failed", extra={"query": query})
        return json.dumps({"items": [], "total": 0, "error": str(exc)})


@vtool(extras=ToolExtras(category="library", icon="database"))
async def list_memory_bucket_catalog(query: str = "") -> str:
    """Lista buckets de memória publicados na Vectora Memory Buckets, com id,
    nome e descrição — use antes de sugerir baixar uma base de conhecimento.

    Args:
        query: filtro opcional por nome/descrição.
    """
    try:
        from backend.services import memory_buckets

        entries = await memory_buckets.list_catalog()
        items = [
            {
                "id": e.get("id", ""),
                "name": e.get("name", ""),
                "description": e.get("description", ""),
            }
            for e in entries
            if _match(
                f"{e.get('id', '')} {e.get('name', '')} {e.get('description', '')}",
                query,
            )
        ]
        return json.dumps(
            {"items": items[:_CATALOG_PAGE_SIZE], "total": len(items)},
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("list_memory_bucket_catalog failed", extra={"query": query})
        return json.dumps({"items": [], "total": 0, "error": str(exc)})
