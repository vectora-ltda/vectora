"""Worker jailado persistente por workspace — ponto de integração único do
Sandbox para as tools que tocam filesystem/shell (`terminal`, `file_write`,
`edit_file`, PTY interativo).

Nasce sob demanda na 1ª ação sandboxável de uma workspace com `[sandbox]`
habilitado e fica vivo enquanto a workspace tiver sessão ativa — nunca um
``bwrap`` novo por chamada. Reflete o mesmo princípio do ``ai-jail``
original (uma sessão de trabalho, um processo jailado), adaptado à
arquitetura de processo único e multi-workspace do Vectora.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from typing import Any

from backend.sandbox.dry_run import build_bwrap_command, python_runtime_paths
from backend.sandbox.linux import build_seccomp_filter
from backend.sandbox.policy import SandboxPolicy, detect_wsl2
from backend.sandbox.windows import (
    assign_job_object_kill_on_close,
    build_soft_env,
    close_job_handle,
)

logger = logging.getLogger(__name__)

DEFAULT_IDLE_TIMEOUT_S = 600.0

#: Teto de espera por resposta de uma requisição ao worker. Um worker pode
#: travar sem morrer (loop infinito dentro do jail, syscall bloqueada); sem
#: teto, `readline()` espera pra sempre e o workspace inteiro fica
#: inacessível — não há outro caminho de recuperação porque o worker é
#: singleton por workspace.
DEFAULT_REQUEST_TIMEOUT_S = 120.0


def _is_windows() -> bool:
    """Indireção sobre `sys.platform` — testável isoladamente sem
    monkeypatchar o módulo `sys` global (afeta pytest-asyncio/outras libs
    que também leem `sys.platform` no mesmo processo)."""
    return sys.platform == "win32"


def _windows_path_to_wsl(path: str) -> str:
    """`C:\\Users\\a\\b` -> `/mnt/c/Users/a/b` — path do workspace visto de
    dentro da distro WSL2 (todo drive Windows é montado em `/mnt/<letra>`)."""
    pure = PureWindowsPath(path)
    drive = pure.drive.rstrip(":").lower()
    rest = "/".join(pure.parts[1:])
    return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"


async def _bwrap_available_in_distro(distro: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "wsl.exe",
            "-d",
            distro,
            "--",
            "which",
            "bwrap",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10.0)
    except (FileNotFoundError, TimeoutError, OSError):
        return False
    return proc.returncode == 0


class WorkerSpawnError(RuntimeError):
    """bwrap ausente/sem permissão — nunca cai silenciosamente pra execução
    sem sandbox."""


@dataclass
class JailedWorker:
    workspace_id: str
    proc: asyncio.subprocess.Process
    last_used: float = field(default_factory=time.monotonic)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _next_id: int = field(default=0, init=False)
    #: Handle do Job Object do Windows no modo "soft" (fallback sem bwrap) —
    #: fechado em `close()`/`_kill_now()` pra derrubar a árvore de processos.
    job_handle: int | None = None
    isolation_enforced: bool = False
    #: O filtro seccomp foi carregado no bwrap (libseccomp disponível).
    seccomp_applied: bool = False
    #: Pasta e política com que o worker nasceu: mudou qualquer uma, o worker
    #: antigo não serve mais (escreveria na pasta errada ou com a regra velha).
    workspace_dir: str = ""
    policy: SandboxPolicy | None = None

    async def request(
        self, op: str, timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S, **kwargs: Any
    ) -> dict[str, Any]:
        """Estourar `timeout_s` mata o worker com SIGKILL antes de propagar:
        depois de um timeout o protocolo de linhas está dessincronizado (a
        resposta atrasada viraria a resposta da *próxima* requisição), então
        o worker é descartado e `get_or_spawn` sobe um novo."""
        async with self._lock:
            self.last_used = time.monotonic()
            self._next_id += 1
            req_id = self._next_id
            payload = {"op": op, "id": req_id, **kwargs}
            if self.proc.stdin is None or self.proc.stdout is None:
                raise RuntimeError("worker jailado sem stdin/stdout conectados")
            self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            await self.proc.stdin.drain()
            try:
                line = await asyncio.wait_for(
                    self.proc.stdout.readline(), timeout=timeout_s
                )
            except TimeoutError:
                await self._kill_now()
                raise RuntimeError(
                    f"worker jailado não respondeu em {timeout_s}s — processo "
                    "finalizado, próxima chamada sobe um worker novo"
                ) from None
            if not line:
                raise RuntimeError("worker jailado encerrou inesperadamente")
            return json.loads(line)

    async def _kill_now(self) -> None:
        """SIGKILL direto (não `terminate`): o worker está travado, então um
        sinal que ele precisaria tratar não seria atendido."""
        if self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                self.proc.kill()
            with contextlib.suppress(Exception):
                await self.proc.wait()
        close_job_handle(self.job_handle)
        self.job_handle = None

    async def isolation_status(self) -> dict[str, bool]:
        """O que está de fato em vigor no jail: seccomp (carregado pelo bwrap) e
        Landlock, `no_new_privs` e rlimits (aplicados pelo worker). Falha em
        perguntar conta como nada aplicado, nunca como completo."""
        try:
            resp = await self.request("status", timeout_s=15.0)
        except Exception:
            resp = {}
        return {
            "seccomp": self.seccomp_applied,
            "landlock": bool(resp.get("landlock")),
            "no_new_privs": bool(resp.get("no_new_privs")),
            "rlimits": bool(resp.get("rlimits")),
        }

    def is_alive(self) -> bool:
        return self.proc.returncode is None

    async def close(self) -> None:
        if self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5.0)
            except TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        close_job_handle(self.job_handle)
        self.job_handle = None


class WorkspaceJailManager:
    """Singleton do processo backend — um worker por ``workspace_id``."""

    def __init__(self, idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S) -> None:
        self._workers: dict[str, JailedWorker] = {}
        self._isolation_required: set[str] = set()
        self._spawn_lock = asyncio.Lock()
        self._idle_timeout_s = idle_timeout_s

    async def get_or_spawn(
        self, workspace_id: str, workspace_dir: str, policy: SandboxPolicy
    ) -> JailedWorker:
        if not policy.enabled:
            raise WorkerSpawnError("sandbox não habilitado para esta workspace")
        async with self._spawn_lock:
            existing = self._workers.get(workspace_id)
            if existing is not None and existing.is_alive():
                if (
                    existing.workspace_dir == workspace_dir
                    and existing.policy == policy
                ):
                    return existing
                await existing.close()
                self._workers.pop(workspace_id, None)
            worker = await self._spawn(workspace_id, workspace_dir, policy)
            if (
                workspace_id in self._isolation_required
                and not worker.isolation_enforced
            ):
                await worker.close()
                raise WorkerSpawnError(
                    "Native isolation was required by HITL; refusing a soft replacement worker."
                )
            self._workers[workspace_id] = worker
            return worker

    async def _spawn(
        self, workspace_id: str, workspace_dir: str, policy: SandboxPolicy
    ) -> JailedWorker:
        soft_env: dict[str, str] | None = None
        cwd: str | None = None

        if _is_windows():
            try:
                argv = await self._build_wsl2_argv(workspace_dir, policy)
            except WorkerSpawnError as exc:
                # Sem WSL2 elegível ou sem bwrap dentro da distro: em vez de
                # bloquear edição/execução, degrada pro sandbox "soft" nativo
                # do Windows (menos isolamento, melhor que nada).
                logger.warning(
                    "sandbox: %s — degradando pro sandbox soft nativo do "
                    "Windows (menos isolamento, mas não bloqueia edição/execução)",
                    exc,
                )
                argv = [sys.executable, "-m", "backend.sandbox.worker"]
                cwd = workspace_dir
                soft_env = build_soft_env(self._soft_worker_env(policy))
        else:
            argv = build_bwrap_command(
                policy,
                workspace_dir,
                [sys.executable, "-m", "backend.sandbox.worker"],
                runtime_ro_paths=python_runtime_paths(),
            )

        # seccomp (libseccomp) só faz sentido no caminho bwrap (Linux/WSL2);
        # o sandbox soft do Windows não tem libseccomp nem syscall equivalente.
        seccomp_fd: int | None = None
        seccomp_applied = False
        if soft_env is None and not _is_windows():
            try:
                bpf = build_seccomp_filter()
            except Exception:
                logger.warning("sandbox: falha ao compilar filtro seccomp — ignorando")
                bpf = None
            if bpf is None and policy.require_full_isolation:
                raise WorkerSpawnError(
                    "isolamento completo exigido, mas o filtro seccomp não pôde "
                    "ser carregado (instale o libseccomp) — o jail não foi iniciado."
                )
            if bpf is not None:
                seccomp_applied = True
                read_fd, write_fd = os.pipe()
                os.write(write_fd, bpf)
                os.close(write_fd)
                seccomp_fd = read_fd
                argv = [argv[0], "--seccomp", str(read_fd), *argv[1:]]

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=soft_env,
                pass_fds=(seccomp_fd,) if seccomp_fd is not None else (),
            )
        except FileNotFoundError as exc:
            logger.warning("sandbox: binário bwrap não encontrado no sistema")
            raise WorkerSpawnError(
                "bwrap não está instalado neste sistema — sandbox indisponível."
            ) from exc
        except PermissionError as exc:
            logger.warning("sandbox: sem permissão para executar o binário bwrap")
            raise WorkerSpawnError(
                "sem permissão para executar bwrap — sandbox indisponível."
            ) from exc
        finally:
            # O filho já herdou sua própria cópia via pass_fds — fecha a
            # nossa pra não vazar o fd no processo do backend.
            if seccomp_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(seccomp_fd)

        job_handle: int | None = None
        if soft_env is not None and proc.pid:
            job_handle = assign_job_object_kill_on_close(proc.pid)
        worker = JailedWorker(
            workspace_id=workspace_id,
            proc=proc,
            job_handle=job_handle,
            isolation_enforced=soft_env is None and policy.backend == "local",
            seccomp_applied=seccomp_applied,
            workspace_dir=workspace_dir,
            policy=policy,
        )
        if policy.require_full_isolation and soft_env is None:
            status = await worker.isolation_status()
            missing = [
                name for name in ("landlock", "no_new_privs") if not status.get(name)
            ]
            if missing:
                await worker.close()
                raise WorkerSpawnError(
                    "isolamento completo exigido, mas o worker não aplicou: "
                    + ", ".join(missing)
                    + " — o jail foi encerrado."
                )
        return worker

    @staticmethod
    def _soft_worker_env(policy: SandboxPolicy) -> dict[str, str]:
        """Env do worker "soft" do Windows — sinaliza o modo soft e propaga o
        perfil de lockdown (rlimits é no-op no Windows, mas mantém o contrato)."""
        return {
            "VECTORA_SANDBOX_SOFT": "1",
            "VECTORA_SANDBOX_LOCKDOWN": "1" if policy.lockdown else "0",
        }

    async def _build_wsl2_argv(
        self, workspace_dir: str, policy: SandboxPolicy
    ) -> list[str]:
        """bwrap não roda nativo no Windows (sem namespace/mount API
        equivalente) — WSL2 é o caminho real que o `ai-jail` original usa
        nesse SO, não Docker. Roteia o worker inteiro (bwrap + python3 +
        `backend.sandbox.worker`) pra dentro da distro via `wsl.exe -d
        <distro> --`; o path do workspace é traduzido de `C:\\...` pra
        `/mnt/c/...` (todo drive Windows já vem montado assim no WSL2).
        `rw_paths`/`ro_paths` da política, por outro lado, já devem estar
        em formato Linux — são paths dentro do sandbox, não do host."""
        distro = await detect_wsl2()
        if distro is None:
            raise WorkerSpawnError(
                "bwrap não roda nativo no Windows. Instale o WSL2 (`wsl --install`, "
                "reinicie e crie uma distro Linux) para habilitar o sandbox — "
                "Docker não é o caminho certo pra isso."
            )
        if not await _bwrap_available_in_distro(distro):
            raise WorkerSpawnError(
                f"WSL2 (distro '{distro}') está disponível, mas o bwrap não está "
                f"instalado dentro dela. Rode `wsl -d {distro} -- sudo apt install "
                "bubblewrap` (ou o gerenciador de pacotes da sua distro) e tente de novo."
            )
        wsl_workspace_dir = _windows_path_to_wsl(workspace_dir)
        bwrap_argv = build_bwrap_command(
            policy, wsl_workspace_dir, ["python3", "-m", "backend.sandbox.worker"]
        )
        return ["wsl.exe", "-d", distro, "--", *bwrap_argv]

    def has_effective_isolation(self, workspace_id: str) -> bool:
        """Only a live worker started under native isolation can relax HITL."""
        worker = self._workers.get(workspace_id)
        isolated = bool(worker and worker.is_alive() and worker.isolation_enforced)
        if isolated:
            # Once HITL relies on native isolation, expiry/restart cannot silently
            # replace the worker with a soft process before the tool dispatch.
            self._isolation_required.add(workspace_id)
        return isolated

    async def close(self, workspace_id: str) -> None:
        worker = self._workers.pop(workspace_id, None)
        if worker is not None:
            await worker.close()

    async def close_idle(self) -> None:
        now = time.monotonic()
        stale = [
            wid
            for wid, w in self._workers.items()
            if now - w.last_used > self._idle_timeout_s
        ]
        for wid in stale:
            await self.close(wid)


jail_manager = WorkspaceJailManager()
