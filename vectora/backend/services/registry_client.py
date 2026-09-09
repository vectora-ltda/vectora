"""Cliente do registry remoto de MCP/Skills (`services/src/registry/routes.ts`).

Segue o mesmo padrão de `backend.services.license`: `httpx.AsyncClient` com
timeout curto, cache local com TTL, fallback offline gracioso. Falha de rede
nunca propaga — cai pro cache existente, e sem cache devolve lista vazia
(estado válido: o caller decide se mescla com um fallback hardcoded próprio,
como `mcp_marketplace.py` faz).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_URL = "https://services.vectora.company/registry"
CACHE_DIR = Path.home() / ".vectora" / "registry_cache"

CACHE_TTL_ONLINE = timedelta(hours=6)
CACHE_TTL_OFFLINE = timedelta(hours=48)
HTTP_TIMEOUT = 10.0

RegistryKind = Literal["mcp", "skills", "mcp_official"]

OFFICIAL_MCP_REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0.1/servers"


def _registry_url() -> str:
    return os.getenv("VECTORA_REGISTRY_URL", DEFAULT_REGISTRY_URL).strip()


def _enterprise_registry_url() -> str:
    """Optional company registry; blank means the feature is disabled."""
    return os.getenv("VECTORA_ENTERPRISE_REGISTRY_URL", "").strip().rstrip("/")


def _valid_registry_url(url: str) -> bool:
    return url.startswith("https://") or (
        url.startswith("http://") and os.getenv("VECTORA_ENV") == "development"
    )


def _cache_path(kind: RegistryKind) -> Path:
    return CACHE_DIR / f"{kind}.json"


def _read_cache(kind: RegistryKind) -> dict | None:
    path = _cache_path(kind)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("registry_client: cache de %s corrompido — ignorando", kind)
        return None


def _write_cache(kind: RegistryKind, entries: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"entries": entries, "fetched_at": datetime.now(UTC).isoformat()}
    _cache_path(kind).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _cache_is_fresh(payload: dict, ttl: timedelta) -> bool:
    try:
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
    except (KeyError, ValueError):
        return False
    return datetime.now(UTC) - fetched_at < ttl


async def fetch_catalog(kind: RegistryKind) -> list[dict]:
    """Busca o catálogo `kind` ("mcp" | "skills") do registry remoto.

    Cache-first: com cache ainda dentro do TTL online (6h), serve direto
    sem tocar rede. Sucesso de rede (cache ausente/expirado) grava cache
    local. Falha de rede cai pro cache existente (até 48h stale). Sem rede
    e sem cache: lista vazia — nunca levanta exceção (tools/handlers que
    chamam isto degradam pro próprio fallback, não travam).
    """
    cache = _read_cache(kind)
    if cache is not None and _cache_is_fresh(cache, CACHE_TTL_ONLINE):
        return list(cache.get("entries", []))
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(f"{_registry_url()}/{kind}")
            resp.raise_for_status()
            data = resp.json()
        entries = list(data.get("entries", []))
        _write_cache(kind, entries)
        return entries
    except Exception as exc:
        logger.warning("registry_client: falha ao buscar catálogo %s (%s)", kind, exc)
        if cache is not None and _cache_is_fresh(cache, CACHE_TTL_OFFLINE):
            logger.info("registry_client: usando cache offline de %s", kind)
            return list(cache.get("entries", []))
        return []


async def fetch_enterprise_catalog(kind: Literal["mcp", "skills"]) -> list[dict]:
    """Fetch an optional enterprise catalog without affecting base sources."""
    base = _enterprise_registry_url()
    if not base or not _valid_registry_url(base):
        return []
    try:
        timeout = httpx.Timeout(HTTP_TIMEOUT)
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
        async with httpx.AsyncClient(
            timeout=timeout, limits=limits, follow_redirects=False
        ) as client:
            response = await client.get(f"{base}/{kind}")
            response.raise_for_status()
            payload = response.json()
        entries = payload.get("entries", [])
        return [entry for entry in entries if isinstance(entry, dict)]
    except Exception as exc:
        logger.warning("registry_client: enterprise catalog unavailable (%s)", exc)
        return []


def _npm_stdio_package(server: dict) -> dict | None:
    """Primeiro pacote npm/stdio do server — só esses são instaláveis pelo
    fluxo atual (`_connector_to_server` monta um McpServer stdio via npx)."""
    for pkg in server.get("packages", []):
        if (
            pkg.get("registryType") == "npm"
            and pkg.get("transport", {}).get("type", "stdio") == "stdio"
        ):
            return pkg
    return None


def _official_entry_to_connector_dict(item: dict) -> dict | None:
    server = item.get("server", {})
    pkg = _npm_stdio_package(server)
    if pkg is None or not pkg.get("identifier"):
        return None
    name = server.get("name", "")
    if not name:
        return None
    env_vars = [
        ev["name"]
        for ev in pkg.get("environmentVariables", [])
        if ev.get("isRequired") and ev.get("name")
    ]
    return {
        "id": name,
        "name": server.get("title") or name.rsplit("/", 1)[-1],
        "description": server.get("description", ""),
        "install_cmd": f"npx -y {pkg['identifier']}",
        "env_vars": env_vars,
        "homepage": (server.get("repository") or {}).get("url", ""),
        "category": "community",
    }


async def fetch_official_mcp_registry(*, max_entries: int = 100) -> list[dict]:
    """Busca o catálogo oficial de MCP servers em registry.modelcontextprotocol.io
    (mantido pela Anthropic/comunidade — não confundir com o registry próprio
    da Vectora em `services/`). Só inclui servers com pacote npm/stdio (único
    transporte que o fluxo de instalação atual suporta); servers remote-only
    (`remotes: [...]`, sem `packages`) são ignorados por ora.

    Mesma política de cache/fallback de `fetch_catalog`: cache-first dentro
    do TTL online (6h) sem tocar rede, sucesso de rede grava cache, falha de
    rede cai pro cache existente (até 48h), sem nada disso devolve lista
    vazia — nunca propaga exceção.
    """
    cache = _read_cache("mcp_official")
    if cache is not None and _cache_is_fresh(cache, CACHE_TTL_ONLINE):
        return list(cache.get("entries", []))
    try:
        connectors: dict[str, dict] = {}
        cursor: str | None = None
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            while len(connectors) < max_entries:
                params = {"version": "latest", "limit": "100"}
                if cursor:
                    params["cursor"] = cursor
                resp = await client.get(OFFICIAL_MCP_REGISTRY_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("servers", []):
                    connector = _official_entry_to_connector_dict(item)
                    if connector is not None:
                        connectors[connector["id"]] = connector
                cursor = (data.get("metadata") or {}).get("nextCursor")
                if not cursor or not data.get("servers"):
                    break
        entries = list(connectors.values())[:max_entries]
        _write_cache("mcp_official", entries)
        return entries
    except Exception as exc:
        logger.warning(
            "registry_client: falha ao buscar registry oficial de MCP (%s)", exc
        )
        if cache is not None and _cache_is_fresh(cache, CACHE_TTL_OFFLINE):
            return list(cache.get("entries", []))
        return []


class RegistryClientError(RuntimeError):
    """Erro tipado de publish — sessão inválida, `source` rejeitado pelo
    servidor, ou falha de rede. Nunca propaga exceção crua pro chamador."""


async def publish_skill(
    name: str,
    description: str,
    source: str,
    *,
    category: str | None = None,
    tags: list[str] | None = None,
    session_token: str,
) -> str:
    """Registra uma skill no catálogo remoto via `POST /registry/skills` —
    `source` é sempre uma URL git (o mesmo formato que
    `backend/workspace/skills.py` já aceita pra instalação), nunca um
    upload de arquivo. Retorna o `id` da entrada recém-criada (sempre
    `verified=false` até curadoria manual via `PATCH
    /registry/admin/skills/:id/verify`)."""
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{_registry_url()}/skills",
                headers={"Authorization": f"Bearer {session_token}"},
                json={
                    "name": name,
                    "description": description,
                    "source": source,
                    "category": category,
                    "tags": tags or [],
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise RegistryClientError(
            f"Falha ao publicar a skill: {exc.response.status_code} {exc.response.text}"
        ) from exc
    except Exception as exc:
        raise RegistryClientError(f"Falha ao publicar a skill: {exc}") from exc

    remote_id = data.get("id")
    if not remote_id:
        raise RegistryClientError("Resposta inesperada do registry/skills (sem id).")
    logger.info("registry_client: skill publicada como %s", remote_id)
    return remote_id


def clear_registry_cache() -> None:
    """Remove todo o cache local — útil em testes/troca de VECTORA_REGISTRY_URL."""
    with contextlib.suppress(OSError):
        for kind in ("mcp", "skills", "mcp_official"):
            _cache_path(kind).unlink(missing_ok=True)
