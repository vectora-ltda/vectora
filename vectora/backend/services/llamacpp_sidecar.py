"""Lifecycle for an optional user-managed llama.cpp server sidecar."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)
_process: asyncio.subprocess.Process | None = None
_lifecycle_lock = asyncio.Lock()
_process_spec: tuple[str, str, str, int] | None = None


def _state_path() -> Path:
    root = Path(os.getenv("VECTORA_HOME", str(Path.home() / ".vectora")))
    return root / "llamacpp-sidecar.json"


async def _write_state(process: asyncio.subprocess.Process) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": process.pid,
        "executable": _process_spec[0] if _process_spec else None,
        "model": _process_spec[1] if _process_spec else None,
        "host": _process_spec[2] if _process_spec else None,
        "port": _process_spec[3] if _process_spec else None,
        "started_at": time.time(),
    }
    temporary = path.with_suffix(".tmp")
    await asyncio.to_thread(
        temporary.write_text, json.dumps(payload, ensure_ascii=False), "utf-8"
    )
    await asyncio.to_thread(temporary.replace, path)


async def _clear_state() -> None:
    await asyncio.to_thread(_state_path().unlink, missing_ok=True)


def _readiness_url(host: str, port: int) -> str:
    """Monta a URL de readiness preservando a sintaxe de hosts IPv6."""
    url_host = f"[{host}]" if ":" in host else host
    return f"http://{url_host}:{port}/v1/models"


def llamacpp_status() -> dict[str, int | bool | str | None]:
    """Retorna somente o estado do processo iniciado por este módulo."""
    process = _process
    running = process is not None and process.returncode is None
    spec = _process_spec if running else None
    persisted: dict[str, object] | None = None
    if not running:
        try:
            persisted = json.loads(_state_path().read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            persisted = None
    stale = False
    persisted_pid = persisted.get("pid") if persisted else None
    if isinstance(persisted_pid, int):
        try:
            os.kill(persisted_pid, 0)
        except OSError:
            stale = True
    return {
        "running": running,
        "pid": process.pid if running and process else None,
        "executable": spec[0] if spec else None,
        "model": spec[1] if spec else None,
        "host": spec[2] if spec else None,
        "port": spec[3] if spec else None,
        "persisted_pid": persisted_pid,
        "stale_state": stale,
    }


async def start_llamacpp(
    executable: str | Path,
    model: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> asyncio.subprocess.Process:
    """Start llama-server with a local model and wait for its OpenAI endpoint."""
    global _process, _process_spec
    async with _lifecycle_lock:
        if _process is not None and _process.returncode is None:
            requested = (
                str(Path(executable).expanduser().resolve()),
                str(Path(model).expanduser().resolve()),
                host,
                port,
            )
            if _process_spec != requested:
                raise RuntimeError("já existe um sidecar com outra configuração")
            return _process
        executable_path = Path(executable).expanduser().resolve()
        model_path = Path(model).expanduser().resolve()
        if not executable_path.is_file() or not model_path.is_file():
            raise FileNotFoundError("llama-server ou modelo não encontrado")
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("o sidecar deve escutar apenas no loopback")
        _process_spec = (str(executable_path), str(model_path), host, port)
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
                        response = await client.get(_readiness_url(host, port))
                        if response.is_success:
                            await _write_state(_process)
                            return _process
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.25)
        except BaseException:
            await _stop_process()
            raise
        await _stop_process()
        raise RuntimeError("llama.cpp não ficou pronto no tempo esperado")


async def stop_llamacpp() -> None:
    """Stop the managed process without affecting user-installed servers."""
    async with _lifecycle_lock:
        await _stop_process()


async def _stop_process() -> None:
    """Encerra o único processo cujo handle pertence a este módulo."""
    global _process, _process_spec
    process, _process = _process, None
    _process_spec = None
    await _clear_state()
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()
