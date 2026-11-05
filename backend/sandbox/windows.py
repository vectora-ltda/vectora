"""Sandbox "soft" nativo do Windows — fallback quando `bwrap`/WSL2 não estão
disponíveis.

Menos seguro que bubblewrap (o Windows não tem namespace/mount API
equivalente nativa), mas melhor que nada: limpa o ambiente (segredos não
vazam pro subprocesso), confina o cwd ao workspace e, quando possível,
envolve o processo num Job Object do Windows (kill-on-close, sem processo
órfão sobrevivente). O isolamento real de filesystem/rede continua sendo o
WSL2+bwrap — este módulo é só a rede de segurança do Windows sem WSL2+bwrap,
para que a edição/execução de arquivos nunca fique bloqueada por um binário
ausente.

Nunca usado em Linux/macOS — nessas plataformas `bwrap`/`sandbox-exec` são o
caminho real (ver ``backend/sandbox/linux.py`` e ``macos.py``).
"""

from __future__ import annotations

import ctypes
import logging
import os

logger = logging.getLogger(__name__)

#: Variáveis de ambiente consideradas seguras/essenciais pra um subprocesso
#: Python (e seus filhos) rodarem no Windows. Tudo o mais — API keys, tokens,
#: segredos do Vectora — é descartado na hora de montar o ambiente "soft".
_MINIMAL_ENV_KEYS: frozenset[str] = frozenset(
    {
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "TEMP",
        "TMP",
        "PATH",
        "PATHEXT",
        "COMSPEC",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMDATA",
        "ALLUSERSPROFILE",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "OS",
        "COMPUTERNAME",
        "USERNAME",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
    }
)

_VECTORA_SANDBOX_PREFIX = "VECTORA_SANDBOX_"

#: `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` — quando o handle do job é fechado,
#: o SO mata todos os processos associados (filhos órfãos não sobrevivem).
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
#: `JobObjectBasicLimitInformation` (classe 2 de SetInformationJobObject).
_JOB_OBJECT_BASIC_LIMIT_INFORMATION = 2


def build_soft_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Monta o ambiente mínimo do worker "soft": só as variáveis essenciais
    do Windows + as que a política de sandbox injeta (`VECTORA_SANDBOX_*`).
    Qualquer segredo do processo pai fica de fora.

    Args:
        extra: Variáveis adicionais a incluir (ex.: flags da política).

    Returns:
        Dicionário de ambiente enxuto, pronto pra ``env=`` no subprocess.
    """
    env: dict[str, str] = {
        key: value
        for key, value in os.environ.items()
        if key in _MINIMAL_ENV_KEYS or key.startswith(_VECTORA_SANDBOX_PREFIX)
    }
    if extra:
        env.update(extra)
    return env


def assign_job_object_kill_on_close(pid: int) -> int | None:
    """Cria um Job Object do Windows com `KILL_ON_JOB_CLOSE` e associa `pid`.

    Retorna o handle do job (o caller DEVE mantê-lo aberto e fechá-lo quando
    quiser derrubar a árvore de processos) ou `None` em qualquer falha — o
    isolamento de processo nunca é pré-condição pra execução, então degrada
    silenciosamente em vez de quebrar o spawn.

    Args:
        pid: PID do processo (já spawnado) a associar ao job.

    Returns:
        Handle (int) do Job Object, ou None se indisponível/falhar.
    """
    try:
        kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009
    except (AttributeError, OSError):
        logger.debug("sandbox: kernel32 indisponível — sem Job Object")
        return None

    class _BasicLimitInformation(ctypes.Structure):
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

    try:
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        info = _BasicLimitInformation()
        info.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_BASIC_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )

        # PROCESS_SET_QUOTA | PROCESS_TERMINATE — necessários pra atribuir o
        # processo ao job (SetQuota) e matá-lo no close (Terminate).
        process_access = 0x0100 | 0x0001
        hproc = kernel32.OpenProcess(process_access, False, pid)
        if not hproc:
            kernel32.CloseHandle(job)
            return None
        try:
            kernel32.AssignProcessToJobObject(job, hproc)
        finally:
            kernel32.CloseHandle(hproc)
        return int(job)
    except Exception:  # pragma: no cover — melhor esforço, nunca fatal
        logger.debug("sandbox: falha ao criar/atribuir Job Object — sem kill-on-close")
        return None


def close_job_handle(handle: int | None) -> None:
    """Fecha o handle do Job Object, disparando o `KILL_ON_JOB_CLOSE` (mata a
    árvore associada). Best-effort: nunca levanta."""
    if handle is None:
        return
    try:
        kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009
        kernel32.CloseHandle(handle)
    except Exception:  # pragma: no cover
        logger.debug("sandbox: falha ao fechar handle do Job Object")
