"""NATS embutido como sidecar do backend — persistência de fila/KV sem Redis.

Mesmo padrão de spawn que o Electron já usa pro backend Python
(``electron/src/main.ts``: resolve o binário, escolhe porta livre, lê stdout
pra achar o sinal de "pronto", encerra limpo no shutdown) — aqui é o backend
Python que sobe o ``nats-server`` como SEU PRÓPRIO sidecar, um nível abaixo,
disponível igualmente em desktop e em modo servidor/VPS.

O binário ``nats-server`` (Go, ~15-20MB, sem dependências) é baixado por
``scons nats`` pra ``vectora/resources/`` e embutido no executável final por
DOIS empacotadores diferentes, então precisa de DOIS caminhos de resolução em
runtime — mesma resolução de bundle congelado que ``server.py::
_chat_static_root`` já usa pro ``frontend/dist``:

- Electron (``electron-builder`` ``extraResources``): resource fica ao lado
  do app empacotado; o processo Electron passa o caminho explícito via
  ``VECTORA_NATS_BINARY``.
- Standalone/VPS (``build-hybrid.py``: Nuitka compila o backend, PyInstaller
  empacota em ``--onedir``): o binário entra via ``--add-binary`` numa pasta
  ``nats/`` dentro do bundle, achado em runtime por ``sys._MEIPASS`` (onedir/
  onefile do PyInstaller) ou ``sys.__compiled__.containing_dir``/
  ``NUITKA_ONEFILE_PARENT`` (onefile do Nuitka puro, sem PyInstaller).

Em dev, ``shutil.which`` resolve uma instalação local (ex.: ``choco install
nats-server`` / ``brew install nats-server``) ou o resource baixado em
``vectora/resources/`` por ``scons nats``. Sem o binário disponível em
NENHUM desses caminhos, o sidecar não sobe e ``get_mq()``/``get_kv()`` caem
pro fallback em memória — nunca impede o backend de iniciar.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import socket
import subprocess  # nosec B404 — só mata/consulta um PID já conhecido nosso, sem shell
import sys
from pathlib import Path
from typing import Any

from backend.services.subprocess_logging import pipe_to_logger
from backend.services.subprocess_sidecar_utils import (
    LazyLock,
    sidecar_process_options,
    terminate_gracefully,
)
from backend.settings import settings

logger = logging.getLogger(__name__)

_PID_FILE_NAME = "sidecar.pid"
# Cap da lista de PIDs órfãos rastreados — best-effort, nunca deixa o
# arquivo crescer indefinidamente se algum PID resistir a `_kill_pid`
# repetidamente entre sessões.
_PID_LIST_CAP = 10

_proc: asyncio.subprocess.Process | None = None
_url: str | None = None
_log_task: asyncio.Task | None = None
# Handle da Windows Job Object (ver _assign_to_job_object_best_effort) —
# vive pelo tempo de vida do processo Python inteiro, nunca fechado
# explicitamente (fechar cedo demais mataria o nats-server via
# KILL_ON_JOB_CLOSE mesmo num shutdown gracioso intencional).
_job_handle: int | None = None
_spawn_lock = LazyLock()

_READY_TIMEOUT_S = 10.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _exe_name() -> str:
    return "nats-server.exe" if sys.platform == "win32" else "nats-server"


def _frozen_bundle_bases() -> list[Path]:
    """Diretórios-raiz de bundle congelado onde ``build-hybrid.py`` pode ter
    colocado a pasta ``nats/`` — mesma fonte que ``_chat_static_root`` usa."""
    bases: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))
    compiled = getattr(sys, "__compiled__", None)
    if compiled is not None and hasattr(compiled, "containing_dir"):
        bases.append(Path(compiled.containing_dir))
    nuitka_parent = os.environ.get("NUITKA_ONEFILE_PARENT")
    if nuitka_parent:
        bases.append(Path(nuitka_parent))
    return bases


def _resolve_binary() -> str | None:
    """Localiza o binário ``nats-server``.

    Ordem: override explícito (``VECTORA_NATS_BINARY``, apontado pelo Electron
    pro binário empacotado via ``extraResources``) → bundle congelado
    (PyInstaller/Nuitka onefile, pasta ``nats/`` embutida por
    ``build-hybrid.py`` — vence o PATH pra nunca rodar uma versão desalinhada
    instalada à parte na máquina) → PATH (dev, ex.: ``choco/brew install
    nats-server``) → árvore-fonte (``vectora/resources/``, baixado por
    ``scons nats``).
    """
    override = os.getenv("VECTORA_NATS_BINARY")
    if override and Path(override).is_file():
        return override

    exe_name = _exe_name()

    for base in _frozen_bundle_bases():
        candidate = base / "nats" / exe_name
        if candidate.is_file():
            return str(candidate)

    from_path = shutil.which("nats-server")
    if from_path:
        return from_path

    bundled = Path(__file__).resolve().parent.parent.parent / "resources" / exe_name
    if bundled.is_file():
        return str(bundled)
    return None


async def ensure_nats_sidecar() -> str | None:
    """Sobe (ou reusa) o sidecar ``nats-server`` com JetStream habilitado.

    Retorna a URL de conexão (``nats://127.0.0.1:<porta>``) ou ``None`` se o
    binário não estiver disponível ou o processo falhar ao iniciar — nesse
    caso o chamador (``get_mq``/``get_kv``) deve degradar pro fallback de
    memória, nunca impedir o backend de subir.

    ``_spawn_lock`` serializa chamadas concorrentes — sem ele, duas corridas
    passariam pelo check ``_proc is None`` antes de qualquer uma setar
    ``_proc``, subindo dois ``nats-server`` em portas diferentes.
    """
    global _proc, _url, _log_task

    async with _spawn_lock.get():
        if _proc is not None and _proc.returncode is None:
            return _url

        binary = _resolve_binary()
        if binary is None:
            logger.info(
                "nats_sidecar: binário nats-server não encontrado — sem persistência de fila"
            )
            return None

        store_dir = settings.vectora_home / "nats"
        store_dir.mkdir(parents=True, exist_ok=True)

        # Um PID **vivo** no pid file não é órfão: é um sidecar em uso. Matá-lo
        # (como este código fazia) derruba o JetStream de quem está usando, e
        # o cliente daquele processo cai em reconexão infinita. Só há dois
        # desfechos legítimos aqui:
        #   * vivo e aceitando conexão -> reusa, não sobe um segundo servidor
        #     sobre o mesmo store (dois derrubam um ao outro);
        #   * vivo mas mudo (zumbi) ou já morto -> descarta e sobe um novo.
        registered = _read_stale_pids(store_dir)
        registered_url = _read_registered_url(store_dir)
        alive = [p for p in registered if _pid_is_alive_safe(p)]

        if alive and registered_url and _responds(registered_url):
            _url = registered_url
            logger.info(
                "nats_sidecar: reusando sidecar já ativo em %s (pid=%s)",
                registered_url,
                alive[0],
            )
            return _url

        for zumbi in alive:
            # Best-effort: falha ao matar um PID nunca aborta a subida do
            # sidecar novo nem impede tentar os demais da lista.
            try:
                logger.info(
                    "nats_sidecar: descartando sidecar que não responde (pid=%s)",
                    zumbi,
                )
                _kill_pid(zumbi)
            except Exception:
                logger.warning(
                    "nats_sidecar: falha ao matar pid %s — best-effort, seguindo",
                    zumbi,
                    exc_info=True,
                )

        port = _free_port()

        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                "-js",
                "-sd",
                str(store_dir),
                "-p",
                str(port),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **sidecar_process_options(),
            )
        except Exception:
            logger.warning("nats_sidecar: falha ao spawnar nats-server", exc_info=True)
            return None

        ready = await _wait_ready(proc)
        if not ready:
            with_kill(proc)
            return None

        _proc = proc
        _log_task = asyncio.create_task(
            pipe_to_logger(proc.stdout, logger, prefix="nats")
        )
        _url = f"nats://127.0.0.1:{port}"
        _write_pid_list(store_dir, [proc.pid], url=_url)
        _assign_to_job_object_best_effort(proc.pid)
        logger.info(
            "nats_sidecar: pronto em %s (pid=%s, store=%s)",
            _url,
            proc.pid,
            store_dir,
        )
        return _url


def _assign_to_job_object_best_effort(pid: int) -> None:
    """Defesa em profundidade contra `SIGKILL`/"Finalizar tarefa" no
    processo Python (Windows only — ver `backend/services/
    win_job_object.py` pela limitação equivalente em POSIX, honestamente
    documentada lá). Best-effort: qualquer falha aqui é só logada, nunca
    impede o sidecar de funcionar via shutdown gracioso normal."""
    global _job_handle
    if sys.platform != "win32":
        return
    try:
        from backend.services.win_job_object import (
            assign_process_to_job,
            create_job_object,
        )

        if _job_handle is None:
            _job_handle = create_job_object()
        if _job_handle is not None:
            assign_process_to_job(_job_handle, pid)
    except Exception:
        logger.warning(
            "nats_sidecar: falha ao associar sidecar à Job Object do Windows",
            exc_info=True,
        )


async def _wait_ready(proc: asyncio.subprocess.Process) -> bool:
    """Lê o stdout até ver a linha de "Server is ready" ou o processo morrer."""
    if proc.stdout is None:
        return False
    try:
        async with asyncio.timeout(_READY_TIMEOUT_S):
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    return False
                line = raw.decode("utf-8", errors="replace")
                if "Server is ready" in line:
                    return True
    except TimeoutError:
        logger.warning("nats_sidecar: timeout esperando o servidor ficar pronto")
        return False


def _pid_is_alive_safe(pid: int) -> bool:
    """`_pid_is_alive` que nunca levanta — uma checagem de PID que estoura não
    pode impedir o sidecar de subir."""
    try:
        return _pid_is_alive(pid)
    except Exception:
        logger.warning(
            "nats_sidecar: falha ao verificar pid %s — tratando como morto",
            pid,
            exc_info=True,
        )
        return False


def _pid_file_path(store_dir: Path) -> Path:
    return store_dir / _PID_FILE_NAME


def _write_pid_list(store_dir: Path, pids: list[int], url: str = "") -> None:
    """Grava PIDs e a URL do sidecar ativo.

    A URL é o que permite a um processo seguinte **reusar** o sidecar em vez
    de subir outro sobre o mesmo store — dois `nats-server` no mesmo diretório
    de JetStream derrubam um ao outro.
    """
    payload: dict[str, Any] = {"pids": pids[-_PID_LIST_CAP:]}
    if url:
        payload["url"] = url
    with contextlib.suppress(Exception):
        _pid_file_path(store_dir).write_text(json.dumps(payload), encoding="utf-8")


def _read_registered_url(store_dir: Path) -> str:
    """URL do sidecar registrado, ou string vazia (pid file legado/ausente)."""
    path = _pid_file_path(store_dir)
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data.get("url", ""))
    except Exception:
        return ""


def _responds(url: str, timeout_s: float = 1.0) -> bool:
    """True se algo aceita conexão TCP na porta do `url`.

    Distingue sidecar vivo e utilizável de processo zumbi: PID vivo sozinho
    não basta, porque um `nats-server` travado continua no `tasklist` sem
    aceitar conexão.
    """
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.hostname or not parsed.port:
        return False
    try:
        with socket.create_connection(
            (parsed.hostname, parsed.port), timeout=timeout_s
        ):
            return True
    except OSError:
        return False


def _read_stale_pids(store_dir: Path) -> list[int]:
    """Lê os PIDs de sidecars registrados por sessões anteriores.

    Aceita o formato legado ``{"pid": N}`` (D3) além do formato atual
    ``{"pids": [N, ...]}`` — sem isso, um pid file antigo já em disco no
    momento do upgrade seria silenciosamente ignorado."""
    path = _pid_file_path(store_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "pids" in data:
            return [int(p) for p in data["pids"]]
        return [int(data["pid"])]
    except Exception:
        return []


def _clear_pid_file(store_dir: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        _pid_file_path(store_dir).unlink()


def _remove_pid(store_dir: Path, pid: int) -> None:
    """Remove um PID específico da lista registrada (usado no shutdown
    gracioso do processo atual) — preserva quaisquer outros PIDs órfãos
    ainda não resolvidos, em vez de apagar o arquivo inteiro."""
    remaining = [p for p in _read_stale_pids(store_dir) if p != pid]
    if remaining:
        _write_pid_list(store_dir, remaining)
    else:
        _clear_pid_file(store_dir)


def _pid_is_alive_posix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # existe, só não temos permissão de sinalizar
    except OSError:
        return False
    return True


def _pid_is_alive_win32(pid: int) -> bool:
    tasklist = shutil.which("tasklist")
    if tasklist is None:
        return False
    try:
        out = subprocess.run(  # noqa: S603  # nosec B603
            [tasklist, "/NH", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    return str(pid) in out.stdout


def kill_orphan_pid(pid: int) -> None:
    """Wrapper público de `_kill_pid` para uso fora deste módulo (ex.:
    `vectora doctor`, `backend/cli/doctor.py`) — evita duplicar a lógica
    de `taskkill`/`SIGKILL` por plataforma em outro lugar."""
    _kill_pid(pid)


def _pid_is_alive(pid: int) -> bool:
    """Checa se um PID de uma sessão anterior ainda está vivo — cross-processo,
    não usa nenhum handle do asyncio (o `Process` só existe pra quem spawnou)."""
    if sys.platform == "win32":
        return _pid_is_alive_win32(pid)
    return _pid_is_alive_posix(pid)


def _kill_pid(pid: int) -> None:
    """Mata um sidecar órfão de sessão anterior — best-effort, nunca lança."""
    if sys.platform == "win32":
        taskkill = shutil.which("taskkill")
        if taskkill is None:
            return
        with contextlib.suppress(Exception):
            subprocess.run(  # noqa: S603  # nosec B603
                [taskkill, "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
                check=False,
            )
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


def with_kill(proc: asyncio.subprocess.Process) -> None:
    """Mata um processo que falhou o handshake de prontidão — best-effort."""
    with contextlib.suppress(Exception):
        proc.kill()


async def stop_nats_sidecar() -> None:
    """Encerra o sidecar, se estiver rodando. Idempotente."""
    global _proc, _url, _log_task

    # Solta a referência ao lock também quando não há processo rodando —
    # entre testes (cada um com seu próprio event loop via pytest-asyncio),
    # sem isso o lock ficaria preso ao loop do teste anterior e a próxima
    # `_spawn_lock.get()` levantaria "Lock is bound to a different event loop".
    _spawn_lock.reset()

    if _log_task is not None:
        _log_task.cancel()
        _log_task = None

    store_dir = settings.vectora_home / "nats"
    if _proc is not None:
        _remove_pid(store_dir, _proc.pid)
    else:
        _clear_pid_file(store_dir)

    if _proc is None:
        return
    proc = _proc
    _proc = None
    _url = None
    await terminate_gracefully(
        proc, timeout_seconds=5.0, logger=logger, log_prefix="nats_sidecar"
    )


def current_url() -> str | None:
    """URL do sidecar já ativo, sem tentar subir um novo — usado por get_mq/get_kv."""
    return _url if _proc is not None and _proc.returncode is None else None
