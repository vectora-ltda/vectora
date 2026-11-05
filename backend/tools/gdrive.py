"""Tools de Google Drive para o agente.

Requer GOOGLE_ACCESS_TOKEN no ambiente (configurado via OAuth /auth/google).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

_DRIVE_BASE = "https://www.googleapis.com/drive/v3"
_EXPORT_MIME: dict[str, str] = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


def _token() -> str:
    tok = os.environ.get("GOOGLE_ACCESS_TOKEN", "")
    if not tok:
        raise RuntimeError(
            "GOOGLE_ACCESS_TOKEN não configurado. Conecte o Google em Integrações."
        )
    return tok


@vtool(extras=ToolExtras(destructive=False, category="integrations", icon="folder"))
async def google_drive_list(folder_id: str = "root") -> str:
    """Lista arquivos e pastas do Google Drive.

    Args:
        folder_id: ID da pasta a listar. Use 'root' para a raiz.
    """
    try:
        import httpx

        token = _token()
        params: dict[str, Any] = {
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "files(id,name,mimeType,size,modifiedTime)",
            "pageSize": 50,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{_DRIVE_BASE}/files",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        r.raise_for_status()
        files = r.json().get("files", [])
        if not files:
            return "Pasta vazia."
        lines = []
        for f in files:
            kind = "📁" if "folder" in f.get("mimeType", "") else "📄"
            lines.append(f"{kind} {f['name']} (id={f['id']})")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("google_drive_list error")
        return f"Erro ao listar Drive: {exc}"


@vtool(extras=ToolExtras(destructive=False, category="integrations", icon="file-text"))
async def google_drive_read(file_id: str) -> str:
    """Lê o conteúdo de um arquivo do Google Drive.

    Documentos Google Docs são exportados como texto; arquivos binários retornam
    o conteúdo bruto (limitado a 500 KB).

    Args:
        file_id: ID do arquivo no Google Drive.
    """
    try:
        import httpx

        token = _token()
        async with httpx.AsyncClient(timeout=15) as client:
            # Descobre o mimeType
            meta = await client.get(
                f"{_DRIVE_BASE}/files/{file_id}",
                params={"fields": "name,mimeType"},
                headers={"Authorization": f"Bearer {token}"},
            )
            meta.raise_for_status()
            mime = meta.json().get("mimeType", "")
            name = meta.json().get("name", file_id)

            export_mime = _EXPORT_MIME.get(mime)
            if export_mime:
                r = await client.get(
                    f"{_DRIVE_BASE}/files/{file_id}/export",
                    params={"mimeType": export_mime},
                    headers={"Authorization": f"Bearer {token}"},
                )
            else:
                r = await client.get(
                    f"{_DRIVE_BASE}/files/{file_id}",
                    params={"alt": "media"},
                    headers={"Authorization": f"Bearer {token}"},
                )
            r.raise_for_status()
            content = r.text[:50_000]
        return f"# {name}\n\n{content}"
    except Exception as exc:
        logger.exception("google_drive_read error file_id=%s", file_id)
        return f"Erro ao ler arquivo {file_id}: {exc}"


@vtool(extras=ToolExtras(destructive=False, category="integrations", icon="search"))
async def google_drive_search(query: str, max_results: int = 10) -> str:
    """Busca arquivos no Google Drive por nome ou conteúdo.

    Args:
        query: Termo de busca (nome ou conteúdo do arquivo).
        max_results: Número máximo de resultados (padrão 10).
    """
    try:
        import httpx

        token = _token()
        params: dict[str, Any] = {
            "q": f"fullText contains '{query}' and trashed=false",
            "fields": "files(id,name,mimeType,modifiedTime)",
            "pageSize": min(max_results, 50),
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{_DRIVE_BASE}/files",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        r.raise_for_status()
        files = r.json().get("files", [])
        if not files:
            return f"Nenhum arquivo encontrado para '{query}'."
        lines = [f"Resultados para '{query}':"]
        lines.extend(
            f"  • {f['name']} (id={f['id']}, modificado={f.get('modifiedTime', '')})"
            for f in files
        )
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("google_drive_search error query=%s", query)
        return f"Erro na busca do Drive: {exc}"
