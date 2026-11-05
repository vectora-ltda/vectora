"""Sandbox — dry_run: monta o argv do `bwrap` sem precisar do binário real
instalado (CI-friendly)."""

from __future__ import annotations

import pytest

from backend.sandbox.dry_run import DENIED_SYSCALLS, build_bwrap_command
from backend.sandbox.policy import SandboxPolicy


def test_command_contains_workspace_bind_and_trailing_command():
    policy = SandboxPolicy(enabled=True)

    argv = build_bwrap_command(policy, "/home/user/project", ["ls", "-la"])

    assert argv[0] == "bwrap"
    assert "--bind" in argv
    idx = argv.index("--bind")
    assert argv[idx + 1 : idx + 3] == ["/home/user/project", "/home/user/project"]
    assert argv[-3:] == ["--", "ls", "-la"]


def test_rw_and_ro_paths_are_mounted():
    policy = SandboxPolicy(enabled=True, rw_paths=("/data",), ro_paths=("/opt/tools",))

    argv = build_bwrap_command(policy, "/ws", ["true"])

    data_idx = argv.index("/data")
    assert argv[data_idx - 1 : data_idx + 2] == ["--bind", "/data", "/data"]
    tools_idx = argv.index("/opt/tools")
    assert argv[tools_idx - 1 : tools_idx + 2] == [
        "--ro-bind",
        "/opt/tools",
        "/opt/tools",
    ]


def test_mask_de_arquivo_literal_vira_ro_bind_dev_null(tmp_path):
    # Arquivo (existente ou não) mascarado: --ro-bind /dev/null <path> —
    # esconde conteúdo sem impedir o path de existir, não --tmpfs (que
    # mascararia um diretório inteiro, não um arquivo específico).
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    policy = SandboxPolicy(enabled=True, mask=(".env",))

    argv = build_bwrap_command(policy, str(tmp_path), ["true"])

    env_path = str(tmp_path / ".env")
    idx = argv.index(env_path)
    assert argv[idx - 2 : idx] == ["--ro-bind", "/dev/null"]


def test_mask_de_diretorio_via_glob_vira_tmpfs(tmp_path):
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "key.pem").write_text("x", encoding="utf-8")
    policy = SandboxPolicy(enabled=True, mask=("secrets",))

    argv = build_bwrap_command(policy, str(tmp_path), ["true"])

    secrets_path = str(tmp_path / "secrets")
    idx = argv.index(secrets_path)
    assert argv[idx - 1] == "--tmpfs"


def test_mask_glob_expande_contra_arquivos_reais(tmp_path):
    (tmp_path / "a.pem").write_text("x", encoding="utf-8")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "b.pem").write_text("x", encoding="utf-8")
    policy = SandboxPolicy(enabled=True, mask=("**/*.pem",))

    argv = build_bwrap_command(policy, str(tmp_path), ["true"])

    assert str(tmp_path / "a.pem") in argv
    assert str(nested / "b.pem") in argv


def test_mask_glob_sem_match_nao_quebra(tmp_path):
    # Erro/borda: padrão glob sem nenhum arquivo correspondente é ignorado,
    # nunca fatal.
    policy = SandboxPolicy(enabled=True, mask=("**/*.pem",))

    argv = build_bwrap_command(policy, str(tmp_path), ["true"])

    assert "--" in argv  # comando montou normalmente até o fim


def test_vectora_toml_sempre_mascarado_mesmo_sem_declarar(tmp_path):
    # 4.5 — o worker nunca vê sua própria política, mesmo se o usuário não
    # incluir vectora.toml em [sandbox].mask explicitamente.
    policy = SandboxPolicy(enabled=True, mask=())

    argv = build_bwrap_command(policy, str(tmp_path), ["true"])

    toml_path = str(tmp_path / "vectora.toml")
    idx = argv.index(toml_path)
    assert argv[idx - 2 : idx] == ["--ro-bind", "/dev/null"]


def test_worker_home_dedicado_sempre_presente():
    from backend.sandbox.dry_run import WORKER_HOME

    argv = build_bwrap_command(SandboxPolicy(enabled=True), "/ws", ["true"])

    idx = argv.index(WORKER_HOME)
    assert argv[idx - 1] == "--tmpfs"
    assert "--setenv" in argv
    setenv_idx = argv.index("--setenv")
    assert argv[setenv_idx + 1 : setenv_idx + 3] == ["HOME", WORKER_HOME]


def test_lockdown_seta_env_var_de_rlimits():
    locked = build_bwrap_command(
        SandboxPolicy(enabled=True, lockdown=True), "/ws", ["true"]
    )
    unlocked = build_bwrap_command(
        SandboxPolicy(enabled=True, lockdown=False), "/ws", ["true"]
    )

    locked_idx = locked.index("VECTORA_SANDBOX_LOCKDOWN")
    assert locked[locked_idx + 1] == "1"
    unlocked_idx = unlocked.index("VECTORA_SANDBOX_LOCKDOWN")
    assert unlocked[unlocked_idx + 1] == "0"


def test_lockdown_adds_unshare_net():
    locked = build_bwrap_command(
        SandboxPolicy(enabled=True, lockdown=True), "/ws", ["true"]
    )
    unlocked = build_bwrap_command(
        SandboxPolicy(enabled=True, lockdown=False), "/ws", ["true"]
    )

    assert "--unshare-net" in locked
    assert "--unshare-net" not in unlocked


def test_allow_tcp_ports_propagado_como_env_var_json():
    argv = build_bwrap_command(
        SandboxPolicy(enabled=True, allow_tcp_ports=(443, 8080)), "/ws", ["true"]
    )

    idx = argv.index("VECTORA_SANDBOX_ALLOW_TCP_PORTS")
    assert argv[idx + 1] == "[443, 8080]"


def test_allow_tcp_ports_vazio_ainda_seta_a_env_var(tmp_path):
    # Erro/borda: sem allow_tcp_ports configurado, a env var ainda existe
    # (lista vazia) — o worker sempre sabe ler `VECTORA_SANDBOX_ALLOW_TCP_PORTS`,
    # nunca precisa tratar a ausência da chave como um caso à parte.
    argv = build_bwrap_command(SandboxPolicy(enabled=True), "/ws", ["true"])

    idx = argv.index("VECTORA_SANDBOX_ALLOW_TCP_PORTS")
    assert argv[idx + 1] == "[]"


def test_no_mounts_when_policy_has_none_configured():
    # Erro/borda: rw_paths/ro_paths/mask todos vazios não deve quebrar a
    # montagem — só o essencial (workspace + sistema + home dedicado do
    # worker + auto-mask do vectora.toml) fica no comando.
    policy = SandboxPolicy(enabled=True, rw_paths=(), ro_paths=(), mask=())

    argv = build_bwrap_command(policy, "/ws", ["true"])

    # 1 --tmpfs: só o $HOME dedicado do worker (nenhum mask de usuário).
    assert argv.count("--tmpfs") == 1
    # Apenas o bind do próprio workspace deve existir (nenhum --bind extra).
    assert argv.count("--bind") == 1


def test_empty_command_still_produces_valid_argv_with_trailing_separator():
    # Erro/borda: comando vazio não deve quebrar a montagem — o "--" fica
    # sozinho no final, sem nenhum argumento de comando depois dele.
    argv = build_bwrap_command(SandboxPolicy(enabled=True), "/ws", [])

    assert argv[-1] == "--"


def test_multiple_rw_and_ro_paths_preserve_order():
    policy = SandboxPolicy(
        enabled=True,
        rw_paths=("/a", "/b", "/c"),
        ro_paths=("/x", "/y"),
    )

    argv = build_bwrap_command(policy, "/ws", ["true"])

    rw_binds = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--bind"]
    assert rw_binds == ["/ws", "/a", "/b", "/c"]
    ro_binds = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--ro-bind"]
    # os três primeiros --ro-bind são /usr /bin /lib fixos do sistema; o(s)
    # último(s) vem(êm) do auto-mask de vectora.toml (/dev/null) — /x /y
    # ficam no meio, na ordem declarada.
    assert ro_binds[3:5] == ["/x", "/y"]


def test_paths_with_spaces_and_unicode_pass_through_unchanged():
    policy = SandboxPolicy(enabled=True, rw_paths=("/home/usuário/meu projeto",))

    argv = build_bwrap_command(policy, "/ws", ["true"])

    assert "/home/usuário/meu projeto" in argv


def test_command_arguments_with_spaces_and_quotes_pass_through_unchanged():
    argv = build_bwrap_command(
        SandboxPolicy(enabled=True), "/ws", ["echo", "hello world", '"quoted"']
    )

    assert argv[-3:] == ["echo", "hello world", '"quoted"']


def test_docker_and_ssh_only_fields_are_ignored_by_bwrap_command():
    # docker_image/remote_host/ssh_key_id não fazem sentido pro backend
    # local — não devem vazar pro argv do bwrap.
    policy = SandboxPolicy(
        enabled=True,
        docker_image="node:20-slim",
        remote_host="user@host",
        ssh_key_id="key-1",
    )

    argv = build_bwrap_command(policy, "/ws", ["true"])

    assert "node:20-slim" not in argv
    assert "user@host" not in argv
    assert "key-1" not in argv


def test_denied_syscalls_list_contains_expected_dangerous_calls():
    for name in ("ptrace", "bpf", "mount", "unshare", "reboot"):
        assert name in DENIED_SYSCALLS


def test_denied_syscalls_is_immutable_tuple():
    assert isinstance(DENIED_SYSCALLS, tuple)
    with pytest.raises(TypeError):
        DENIED_SYSCALLS[0] = "ptrace"  # ty: ignore[invalid-assignment]


def test_workspace_dir_appears_exactly_once_as_bind_source():
    argv = build_bwrap_command(SandboxPolicy(enabled=True), "/ws/project", ["true"])

    bind_idx = argv.index("--bind")
    assert argv[bind_idx + 1] == "/ws/project"
    assert argv[bind_idx + 2] == "/ws/project"
    assert "--chdir" in argv
    chdir_idx = argv.index("--chdir")
    assert argv[chdir_idx + 1] == "/ws/project"
