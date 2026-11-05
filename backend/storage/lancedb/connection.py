"""Cache de conexões LanceDB assíncronas (lite mode).

LanceDB abre uma conexão por chamada ``connect_async(path)`` — sem pooling
nativo. Abrir/fechar conexões repetidamente tem custo de I/O e inicialização
de metadados. Este módulo mantém um cache processo-local de conexões por
path, reutilizando-as entre chamadas.

Uso:
    # Obtém (ou cria) a conexão para o diretório LanceDB default
    db = await get_lancedb()

    # Obtém conexão para path específico
    db = await get_lancedb("/data/my-lancedb")

    # Usando o cache diretamente
    cache = LanceDBConnectionCache.default()
    db = await cache.connect("/data/my-lancedb")
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class LanceDBConnectionCache:
    """Cache processo-local de conexões LanceDB assíncronas.

    Cada path único mantém exatamente uma conexão aberta. Conexões são
    thread-safe (lancedb-py garante isso internamente via Rust async runtime).

    Args:
        default_path: Path usado quando ``connect()`` é chamado sem argumento.
    """

    def __init__(self, default_path: str | Path | None = None) -> None:
        self._default_path: Path | None = Path(default_path) if default_path else None
        self._cache: dict[Path, Any] = {}  # path → AsyncConnection (lancedb)
        self._lock = asyncio.Lock()

    async def connect(self, path: str | Path | None = None) -> Any:
        """Retorna (ou cria) a conexão LanceDB para ``path``.

        Args:
            path: Diretório do banco LanceDB. Se None, usa ``default_path``
                  ou o valor de ``settings.lancedb_dir``.

        Returns:
            Objeto ``lancedb.AsyncConnection`` reutilizável.

        Raises:
            RuntimeError: Se nenhum path for fornecido ou configurado.
        """
        resolved = self._resolve_path(path)

        async with self._lock:
            if resolved in self._cache:
                return self._cache[resolved]

            import lancedb

            resolved.mkdir(parents=True, exist_ok=True)
            db = await lancedb.connect_async(str(resolved))
            self._cache[resolved] = db
            logger.info("lancedb: nova conexão → %s", resolved)
            return db

    def _resolve_path(self, path: str | Path | None) -> Path:
        if path is not None:
            return Path(path)
        if self._default_path is not None:
            return self._default_path
        # Fallback: lê das settings
        try:
            from backend.settings import settings

            lancedb_dir = getattr(settings, "lancedb_dir", None)
            if lancedb_dir:
                return Path(lancedb_dir)
        except Exception:
            pass
        raise RuntimeError(
            "LanceDBConnectionCache: nenhum path fornecido e lancedb_dir "
            "não configurado nas settings."
        )

    async def close_all(self) -> None:
        """Fecha todas as conexões cacheadas. Usado em shutdown ou testes.

        Chama ``AsyncConnection.close()`` (síncrono) explicitamente em vez de
        só limpar o cache — sem isso, o fechamento de verdade depende de
        GC/Drop do lado Rust, que pode demorar até ~30s por conexão pendente.
        """
        async with self._lock:
            for path, db in self._cache.items():
                try:
                    db.close()
                except Exception:
                    logger.warning(
                        "storage/lancedb/connection: falha ao fechar conexão %s",
                        path,
                        exc_info=True,
                    )
            count = len(self._cache)
            self._cache.clear()
            logger.debug("storage/lancedb/connection: cache limpo (%d conn(s))", count)

    @property
    def cached_paths(self) -> list[Path]:
        """Lista de paths com conexões ativas no cache."""
        return list(self._cache.keys())


# ---------------------------------------------------------------------------
# Singleton processo-local
# ---------------------------------------------------------------------------

#: Cache padrão — use ``get_lancedb()`` para acesso simples.
_default_cache: LanceDBConnectionCache | None = None


def default_lancedb_cache() -> LanceDBConnectionCache:
    """Retorna o cache singleton do processo (cria na primeira chamada)."""
    global _default_cache
    if _default_cache is None:
        _default_cache = LanceDBConnectionCache()
    return _default_cache


async def get_lancedb(path: str | Path | None = None) -> Any:
    """Atalho: retorna a conexão LanceDB para ``path`` via cache padrão.

    Na maioria dos casos você só precisa disso:

        db = await get_lancedb()
        table = await db.open_table("articles")

    Args:
        path: Diretório do banco. Se None, usa ``settings.lancedb_dir``.

    Returns:
        ``lancedb.AsyncConnection``
    """
    return await default_lancedb_cache().connect(path)
