"""Lifecycle for an optional user-managed llama.cpp server sidecar."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)
_process: asyncio.subprocess.Process | None = None


def llamacpp_status() -> dict[str, int | bool | None]:
    """Retorna somente o estado do processo iniciado por este módulo."""
    process = _process
    running = process is not None and process.returncode is None
    return {"running": running, "pid": process.pid if running and process else None}


async def start_llamacpp(
    executable: str | Path,
    model: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> asyncio.subprocess.Process:
    """Start llama-server with a local model and wait for its OpenAI endpoint."""
    global _process
    if _process is not None and _process.returncode is None:
        return _process
    executable_path = Path(executable).expanduser().resolve()
    model_path = Path(model).expanduser().resolve()
    if not executable_path.is_file() or not model_path.is_file():
        raise FileNotFoundError("llama-server ou modelo não encontrado")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("o sidecar deve escutar apenas no loopback")
    _process = await asyncio.create_subprocess_exec(
        str(executable_path),
        "-m",
        str(model_path),
        "--host",
        host,
        "--port",
        str(port),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            for _ in range(30):
                try:
                    response = await client.get(f"http://{host}:{port}/v1/models")
                    if response.is_success:
                        return _process
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
    except Exception:
        await stop_llamacpp()
        raise
    await stop_llamacpp()
    raise RuntimeError("llama.cpp não ficou pronto no tempo esperado")


async def stop_llamacpp() -> None:
    """Stop the managed process without affecting user-installed servers."""
    global _process
    process, _process = _process, None
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()
