"""Tentativas de escape: o que o jail real recusa, e a regressão do docker.sock.

Cada teste roda o comando no worker jailado de verdade (bwrap, seccomp, Landlock),
do ponto de vista do agente. O que é negado por syscall morre por SIGSYS.
"""

from __future__ import annotations

import platform
import shlex
import sys

import pytest

from backend.sandbox import dry_run, workspace_jail
from backend.sandbox.egress_proxy import egress_registry
from backend.sandbox.policy import SandboxPolicy
from tests.unit.test_sandbox_hardening import _bwrap_works, _exec

real_bwrap = pytest.mark.skipif(
    not _bwrap_works(), reason="bwrap sem user namespaces neste ambiente"
)

#: Mortos por SIGSYS: 31 pelo sinal (`-31`) ou 128+31 pelo shell.
_KILLED_BY_SIGSYS = {159, 31}

_ARCH = platform.machine()
_SYSCALL_NUMBERS = {
    "x86_64": {"io_uring_setup": 425, "keyctl": 250, "userfaultfd": 323},
    "aarch64": {"io_uring_setup": 425, "keyctl": 219, "userfaultfd": 282},
}


def _py(code: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


async def _no_jail(tmp_path, *comandos: str, policy: SandboxPolicy | None = None):
    manager = workspace_jail.WorkspaceJailManager()
    try:
        worker = await manager.get_or_spawn(
            "ws-esc", str(tmp_path), policy or SandboxPolicy(enabled=True)
        )
        return [await _exec(worker, comando) for comando in comandos]
    finally:
        await manager.close("ws-esc")
        await egress_registry.close_all()


@real_bwrap
@pytest.mark.asyncio
async def test_syscalls_perigosas_matam_o_processo(tmp_path):
    pytest.importorskip("pyseccomp")
    if _ARCH not in _SYSCALL_NUMBERS:
        pytest.skip(f"números de syscall não mapeados para {_ARCH}")
    numeros = _SYSCALL_NUMBERS[_ARCH]
    tentativas = {
        "ptrace": "import ctypes; ctypes.CDLL(None).ptrace(0, 0, 0, 0)",
        "unshare": "import os; os.unshare(os.CLONE_NEWUSER)",
        "mount": "import ctypes; ctypes.CDLL(None).mount(b'x', b'/tmp', b'tmpfs', 0, 0)",
        "io_uring": f"import ctypes; ctypes.CDLL(None).syscall({numeros['io_uring_setup']}, 8, 0)",
        "keyctl": f"import ctypes; ctypes.CDLL(None).syscall({numeros['keyctl']}, 0, 0)",
        "userfaultfd": f"import ctypes; ctypes.CDLL(None).syscall({numeros['userfaultfd']}, 0)",
    }

    resultados = await _no_jail(tmp_path, *(_py(c) for c in tentativas.values()))

    for nome, resultado in zip(tentativas, resultados, strict=True):
        assert resultado["exit_code"] in _KILLED_BY_SIGSYS, (nome, resultado)


@real_bwrap
@pytest.mark.asyncio
async def test_clone_que_cria_namespace_e_morto_e_thread_comum_segue(tmp_path):
    pytest.importorskip("pyseccomp")
    comum = _py(
        "import threading; t = threading.Thread(target=lambda: None); t.start(); t.join(); print('ok')"
    )
    # `unshare(1)` e `clone(CLONE_NEWNS)` pela libc: a flag de namespace é negada.
    clone = _py(
        "import ctypes; libc = ctypes.CDLL(None); "
        "libc.syscall(56, 0x00020000, 0, 0, 0, 0)"  # clone(CLONE_NEWNS) em x86_64
    )
    if _ARCH != "x86_64":
        pytest.skip("o número do clone é o de x86_64")

    ok, morto = await _no_jail(tmp_path, comum, clone)

    assert ok["exit_code"] == 0 and ok["stdout"].strip() == "ok"
    assert morto["exit_code"] in _KILLED_BY_SIGSYS


@real_bwrap
@pytest.mark.asyncio
async def test_tiocsti_e_negado_com_eperm(tmp_path):
    pytest.importorskip("pyseccomp")
    (resultado,) = await _no_jail(
        tmp_path,
        _py(
            "import fcntl, errno\n"
            "try:\n"
            "    fcntl.ioctl(0, 0x5412, b'x')\n"
            "except OSError as erro:\n"
            "    print(erro.errno == errno.EPERM)\n"
        ),
    )

    assert resultado["stdout"].strip() == "True"


@real_bwrap
@pytest.mark.asyncio
async def test_sem_privilegios_e_sem_novas_capacidades(tmp_path):
    cap, nnp = await _no_jail(
        tmp_path,
        "grep CapEff /proc/self/status",
        "grep NoNewPrivs /proc/self/status",
    )

    assert cap["stdout"].split()[-1] == "0" * 16
    assert nnp["stdout"].split()[-1] == "1"


@real_bwrap
@pytest.mark.asyncio
async def test_sistema_e_somente_leitura_e_o_home_real_nao_existe(tmp_path):
    escrever_usr, escrever_etc, home = await _no_jail(
        tmp_path,
        "touch /usr/arquivo-do-agente",
        "touch /etc/arquivo-do-agente",
        "ls /root /home 2>&1 | head -3",
    )

    assert escrever_usr["exit_code"] != 0
    assert escrever_etc["exit_code"] != 0
    assert "arquivo-do-agente" not in home["stdout"]


@real_bwrap
@pytest.mark.asyncio
async def test_nao_ha_docker_sock_no_jail(tmp_path):
    existe, run = await _no_jail(
        tmp_path,
        "test -e /var/run/docker.sock",
        "ls /run /var/run 2>&1",
    )

    assert existe["exit_code"] != 0
    assert "docker.sock" not in run["stdout"]


def test_comando_do_bwrap_nunca_monta_o_docker_sock(tmp_path):
    """Regressão: um socket montado entrega o host (o daemon roda como root e
    obedece a API), anulando todo o resto do isolamento."""
    politicas = [
        SandboxPolicy(enabled=True),
        SandboxPolicy(enabled=True, network="open"),
        SandboxPolicy(enabled=True, lockdown=True),
        SandboxPolicy(enabled=True, rw_paths=(str(tmp_path),)),
    ]
    for politica in politicas:
        argv = dry_run.build_bwrap_command(politica, str(tmp_path), ["true"])
        assert not any("docker.sock" in parte for parte in argv)
        assert "/var/run" not in argv
        assert "/run/user" not in " ".join(argv)


@real_bwrap
@pytest.mark.asyncio
async def test_processo_do_jail_nao_enxerga_os_do_host(tmp_path):
    (resultado,) = await _no_jail(tmp_path, "ls /proc | grep -c '^[0-9]'")

    assert int(resultado["stdout"].strip()) < 20  # só os processos do próprio jail


@real_bwrap
@pytest.mark.asyncio
async def test_segredos_do_backend_e_chaves_ssh_nao_aparecem(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-segredo")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh-agent-do-host")

    env, sock = await _no_jail(tmp_path, "env", "test -e /tmp/ssh-agent-do-host")

    assert "sk-ant-segredo" not in env["stdout"]
    assert "SSH_AUTH_SOCK" not in env["stdout"]
    assert sock["exit_code"] != 0
