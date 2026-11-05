"""Camada de transporte para o filesystem do workspace.

Workspaces podem viver em três lugares: local, host SSH remoto, ou
GitHub Codespace. Para que as tools (``fs.py``, ``git.py``, terminal)
não precisem conhecer essa distinção, cada uma das três varíantes
implementa o mesmo ``TransportBackend``:

    backend = get_transport(workspace)
    await backend.read_file(path, max_bytes=...)
    await backend.run(["ls", "-la"], cwd=...)
    await backend.open_pty(shell=..., cwd=...)

A factory ``get_transport()`` cacheia por ``workspace.id``.
"""

from __future__ import annotations

from backend.transport.base import DirEntry, RunResult, TransportBackend
from backend.transport.factory import close_all_transports, get_transport
from backend.transport.local import LocalTransport

__all__ = [
    "DirEntry",
    "LocalTransport",
    "RunResult",
    "TransportBackend",
    "close_all_transports",
    "get_transport",
]
