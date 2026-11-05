"""Protocols tipados para todos os backends de storage do Vectora.

Define as interfaces que cada implementação deve satisfazer. Backends são
trocáveis em runtime: a factory (``storage/factory.py``) escolhe a
implementação correta de acordo com ``settings.storage_mode``.

Modos:
    lite     — SQLite + LanceDB (default, sem deps externas)
    complete — Postgres + Qdrant + Redis (Pro gate)

Cada Protocol tem um método ``health() -> HealthResult`` que retorna
``{"ok": True}`` ou ``{"ok": False, "error": "..."}`` para o endpoint
``GET /admin/storage`` (F10) e o CLI ``vectora storage info`` (F11).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from backend.vtypes.storage import HealthResult


def _ok() -> HealthResult:
    """Executa a operação ok."""
    return {"ok": True, "error": None}


def _err(msg: str) -> HealthResult:
    """Executa a operação err."""
    return {"ok": False, "error": msg}


# ---------------------------------------------------------------------------
# Store — armazenamento de memórias do agente
# ---------------------------------------------------------------------------


@runtime_checkable
class StoreBackend(Protocol):
    """Backend de store para memórias e knowledge do agente.

    Implementações:
        lite     — ``InMemoryStore`` com índice Cohere (F5)
        complete — ``AsyncPostgresStore`` com pgvector (F5)
    """

    async def aget(self, namespace: tuple[str, ...], key: str) -> Any:
        """Retorna o item ou None."""
        ...

    async def aput(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
    ) -> None:
        """Grava ou sobrescreve o item."""
        ...

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        """Remove o item (sem erro se não existir)."""
        ...

    async def asearch(
        self,
        namespace: tuple[str, ...],
        *,
        query: str | None = None,
        limit: int = 10,
    ) -> list[Any]:
        """Busca itens por namespace, com busca semântica opcional."""
        ...

    async def health(self) -> HealthResult:
        """Verifica se o backend está acessível."""
        ...


# ---------------------------------------------------------------------------
# VectorStore — armazenamento e busca de embeddings
# ---------------------------------------------------------------------------


from backend.storage.vectorstore.base import (
    VectorHit,
    VectorRow,
    VectorStoreBackend,
)

# ---------------------------------------------------------------------------
# AuthDB — usuários, tokens, audit, invites
# ---------------------------------------------------------------------------


@runtime_checkable
class AuthDB(Protocol):
    """Backend de persistência de autenticação.

    Implementações:
        lite     — SQLite via ``src/services/auth.py`` (F7)
        complete — Postgres via ``src/services/auth_pg.py`` (F7)
    """

    async def health(self) -> HealthResult:
        """Verifica se o banco está acessível."""
        ...


# ---------------------------------------------------------------------------
# SessionDB — metadados de threads e checkpoints de rewind
# ---------------------------------------------------------------------------


@runtime_checkable
class SessionDB(Protocol):
    """Backend de metadados de sessões/threads.

    Implementações:
        lite     — SQLite via ``src/api/handlers/threads.py`` (F7)
        complete — Postgres (F7)
    """

    async def health(self) -> HealthResult:
        """Verifica se o banco está acessível."""
        ...


# ---------------------------------------------------------------------------
# QueueDB — fila de embedding (background worker)
# ---------------------------------------------------------------------------


@runtime_checkable
class QueueDB(Protocol):
    """Backend de fila de tarefas assíncronas (embedding queue).

    Implementações:
        lite     — SQLite via ``src/services/queue.py`` (F8)
        complete — Postgres via ``SELECT … FOR UPDATE SKIP LOCKED`` (F8)
    """

    async def enqueue(self, task: dict[str, Any]) -> str:
        """Enfileira uma tarefa e retorna o ID."""
        ...

    async def dequeue(self, limit: int = 1) -> list[dict[str, Any]]:
        """Retira até ``limit`` tarefas da fila (FIFO)."""
        ...

    async def health(self) -> HealthResult:
        """Verifica se a fila está acessível."""
        ...


# ---------------------------------------------------------------------------
# SecretsDB — segredos cifrados por usuário
# ---------------------------------------------------------------------------


@runtime_checkable
class SecretsDB(Protocol):
    """Backend de segredos cifrados (API keys, tokens de terceiros).

    Implementações:
        lite     — SQLite via ``src/services/secrets/internal.py`` (F7)
        complete — Postgres (F7)
    """

    async def get(self, user_id: str, key: str) -> bytes | None:
        """Retorna o ciphertext ou None."""
        ...

    async def set(
        self, user_id: str, key: str, ciphertext: bytes, nonce: bytes
    ) -> None:
        """Grava ou sobrescreve o segredo."""
        ...

    async def delete(self, user_id: str, key: str) -> None:
        """Remove o segredo (sem erro se não existir)."""
        ...

    async def health(self) -> HealthResult:
        """Verifica se o banco está acessível."""
        ...


# ---------------------------------------------------------------------------
# TracesDB — spans de observabilidade
# ---------------------------------------------------------------------------


@runtime_checkable
class TracesDB(Protocol):
    """Backend de spans de observabilidade.

    Implementação atual: SQLite via ``src/services/tracer.py``.
    Futura: Postgres (F7) ou backend externo (OpenTelemetry).
    """

    async def health(self) -> HealthResult:
        """Verifica se o banco de traces está acessível."""
        ...


# ---------------------------------------------------------------------------
# Re-exporta helpers
# ---------------------------------------------------------------------------

__all__ = [
    "AuthDB",
    "HealthResult",
    "QueueDB",
    "SecretsDB",
    "SessionDB",
    "StoreBackend",
    "TracesDB",
    "VectorHit",
    "VectorRow",
    "VectorStoreBackend",
    "_err",
    "_ok",
]
