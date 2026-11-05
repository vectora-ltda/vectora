"""Pool de conexões SQLite assíncronas com aiosqlite.

Gerencia um conjunto de conexões prontas para uso, evitando o overhead de
abrir e fechar conexões a cada operação. Todas as conexões são criadas com
um conjunto fixo de PRAGMAs que habilitam WAL mode, busy timeout e outras
otimizações de desempenho e confiabilidade para uso embarcado.

Uso típico:
    pool = AsyncConnectionPool("data/vectora.db")
    await pool.open()

    async with pool.acquire() as conn:
        await conn.execute("SELECT 1")

    await pool.close()

Como context manager:
    async with AsyncConnectionPool.from_path("data/vectora.db") as pool:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO …")
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PRAGMAs aplicados a toda conexão nova
# ---------------------------------------------------------------------------

_PRAGMAS = (
    "PRAGMA journal_mode=WAL;"  # write-ahead log — leitores não bloqueiam escritor
    "PRAGMA busy_timeout=30000;"  # 30 s de espera antes de SQLITE_BUSY
    "PRAGMA synchronous=NORMAL;"  # fsync só em pontos de checkpoint — mais rápido que FULL
    "PRAGMA temp_store=MEMORY;"  # tabelas temporárias em RAM
    "PRAGMA mmap_size=268435456;"  # 256 MiB de mmap — reduz syscalls de leitura
    "PRAGMA foreign_keys=ON;"  # integridade referencial ativada
)


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------


class AsyncConnectionPool:
    """Pool de conexões aiosqlite com PRAGMAs de hardening (lite mode).

    Mantém entre ``min_size`` e ``max_size`` conexões abertas simultaneamente.
    Conexões são criadas sob demanda e devolvidas ao pool após o uso (não
    fechadas). A criação de novas conexões é serializada por ``_lock`` para
    evitar ultrapassar ``max_size``.

    Args:
        path:     Caminho para o arquivo ``.db``.
        min_size: Conexões abertas no ``open()``. Default 1.
        max_size: Limite superior de conexões simultâneas. Default 8.

    Raises:
        RuntimeError: Se ``acquire()`` for chamado antes de ``open()``.
    """

    def __init__(
        self,
        path: str | Path,
        min_size: int = 1,
        max_size: int = 8,
    ) -> None:
        if min_size < 1:
            raise ValueError("min_size deve ser >= 1")
        if max_size < min_size:
            raise ValueError("max_size deve ser >= min_size")

        self._path = Path(path)
        self._min_size = min_size
        self._max_size = max_size

        # Fila FIFO de conexões ociosas.  maxsize=max_size garante que a fila
        # nunca acumule mais conexões do que o máximo permitido.
        self._idle: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue(
            maxsize=max_size
        )
        self._lock = asyncio.Lock()
        self._size = 0  # total de conexões já criadas
        self._opened = False

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Cria as conexões iniciais (``min_size``) e configura o banco.

        Idempotente: chamadas repetidas são ignoradas.
        """
        if self._opened:
            return
        async with self._lock:
            if self._opened:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            for _ in range(self._min_size):
                conn = await self._new_conn()
                self._idle.put_nowait(conn)
            self._opened = True
            logger.info(
                "sqlite: pool aberto — path=%s min=%d max=%d",
                self._path,
                self._min_size,
                self._max_size,
            )

    async def close(self) -> None:
        """Fecha todas as conexões ociosas e reseta o pool.

        Conexões ativamente em uso pelo caller **não** são fechadas aqui —
        o caller deve concluir seu ``async with pool.acquire()`` antes.
        """
        closed = 0
        while True:
            try:
                conn = self._idle.get_nowait()
                try:
                    await conn.close()
                    closed += 1
                except Exception:
                    pass
            except asyncio.QueueEmpty:
                break
        self._opened = False
        self._size = 0
        logger.debug("storage/sqlite/pool: pool fechado (%d conn(s))", closed)

    @classmethod
    @asynccontextmanager
    async def from_path(
        cls,
        path: str | Path,
        min_size: int = 1,
        max_size: int = 8,
    ) -> AsyncGenerator[AsyncConnectionPool]:
        """Context manager que abre e fecha o pool automaticamente.

        Example:
            async with AsyncConnectionPool.from_path("data/db.sqlite") as pool:
                async with pool.acquire() as conn:
                    await conn.execute("SELECT 1")
        """
        pool = cls(path, min_size=min_size, max_size=max_size)
        await pool.open()
        try:
            yield pool
        finally:
            await pool.close()

    # ------------------------------------------------------------------
    # Aquisição de conexão
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[aiosqlite.Connection]:
        """Adquire uma conexão do pool.

        Ordem de prioridade:
        1. Conexão ociosa na fila (não-bloqueante).
        2. Nova conexão se ``_size < _max_size`` (serializado por ``_lock``).
        3. Aguarda conexão ociosa ser devolvida (bloqueante).

        A conexão é devolvida ao pool automaticamente ao sair do ``async with``.
        """
        if not self._opened:
            await self.open()

        conn: aiosqlite.Connection | None = None

        # 1. Tentativa rápida: pegar da fila sem bloquear
        with contextlib.suppress(asyncio.QueueEmpty):
            conn = self._idle.get_nowait()

        # 2. Criar nova conexão se ainda há slot disponível
        if conn is None:
            async with self._lock:
                if self._size < self._max_size:
                    conn = await self._new_conn()

        # 3. Todas as slots usadas — aguardar devolução ao pool
        if conn is None:
            conn = await self._idle.get()

        try:
            yield conn
        finally:
            # Devolve ao pool; em caso raro de fila cheia, fecha a conexão extra.
            try:
                self._idle.put_nowait(conn)
            except asyncio.QueueFull:
                logger.debug("storage/sqlite/pool: fila cheia, fechando conexão extra")
                with contextlib.suppress(Exception):
                    await conn.close()

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    async def _new_conn(self) -> aiosqlite.Connection:
        """Abre uma nova conexão aiosqlite e aplica os PRAGMAs de hardening."""
        # DIAGNÓSTICO TEMPORÁRIO — arquivo, não sys.__stderr__: a tentativa
        # anterior via __stderr__ nunca apareceu em NENHUM log de CI, nem em
        # testes que claramente passaram — o bypass de capture do pytest
        # pode simplesmente não funcionar neste ambiente (py-spy também
        # bloqueado por ptrace, mesmo com sudo — runner restringe). Um
        # arquivo é lido depois pelo workflow (cat), sem depender de
        # nenhuma captura de stream. Remover assim que a causa for achada.
        import tempfile
        import time
        from pathlib import Path as _Path

        import aiosqlite as _aiosqlite

        # tempfile.gettempdir() é /tmp no runner Linux da CI (onde o
        # workflow lê este arquivo via `cat /tmp/vectora_pool_diag.log`) —
        # caminho fixo funcionaria lá, mas quebra rodando local no Windows.
        _diag_path = _Path(tempfile.gettempdir()) / "vectora_pool_diag.log"

        def _diag(msg: str) -> None:
            with _diag_path.open("a", encoding="utf-8") as f:
                f.write(f"{time.time():.3f} {msg}\n")

        t0 = time.monotonic()
        _diag(f"início connect() path={self._path}")
        conn: aiosqlite.Connection = await _aiosqlite.connect(str(self._path))
        _diag(f"connect() retornou em {time.monotonic() - t0:.2f}s")
        conn.row_factory = _aiosqlite.Row
        await conn.executescript(_PRAGMAS)
        _diag(f"executescript concluído em {time.monotonic() - t0:.2f}s")
        self._size += 1
        logger.debug(
            "storage/sqlite/pool: nova conexão #%d para %s", self._size, self._path
        )
        return conn

    # ------------------------------------------------------------------
    # Propriedades de diagnóstico
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        """Caminho do arquivo SQLite."""
        return self._path

    @property
    def size(self) -> int:
        """Total de conexões abertas (ativas + ociosas)."""
        return self._size

    @property
    def idle(self) -> int:
        """Número de conexões ociosas disponíveis imediatamente."""
        return self._idle.qsize()

    def __repr__(self) -> str:
        return (
            f"AsyncConnectionPool(path={self._path!r}, "
            f"size={self._size}/{self._max_size}, idle={self.idle})"
        )
