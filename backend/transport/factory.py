"""Factory que escolhe o transport correto por workspace.

Cache por ``workspace.id``: cada workspace tem uma única instância de
backend, reaproveitada entre chamadas (pool de conexões SSH, processo
filho do ``gh codespace ssh``, etc.).
"""

from __future__ import annotations

import logging
from typing import Any

from backend.transport.base import TransportBackend
from backend.transport.local import LocalTransport

logger = logging.getLogger(__name__)

#: Cache de instâncias por workspace.id.
_CACHE: dict[str, TransportBackend] = {}


def get_transport(workspace: Any) -> TransportBackend:
    """Resolve o backend correto para um workspace.

    Suporta ``local``, ``ssh`` e ``codespace``; qualquer outro valor de
    ``workspace.transport`` cai em ``LocalTransport`` com aviso no log.
    """
    ws_id = getattr(workspace, "id", None)
    if ws_id is None:
        # Sem id — devolve singleton local efêmero (não cacheia).
        return LocalTransport()

    cached = _CACHE.get(ws_id)
    if cached is not None:
        return cached

    transport = str(getattr(workspace, "transport", "local"))
    backend: TransportBackend
    if transport == "local":
        backend = LocalTransport()
    elif transport == "ssh":
        from backend.transport.ssh import SshTransport

        backend = SshTransport(
            remote_host=str(getattr(workspace, "remote_host", "") or ""),
            ssh_key_id=getattr(workspace, "ssh_key_id", None),
            user_id=getattr(workspace, "trusted_by", None),
        )
    elif transport == "codespace":
        from backend.transport.codespace import CodespaceTransport

        backend = CodespaceTransport(
            codespace_name=str(getattr(workspace, "codespace_name", "") or ""),
            remote_path=getattr(workspace, "remote_path", None),
        )
    else:
        logger.warning(
            "Workspace %s tem transport desconhecido '%s'; usando local.",
            ws_id,
            transport,
        )
        backend = LocalTransport()

    _CACHE[ws_id] = backend
    return backend


async def close_all_transports() -> None:
    """Encerra todos os backends cacheados (chamar no shutdown do server)."""
    for ws_id, backend in list(_CACHE.items()):
        try:
            await backend.close()
        except Exception:
            logger.warning("Falha ao fechar transport de %s", ws_id, exc_info=True)
    _CACHE.clear()
