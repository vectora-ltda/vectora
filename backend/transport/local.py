"""LocalTransport — filesystem do host onde o Vectora roda."""

from __future__ import annotations

import asyncio
from pathlib import Path

from backend.transport.base import DirEntry, RunResult


class LocalTransport:
    """Implementação direta sobre ``pathlib`` e ``asyncio.create_subprocess_exec``.

    Consolida o filesystem local atrás do mesmo Protocol implementado
    por :class:`SshTransport` e :class:`CodespaceTransport`.
    """

    async def list_dir(self, path: str) -> list[DirEntry]:
        def _list() -> list[DirEntry]:
            base = Path(path).expanduser().resolve()
            entries: list[DirEntry] = []
            try:
                for item in sorted(base.iterdir(), key=lambda p: p.name.lower()):
                    try:
                        is_dir = item.is_dir()
                        size = None if is_dir else item.stat().st_size
                    except OSError:
                        continue
                    entries.append(
                        DirEntry(
                            name=item.name,
                            path=str(item),
                            is_dir=is_dir,
                            size=size,
                        )
                    )
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                return []
            return entries

        return await asyncio.to_thread(_list)

    async def read_file(self, path: str, max_bytes: int = 1_048_576) -> bytes:
        def _read() -> bytes:
            p = Path(path).expanduser()
            with p.open("rb") as fh:
                return fh.read(max_bytes)

        return await asyncio.to_thread(_read)

    async def write_file(self, path: str, data: bytes) -> None:
        def _write() -> None:
            p = Path(path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("wb") as fh:
                fh.write(data)

        await asyncio.to_thread(_write)

    async def run(
        self,
        cmd: list[str],
        cwd: str,
        timeout: float = 30.0,  # noqa: ASYNC109
    ) -> RunResult:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return RunResult(
                exit_code=-1,
                stdout="",
                stderr=f"Timeout after {timeout}s",
            )
        return RunResult(
            exit_code=proc.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    async def close(self) -> None:
        # Nada a fechar — o filesystem local não mantém estado.
        return None
