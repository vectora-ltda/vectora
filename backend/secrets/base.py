"""Protocol base para providers de secrets do Vectora.

Qualquer provider implementa estas operações:
    unlock(user_id, master_password)  — abre/desbloqueia o vault do usuário
    lock(user_id)                     — fecha o vault e descarta chave em memória
    get(user_id, key)                 — lê secret; None se não existir
    set(user_id, key, value)          — grava/sobrescreve secret
    delete(user_id, key)              — remove secret
    list(user_id)                     — lista chaves (sem valores)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretsProvider(Protocol):
    """Interface que todos os providers de secrets devem implementar."""

    async def unlock(self, user_id: str, master_password: str) -> None:
        """Abre o vault do usuário (cria se for primeira vez)."""
        ...

    async def lock(self, user_id: str) -> None:
        """Fecha o vault e descarta a chave em memória."""
        ...

    async def get(self, user_id: str, key: str) -> str | None:
        """Retorna o valor do secret, ou None se não existir."""
        ...

    async def set(self, user_id: str, key: str, value: str) -> None:
        """Grava ou sobrescreve um secret."""
        ...

    async def delete(self, user_id: str, key: str) -> None:
        """Remove um secret (silencioso se não existir)."""
        ...

    async def list_keys(self, user_id: str) -> list[str]:
        """Lista as chaves dos secrets sem revelar valores."""
        ...
