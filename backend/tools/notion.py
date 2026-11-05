"""Tools do Notion para o agente.

Requer NOTION_API_KEY no ambiente.
"""

from __future__ import annotations

import logging
import os

from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

_NOTION_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


def _headers() -> dict[str, str]:
    key = os.environ.get("NOTION_API_KEY", "")
    if not key:
        raise RuntimeError("NOTION_API_KEY não configurado. Adicione em Integrações.")
    return {
        "Authorization": f"Bearer {key}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _rich_text_content(blocks: list[dict]) -> str:
    """Extrai texto de blocos de rich_text."""
    parts: list[str] = []
    for block in blocks:
        btype = block.get("type", "")
        b = block.get(btype, {})
        parts.extend(rt.get("plain_text", "") for rt in b.get("rich_text", []))
    return "\n".join(parts)


@vtool(extras=ToolExtras(destructive=False, category="integrations", icon="search"))
async def notion_search(query: str, limit: int = 10) -> str:
    """Busca páginas e databases no Notion.

    Args:
        query: Termo de busca.
        limit: Número máximo de resultados (padrão 10).
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{_NOTION_BASE}/search",
                json={"query": query, "page_size": min(limit, 50)},
                headers=_headers(),
            )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return f"Nenhum resultado para '{query}'."
        lines = []
        for res in results:
            title_prop = res.get("properties", {}).get("title") or res.get(
                "properties", {}
            ).get("Name")
            title = ""
            if title_prop:
                for rt in title_prop.get("title", []):
                    title += rt.get("plain_text", "")
            if not title:
                title = (
                    res.get("title", [{}])[0].get("plain_text", "(sem título)")
                    if res.get("title")
                    else "(sem título)"
                )
            lines.append(f"[{res['object']}] {title} (id={res['id']})")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("notion_search error query=%s", query)
        return f"Erro ao buscar no Notion: {exc}"


@vtool(extras=ToolExtras(destructive=False, category="integrations", icon="file-text"))
async def notion_read_page(page_id: str) -> str:
    """Lê o conteúdo de uma página do Notion.

    Args:
        page_id: ID da página (obtido via notion_search).
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            # Metadados da página
            pr = await client.get(f"{_NOTION_BASE}/pages/{page_id}", headers=_headers())
            pr.raise_for_status()
            page = pr.json()

            # Conteúdo (blocos)
            br = await client.get(
                f"{_NOTION_BASE}/blocks/{page_id}/children", headers=_headers()
            )
            br.raise_for_status()
            blocks = br.json().get("results", [])

        # Extrai título
        title = ""
        for prop in page.get("properties", {}).values():
            if prop.get("type") == "title":
                for rt in prop.get("title", []):
                    title += rt.get("plain_text", "")
                break

        content = _rich_text_content(blocks)
        return f"# {title}\n\n{content[:30_000]}"
    except Exception as exc:
        logger.exception("notion_read_page error id=%s", page_id)
        return f"Erro ao ler página Notion: {exc}"


@vtool(extras=ToolExtras(destructive=True, category="integrations", icon="plus-square"))
async def notion_create_page(parent_id: str, title: str, content: str = "") -> str:
    """Cria uma nova página no Notion.

    Args:
        parent_id: ID do database ou página pai onde a página será criada.
        title: Título da nova página.
        content: Conteúdo inicial da página (texto simples).
    """
    try:
        import httpx

        body: dict = {
            "parent": {"database_id": parent_id},
            "properties": {"title": {"title": [{"text": {"content": title}}]}},
        }
        if content:
            body["children"] = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {"type": "text", "text": {"content": content[:2000]}}
                        ]
                    },
                }
            ]

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{_NOTION_BASE}/pages", json=body, headers=_headers()
            )
        r.raise_for_status()
        page = r.json()
        return f"Página criada: id={page['id']} — {title}"
    except Exception as exc:
        logger.exception("notion_create_page error parent=%s", parent_id)
        return f"Erro ao criar página Notion: {exc}"
