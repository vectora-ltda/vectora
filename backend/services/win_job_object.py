"""Windows Job Object — defesa em profundidade contra processo filho órfão.

Um handler de sinal (`backend/main.py::_install_terminal_signals`) só roda
em shutdown GRACIOSO — nunca em `SIGKILL`/"Finalizar tarefa" no Task
Manager, que não dá nenhuma chance de código Python rodar antes do
processo morrer. Nesses casos, a única garantia real (a nível de kernel,
não de aplicação) de que um processo filho (ex.: `nats-server.exe`) morre
junto do processo Python é uma Job Object do Windows com
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: o SO mata automaticamente todo
processo associado à Job assim que o handle dela é fechado — o que
acontece de qualquer forma quando o processo dono termina, por qualquer
motivo, gracioso ou não.

`ctypes` puro contra `kernel32.dll`, não `pywin32` — o projeto não tem essa
dependência hoje e adicioná-la só por 3 chamadas de API bem conhecidas
traria DLLs binárias por plataforma, complicando o bundle Nuitka/
PyInstaller (que já resolve paths de bundle manualmente, ver
`nats_sidecar.py::_frozen_bundle_bases`).

Limitação honesta: não há equivalente universal em POSIX (Linux tem
`prctl(PR_SET_PDEATHSIG)` via `ctypes`/`libc`, macOS não tem nada
parecido) — Linux/macOS seguem dependendo só do shutdown gracioso via
sinal para esse cenário, sem a mesma garantia de kernel que o Windows tem
aqui. Todo esse módulo é Windows-only; chamadores devem checar
`sys.platform == "win32"` antes de importar.
"""

from __future__ import annotations

import ctypes
import logging
from typing import Any

logger = logging.getLogger(__name__)

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32() -> Any:
    """`ctypes.windll.kernel32` com `argtypes`/`restype` explícitos nas
    funções usadas aqui — sem isso, ctypes assume `c_int` (32 bits) pra
    todo handle, o que trunca/corrompe silenciosamente valores de HANDLE
    em Windows x64 (o handle "parece" funcionar em `CreateJobObjectW` mas
    quebra ao ser repassado pra `AssignProcessToJobObject`/
    `SetInformationJobObject`, e `KILL_ON_JOB_CLOSE` nunca dispara de
    verdade — bug real encontrado na verificação ao vivo desta correção).
    `ctypes.windll` já cacheia a mesma instância de `WinDLL` entre
    chamadas, então redefinir os tipos aqui é idempotente e barato.

    Acesso via `getattr` (não `ctypes.windll` direto): `windll`/`WinDLL`
    só existem no stub de tipos Windows — `ty` roda tanto localmente
    (Windows) quanto no CI (Linux), e um `# ty: ignore` fixo vira erro
    "unused" numa das duas plataformas. `getattr` devolve `Any`,
    resolvível nas duas sem suprimir nada; o módulo inteiro só é
    importado sob `sys.platform == "win32"` em runtime.
    """
    from ctypes import wintypes

    kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def create_job_object() -> int | None:
    """Cria uma Job Object com `KILL_ON_JOB_CLOSE` habilitado.

    O handle retornado deve viver pelo tempo de vida do processo Python
    inteiro — nunca fechado explicitamente enquanto sidecars associados
    ainda devem rodar (fechar cedo demais mata tudo associado na hora, o
    que é o comportamento desejado só na morte do processo dono, não num
    shutdown gracioso intencional de um sidecar específico). Retorna
    `None` em qualquer falha — best-effort, nunca deve impedir o sidecar
    de subir.
    """
    try:
        kernel32 = _kernel32()
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _JobObjectExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(handle)
            return None
        return handle
    except Exception:
        logger.warning("win_job_object: falha ao criar Job Object", exc_info=True)
        return None


def assign_process_to_job(job_handle: int, pid: int) -> bool:
    """Associa um processo (por PID) à Job Object — a partir daqui, o SO
    mata esse processo automaticamente quando o handle da Job for fechado
    (o que acontece na morte do processo Python dono, por qualquer
    motivo). Best-effort: `False` em qualquer falha, nunca lança.
    """
    try:
        kernel32 = _kernel32()
        process_handle = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid
        )
        if not process_handle:
            return False
        try:
            return bool(kernel32.AssignProcessToJobObject(job_handle, process_handle))
        finally:
            kernel32.CloseHandle(process_handle)
    except Exception:
        logger.warning(
            "win_job_object: falha ao associar pid %s à Job Object",
            pid,
            exc_info=True,
        )
        return False
