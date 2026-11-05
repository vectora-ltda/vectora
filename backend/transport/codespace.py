"""CodespaceTransport — workspace remoto em GitHub Codespaces.

Reusa o ``gh`` CLI já presente em ``src/tools/gh.py`` para descobrir
codespaces e rodar comandos via ``gh codespace ssh -c <name>``. O
``gh`` cuida do túnel (autenticação via OAuth do user), o que evita
gerenciar chaves SSH ou tokens diretamente.

Workspaces remotos precisam do ``codespace_name`` no modelo
:class:`src.types.Workspace`. O ``cwd`` no Codespace é o diretório
default que o ``gh codespace ssh`` abre (``/workspaces/<repo>`` na
maioria dos templates), salvo ``remote_path`` definido.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex

from backend.transport.base import DirEntry, RunResult

logger = logging.getLogger(__name__)


async def list_codespaces() -> list[dict]:
    """Lista codespaces do user via ``gh codespace list --json``.

    Cada item tem: ``name``, ``repository``, ``state``, ``gitStatus``.
    Devolve ``[]`` se ``gh`` não está autenticado ou indisponível.
    """
    proc = await asyncio.create_subprocess_exec(
        "gh",
        "codespace",
        "list",
        "--json",
        "name,repository,state,gitStatus",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=15.0)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return []
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(out.decode("utf-8", errors="replace") or "[]")
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    return []


async def ensure_started(codespace_name: str) -> bool:
    """Garante que o codespace está em estado executável.

    ``gh codespace start`` é idempotente: roda mesmo se já estiver
    rodando. Devolve True em sucesso.
    """
    proc = await asyncio.create_subprocess_exec(
        "gh",
        "codespace",
        "start",
        "--codespace",
        codespace_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=120.0)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False
    return proc.returncode == 0


class CodespaceTransport:
    """Transport via ``gh codespace ssh`` — um subprocess por comando.

    Cada chamada de ``run`` levanta um subprocess ``gh codespace ssh
    -c <name> -- <cmd>``; sem pool persistente. Performance pior que
    o ``SshTransport``, mas elimina gerência de chave (gh OAuth basta).
    """

    def __init__(self, *, codespace_name: str, remote_path: str | None) -> None:
        self._name = codespace_name
        self._remote_path = remote_path

    async def list_dir(self, path: str) -> list[DirEntry]:
        result = await self.run(
            ["sh", "-c", f"ls -la --time-style=long-iso {shlex.quote(path)}"],
            cwd=".",
            timeout=15.0,
        )
        entries: list[DirEntry] = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 8:
                continue
            mode = parts[0]
            name = parts[-1]
            if name in (".", ".."):
                continue
            entries.append(
                DirEntry(
                    name=name,
                    path=f"{path.rstrip('/')}/{name}",
                    is_dir=mode.startswith("d"),
                )
            )
        return entries

    async def read_file(self, path: str, max_bytes: int = 1_048_576) -> bytes:
        result = await self.run(
            ["sh", "-c", f"head -c {max_bytes} {shlex.quote(path)}"],
            cwd=".",
            timeout=30.0,
        )
        return result.stdout.encode("utf-8", errors="replace")

    async def write_file(self, path: str, data: bytes) -> None:
        import base64

        b64 = base64.b64encode(data).decode("ascii")
        await self.run(
            ["sh", "-c", f"echo '{b64}' | base64 -d > {shlex.quote(path)}"],
            cwd=".",
            timeout=60.0,
        )

    async def run(
        self,
        cmd: list[str],
        cwd: str,
        timeout: float = 30.0,  # noqa: ASYNC109
    ) -> RunResult:
        joined = " ".join(shlex.quote(part) for part in cmd)
        effective_cwd = self._remote_path or cwd
        remote_cmd = (
            f"cd {shlex.quote(effective_cwd)} 2>/dev/null && {joined}"
            if effective_cwd and effective_cwd not in {"", "."}
            else joined
        )
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "codespace",
            "ssh",
            "--codespace",
            self._name,
            "--",
            "bash",
            "-lc",
            remote_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return RunResult(
                exit_code=-1, stdout="", stderr=f"Timeout after {timeout}s"
            )
        return RunResult(
            exit_code=proc.returncode or 0,
            stdout=out.decode("utf-8", errors="replace"),
            stderr=err.decode("utf-8", errors="replace"),
        )

    async def close(self) -> None:
        # gh codespace ssh é one-shot por chamada; nada a fechar.
        return None
