"""Lifecycle for an optional user-managed llama.cpp server sidecar."""

from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_READINESS_INTERVAL_SECONDS = 0.5
_READINESS_TIMEOUT_SECONDS = 120.0
_process: asyncio.subprocess.Process | None = None
_lifecycle_lock = asyncio.Lock()
_process_spec: tuple[str, str, str, int] | None = None
_process_options: (
    tuple[str | None, str | None, int | None, int | None, int | None, int | None, bool]
    | None
) = None
_process_started_at: float | None = None


def _state_path() -> Path:
    root = Path(os.getenv("VECTORA_HOME", str(Path.home() / ".vectora")))
    return root / "llamacpp-sidecar.json"


def _process_creation_time(pid: int) -> float | None:
    """Obtém a criação do processo quando o sistema a expõe via procfs."""
    stat_path = Path(f"/proc/{pid}/stat")
    boot_path = Path("/proc/stat")
    if not stat_path.is_file() or not boot_path.is_file():
        return None
    try:
        fields = stat_path.read_text(encoding="utf-8").split()
        start_ticks = int(fields[21])
        boot_time = next(
            float(line.split()[1])
            for line in boot_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("btime ")
        )
        clock_ticks = ctypes.CDLL(None).sysconf(2)
        if clock_ticks <= 0:
            return None
        return boot_time + start_ticks / clock_ticks
    except (OSError, IndexError, StopIteration, ValueError):
        return None


def _pid_exists(pid: int) -> bool:
    """Verifica existência sem enviar CTRL_C_EVENT no Windows."""
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


async def _write_state(
    process: asyncio.subprocess.Process, started_at: float | None = None
) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": process.pid,
        "executable": _process_spec[0] if _process_spec else None,
        "model": _process_spec[1] if _process_spec else None,
        "host": _process_spec[2] if _process_spec else None,
        "port": _process_spec[3] if _process_spec else None,
        "alias": _process_options[0] if _process_options else None,
        "mmproj": _process_options[1] if _process_options else None,
        "ctx_size": _process_options[2] if _process_options else None,
        "n_gpu_layers": _process_options[3] if _process_options else None,
        "threads": _process_options[4] if _process_options else None,
        "parallel": _process_options[5] if _process_options else None,
        "jinja": _process_options[6] if _process_options else False,
        "started_at": started_at if started_at is not None else time.time(),
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


def _health_url(host: str, port: int) -> str:
    """Monta a URL de saúde usada antes de consultar o catálogo OpenAI."""
    url_host = f"[{host}]" if ":" in host else host
    return f"http://{url_host}:{port}/health"


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
        stale = not _pid_exists(persisted_pid)
        persisted_started = persisted.get("started_at") if persisted else None
        actual_started = _process_creation_time(persisted_pid)
        if (
            not stale
            and isinstance(persisted_started, (int, float))
            and actual_started is not None
            and abs(actual_started - float(persisted_started)) > 2
        ):
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
    port: int = 18080,
    alias: str | None = None,
    mmproj: str | Path | None = None,
    ctx_size: int | None = None,
    n_gpu_layers: int | None = None,
    threads: int | None = None,
    parallel: int | None = None,
    jinja: bool = False,
) -> asyncio.subprocess.Process:
    """Start llama-server with a local model and wait for its OpenAI endpoint."""
    global _process, _process_spec, _process_options, _process_started_at
    async with _lifecycle_lock:
        if alias is not None and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}", alias
        ):
            raise ValueError("alias inválido")
        if ctx_size is not None and not 1 <= ctx_size <= 1_000_000:
            raise ValueError("ctx_size inválido")
        if n_gpu_layers is not None and not -1 <= n_gpu_layers <= 1000:
            raise ValueError("n_gpu_layers inválido")
        if threads is not None and not 1 <= threads <= 1024:
            raise ValueError("threads inválido")
        if parallel is not None and not 1 <= parallel <= 256:
            raise ValueError("parallel inválido")
        mmproj_path = Path(mmproj).expanduser().resolve() if mmproj else None
        if mmproj_path is not None and not mmproj_path.is_file():
            raise FileNotFoundError("mmproj não encontrado")
        options = (
            alias,
            str(mmproj_path) if mmproj_path else None,
            ctx_size,
            n_gpu_layers,
            threads,
            parallel,
            jinja,
        )
        if _process is not None and _process.returncode is None:
            requested = (
                str(Path(executable).expanduser().resolve()),
                str(Path(model).expanduser().resolve()),
                host,
                port,
            )
            if _process_spec != requested or _process_options != options:
                raise RuntimeError("já existe um sidecar com outra configuração")
            return _process
        executable_path = Path(executable).expanduser().resolve()
        model_path = Path(model).expanduser().resolve()
        if not executable_path.is_file() or not model_path.is_file():
            raise FileNotFoundError("llama-server ou modelo não encontrado")
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("o sidecar deve escutar apenas no loopback")
        _process_spec = (str(executable_path), str(model_path), host, port)
        _process_options = options
        _process_started_at = time.time()
        command = [
            str(executable_path),
            "-m",
            str(model_path),
            "--host",
            host,
            "--port",
            str(port),
        ]
        if alias:
            command.extend(["--alias", alias])
        if mmproj_path:
            command.extend(["--mmproj", str(mmproj_path)])
        if ctx_size is not None:
            command.extend(["--ctx-size", str(ctx_size)])
        if n_gpu_layers is not None:
            command.extend(["--n-gpu-layers", str(n_gpu_layers)])
        if threads is not None:
            command.extend(["--threads", str(threads)])
        if parallel is not None:
            command.extend(["--parallel", str(parallel)])
        if jinja:
            command.append("--jinja")
        _process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                health_ready = False
                deadline = time.monotonic() + float(
                    os.getenv(
                        "VECTORA_LLAMACPP_READINESS_TIMEOUT_S",
                        str(_READINESS_TIMEOUT_SECONDS),
                    )
                )
                while time.monotonic() < deadline:
                    if _process is None or _process.returncode is not None:
                        break
                    try:
                        if not health_ready:
                            health = await client.get(_health_url(host, port))
                            health_ready = health.is_success
                        if health_ready:
                            response = await client.get(_readiness_url(host, port))
                            if (
                                _process is not None
                                and _process.returncode is None
                                and response.is_success
                            ):
                                await _write_state(_process, _process_started_at)
                                return _process
                    except httpx.HTTPError:
                        pass
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        await asyncio.sleep(min(_READINESS_INTERVAL_SECONDS, remaining))
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
    global _process, _process_spec, _process_options, _process_started_at
    process = _process
    if process is None or process.returncode is not None:
        try:
            await _clear_state()
        finally:
            _process = None
            _process_spec = None
            _process_options = None
            _process_started_at = None
        return

    async def terminate_process() -> None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()

    try:
        await terminate_process()
    except asyncio.CancelledError:
        # A cancelled request must not abandon a live sidecar. Finish the
        # termination under shielding, then propagate cancellation to the
        # caller after ownership and persisted state are consistent.
        await asyncio.shield(terminate_process())
        raise
    finally:
        try:
            await _clear_state()
        finally:
            _process = None
            _process_spec = None
            _process_options = None
            _process_started_at = None
