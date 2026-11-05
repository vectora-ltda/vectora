"""Protocol e tipos compartilhados pela camada de transporte."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class DirEntry:
    """Entrada simplificada de listagem de diretório."""

    name: str
    path: str
    is_dir: bool
    size: int | None = None


@dataclass(slots=True)
class RunResult:
    """Resultado de ``run(cmd, cwd, timeout)``."""

    exit_code: int
    stdout: str
    stderr: str


@runtime_checkable
class TransportBackend(Protocol):
    """Interface comum para Local, SSH e Codespace.

    Todas as operações são assíncronas para permitir I/O bloqueante
    (ex.: rede em SSH). Implementações locais que rodam em filesystem
    podem usar ``asyncio.to_thread`` internamente.
    """

    async def list_dir(self, path: str) -> list[DirEntry]:
        """Lista o conteúdo de ``path`` (não recursivo)."""
        ...

    async def read_file(self, path: str, max_bytes: int = 1_048_576) -> bytes:
        """Lê o arquivo. Trunca em ``max_bytes`` para evitar overflow."""
        ...

    async def write_file(self, path: str, data: bytes) -> None:
        """Sobrescreve o arquivo (cria se não existir)."""
        ...

    async def run(
        self,
        cmd: list[str],
        cwd: str,
        timeout: float = 30.0,  # noqa: ASYNC109
    ) -> RunResult:
        """Roda um comando sincronicamente e devolve stdout/stderr/exit.

        ``timeout`` é um parâmetro de configuração da execução, não da
        cancelação cooperativa do asyncio — internamente cada backend
        usa ``asyncio.wait_for`` ou equivalente.
        """
        ...

    async def close(self) -> None:
        """Libera conexões (no-op no Local, fecha pool no SSH)."""
        ...
