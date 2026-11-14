"""Sandbox — ambiente mínimo, sessão nova, proteção de `.git` e isolamento exigido."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.sandbox import dry_run, workspace_jail
from backend.sandbox.dry_run import (
    CLONE_NAMESPACE_FLAGS,
    DENIED_SYSCALLS,
    TIOCSTI,
    build_bwrap_command,
    git_protection_mounts,
    jail_environment,
    python_runtime_paths,
    system_mounts,
)
from backend.sandbox.policy import SandboxPolicy, parse_policy


def _setenv_pairs(argv: list[str]) -> dict[str, str]:
    return {
        argv[i + 1]: argv[i + 2] for i, item in enumerate(argv) if item == "--setenv"
    }


# ── ambiente limpo ──────────────────────────────────────────────────────────


def test_jail_usa_clearenv_e_sessao_nova():
    argv = build_bwrap_command(SandboxPolicy(enabled=True), "/ws", ["true"])

    assert "--clearenv" in argv
    assert "--new-session" in argv
    # `--clearenv` precisa vir antes de qualquer `--setenv`, senão apagaria o ambiente montado.
    assert argv.index("--clearenv") < argv.index("--setenv")


def test_segredos_do_backend_nao_entram_no_ambiente_do_jail(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-segredo")
    monkeypatch.setenv("VECTORA_TOKEN", "tok-segredo")
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")

    argv = build_bwrap_command(SandboxPolicy(enabled=True), "/ws", ["true"])
    env = _setenv_pairs(argv)

    assert "ANTHROPIC_API_KEY" not in env
    assert "VECTORA_TOKEN" not in env
    assert "sk-segredo" not in argv
    assert "tok-segredo" not in argv
    assert env["LANG"] == "pt_BR.UTF-8"
    assert env["HOME"] == dry_run.WORKER_HOME


def test_env_pass_libera_so_o_que_o_usuario_declarou(monkeypatch):
    monkeypatch.setenv("NPM_TOKEN", "npm-ok")
    monkeypatch.setenv("OUTRO_SEGREDO", "nao")

    env = jail_environment(SandboxPolicy(enabled=True, env_pass=("NPM_TOKEN",)))

    assert env["NPM_TOKEN"] == "npm-ok"
    assert "OUTRO_SEGREDO" not in env


def test_env_pass_de_variavel_ausente_nao_cria_entrada(monkeypatch):
    monkeypatch.delenv("NAO_EXISTE", raising=False)

    env = jail_environment(SandboxPolicy(enabled=True, env_pass=("NAO_EXISTE",)))

    assert "NAO_EXISTE" not in env


# ── sistema e runtime ───────────────────────────────────────────────────────


def test_links_do_sistema_sao_recriados_como_links(monkeypatch, tmp_path):
    """`/lib64 -> usr/lib64`: montar o link como pasta esconde o carregador."""
    real = tmp_path / "usr"
    real.mkdir()
    link = tmp_path / "lib64"
    link.symlink_to("usr/lib64")
    monkeypatch.setattr(dry_run, "SYSTEM_PATHS", (str(real), str(link)))
    monkeypatch.setattr(dry_run, "_resolver_run_dir", lambda: None)

    argv = system_mounts()

    assert ["--ro-bind", str(real), str(real)] == argv[:3]
    assert ["--symlink", "usr/lib64", str(link)] == argv[3:6]


def test_pasta_de_sistema_inexistente_e_ignorada(monkeypatch):
    monkeypatch.setattr(dry_run, "SYSTEM_PATHS", ("/nao/existe/de/jeito/nenhum",))
    monkeypatch.setattr(dry_run, "_resolver_run_dir", lambda: None)

    assert system_mounts() == []


def test_runtime_do_python_nao_expoe_o_repositorio_inteiro():
    paths = [p if isinstance(p, str) else p[0] for p in python_runtime_paths()]
    repo_root = str(Path(dry_run.__file__).resolve().parents[2])

    assert repo_root not in paths
    assert any(Path(p).name == "backend" for p in paths)


def test_runtime_so_entra_quando_pedido_e_vai_para_pythonpath():
    sem = build_bwrap_command(SandboxPolicy(enabled=True), "/ws", ["true"])
    com = build_bwrap_command(
        SandboxPolicy(enabled=True),
        "/ws",
        ["true"],
        runtime_ro_paths=python_runtime_paths(),
    )

    assert "PYTHONPATH" not in _setenv_pairs(sem)
    assert _setenv_pairs(com)["PYTHONPATH"]


def test_tmp_proprio_do_jail():
    argv = build_bwrap_command(SandboxPolicy(enabled=True), "/ws", ["true"])

    idx = argv.index("/tmp")
    assert argv[idx - 1] == "--tmpfs"


# ── .git ────────────────────────────────────────────────────────────────────


def test_git_hooks_e_config_ficam_somente_leitura(tmp_path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    (tmp_path / ".git" / "config").write_text("[core]\n")

    argv = git_protection_mounts(str(tmp_path))

    assert [
        "--ro-bind",
        str(tmp_path / ".git/hooks"),
        str(tmp_path / ".git/hooks"),
    ] == argv[:3]
    assert str(tmp_path / ".git/config") in argv


def test_sem_repositorio_nao_ha_protecao(tmp_path):
    assert git_protection_mounts(str(tmp_path)) == []


def test_git_writable_libera_a_escrita(tmp_path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)

    protegido = build_bwrap_command(
        SandboxPolicy(enabled=True), str(tmp_path), ["true"]
    )
    liberado = build_bwrap_command(
        SandboxPolicy(enabled=True, git_writable=True), str(tmp_path), ["true"]
    )

    hooks = str(tmp_path / ".git/hooks")
    assert hooks in protegido
    assert hooks not in liberado


def test_protecao_do_git_vem_depois_do_bind_do_workspace(tmp_path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)

    argv = build_bwrap_command(SandboxPolicy(enabled=True), str(tmp_path), ["true"])

    assert argv.index("--bind") < argv.index(str(tmp_path / ".git/hooks"))


# ── política ────────────────────────────────────────────────────────────────


def test_parse_policy_le_os_campos_novos(tmp_path):
    toml = tmp_path / "vectora.toml"
    toml.write_text(
        '[sandbox]\nenv_pass = ["NPM_TOKEN"]\ngit_writable = true\n'
        "require_full_isolation = true\n"
    )

    policy = parse_policy(toml)

    assert policy.env_pass == ("NPM_TOKEN",)
    assert policy.git_writable is True
    assert policy.require_full_isolation is True


def test_parse_policy_padroes_dos_campos_novos(tmp_path):
    toml = tmp_path / "vectora.toml"
    toml.write_text("[sandbox]\nenabled = true\n")

    policy = parse_policy(toml)

    assert policy.env_pass == ()
    assert policy.git_writable is False
    assert policy.require_full_isolation is False


# ── seccomp ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "io_uring_setup",
        "io_uring_enter",
        "userfaultfd",
        "keyctl",
        "setns",
        "perf_event_open",
    ],
)
def test_denylist_cobre_superficies_de_ataque(name):
    assert name in DENIED_SYSCALLS


def test_tiocsti_e_flags_de_namespace_estao_definidos():
    assert TIOCSTI == 0x5412
    assert 0x10000000 in CLONE_NAMESPACE_FLAGS  # CLONE_NEWUSER
    assert 0x40000000 in CLONE_NAMESPACE_FLAGS  # CLONE_NEWNET


# ── isolamento exigido ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_require_full_isolation_recusa_sem_seccomp(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_jail, "_is_windows", lambda: False)
    monkeypatch.setattr(workspace_jail, "build_seccomp_filter", lambda: None)
    policy = SandboxPolicy(enabled=True, require_full_isolation=True)
    manager = workspace_jail.WorkspaceJailManager()

    with pytest.raises(workspace_jail.WorkerSpawnError, match="seccomp"):
        await manager.get_or_spawn("ws", str(tmp_path), policy)


@pytest.mark.asyncio
async def test_sem_exigencia_o_jail_inicia_com_isolamento_parcial(
    tmp_path, monkeypatch
):
    """Sem libseccomp o jail segue (namespaces e Landlock valem) e o estado
    mostra `seccomp: False`, nunca "completo"."""
    if not _bwrap_works():
        pytest.skip("bwrap sem user namespaces neste ambiente")
    monkeypatch.setattr(workspace_jail, "build_seccomp_filter", lambda: None)
    manager = workspace_jail.WorkspaceJailManager()
    try:
        worker = await manager.get_or_spawn(
            "ws", str(tmp_path), SandboxPolicy(enabled=True)
        )
        status = await worker.isolation_status()
    finally:
        await manager.close("ws")

    assert status["seccomp"] is False
    assert status["landlock"] is True


# ── execução real no bwrap ──────────────────────────────────────────────────


def _bwrap_works() -> bool:
    if shutil.which("bwrap") is None or sys.platform != "linux":
        return False
    try:
        run = subprocess.run(  # noqa: S603
            [
                shutil.which("bwrap") or "bwrap",
                "--ro-bind",
                "/",
                "/",
                "--unshare-pid",
                "true",
            ],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return run.returncode == 0


real_bwrap = pytest.mark.skipif(
    not _bwrap_works(), reason="bwrap sem user namespaces neste ambiente"
)


async def _exec(worker, command: str) -> dict:
    return await worker.request("exec", timeout_s=30, command=["sh", "-c", command])


@real_bwrap
@pytest.mark.asyncio
async def test_jail_real_executa_comandos_e_nao_herda_segredos(tmp_path, monkeypatch):
    """Antes: `/lib64`, o interpretador e o pacote `backend` não existiam no
    jail, e o worker morria ao iniciar; agora executa, sem as chaves do backend."""
    monkeypatch.setenv("VECTORA_TESTE_SEGREDO", "abc123")
    manager = workspace_jail.WorkspaceJailManager()
    try:
        worker = await manager.get_or_spawn(
            "ws", str(tmp_path), SandboxPolicy(enabled=True)
        )
        ok = await _exec(worker, "echo ola > arquivo.txt && cat arquivo.txt")
        env = await _exec(worker, "env")
    finally:
        await manager.close("ws")

    assert ok["exit_code"] == 0
    assert ok["stdout"].strip() == "ola"
    assert (tmp_path / "arquivo.txt").read_text().strip() == "ola"
    assert "VECTORA_TESTE_SEGREDO" not in env["stdout"]
    assert "abc123" not in env["stdout"]


@real_bwrap
@pytest.mark.asyncio
async def test_jail_real_protege_hooks_e_config_do_git(tmp_path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    (tmp_path / ".git" / "config").write_text("[core]\n")
    manager = workspace_jail.WorkspaceJailManager()
    try:
        worker = await manager.get_or_spawn(
            "ws", str(tmp_path), SandboxPolicy(enabled=True)
        )
        hook = await _exec(worker, "echo x > .git/hooks/pre-commit")
        config = await _exec(worker, "echo x >> .git/config")
        objeto = await _exec(worker, "echo x > .git/HEAD")
    finally:
        await manager.close("ws")

    assert hook["exit_code"] != 0
    assert config["exit_code"] != 0
    assert not (tmp_path / ".git/hooks/pre-commit").exists()
    assert (tmp_path / ".git/config").read_text() == "[core]\n"
    assert objeto["exit_code"] == 0  # o resto de .git segue gravável


@real_bwrap
@pytest.mark.asyncio
async def test_jail_real_com_git_writable_permite_gravar_hook(tmp_path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    manager = workspace_jail.WorkspaceJailManager()
    try:
        worker = await manager.get_or_spawn(
            "ws", str(tmp_path), SandboxPolicy(enabled=True, git_writable=True)
        )
        hook = await _exec(worker, "echo x > .git/hooks/pre-commit")
    finally:
        await manager.close("ws")

    assert hook["exit_code"] == 0


@real_bwrap
@pytest.mark.asyncio
async def test_jail_real_nao_enxerga_o_home_do_usuario(tmp_path):
    manager = workspace_jail.WorkspaceJailManager()
    try:
        worker = await manager.get_or_spawn(
            "ws", str(tmp_path), SandboxPolicy(enabled=True)
        )
        home = await _exec(worker, 'ls -A "$HOME"')
        real_home = await _exec(worker, f"ls -A {Path.home()}")
    finally:
        await manager.close("ws")

    assert ".ssh" not in home["stdout"]
    assert ".aws" not in home["stdout"]
    # O caminho do home real pode existir vazio (o bwrap cria os pais das
    # montagens do runtime), mas nenhum arquivo do usuário aparece.
    for name in (".ssh", ".aws", ".gnupg", ".bashrc", ".config"):
        assert name not in real_home["stdout"].split()


@real_bwrap
@pytest.mark.asyncio
async def test_jail_real_com_isolamento_exigido_reporta_tudo_aplicado(tmp_path):
    pytest.importorskip("pyseccomp")
    manager = workspace_jail.WorkspaceJailManager()
    try:
        worker = await manager.get_or_spawn(
            "ws",
            str(tmp_path),
            SandboxPolicy(enabled=True, require_full_isolation=True),
        )
        status = await worker.isolation_status()
    finally:
        await manager.close("ws")

    assert status == {
        "seccomp": True,
        "landlock": True,
        "no_new_privs": True,
        "rlimits": True,
    }


@real_bwrap
def test_bwrap_nao_executa_ioctl_tiocsti_nem_syscalls_negadas(tmp_path):
    pytest.importorskip("pyseccomp")
    # Um filtro real carregado pelo bwrap mata `unshare`: o processo morre por sinal.
    from backend.sandbox.linux import build_seccomp_filter

    bpf = build_seccomp_filter()
    assert bpf
    read_fd, write_fd = os.pipe()
    os.write(write_fd, bpf)
    os.close(write_fd)
    argv = build_bwrap_command(
        SandboxPolicy(enabled=True), str(tmp_path), ["unshare", "-U", "true"]
    )
    argv = [argv[0], "--seccomp", str(read_fd), *argv[1:]]
    run = subprocess.run(  # noqa: S603
        argv, capture_output=True, pass_fds=(read_fd,), timeout=30, check=False
    )

    assert run.returncode != 0
