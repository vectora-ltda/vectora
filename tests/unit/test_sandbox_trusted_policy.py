"""Política confiável do sandbox: o usuário amplia, o repositório só restringe."""

from __future__ import annotations

import pytest

from backend.sandbox import policy as policy_module
from backend.sandbox.policy import (
    DISABLED_POLICY,
    SandboxPolicy,
    policy_to_toml,
    resolve_policy,
    trusted_policy_path,
    write_trusted_policy,
)


@pytest.fixture(autouse=True)
def _sem_deteccao_do_ambiente(monkeypatch):
    monkeypatch.setattr(policy_module, "_native_sandbox_available_sync", lambda: False)


def _user(root, toml: str) -> None:
    trusted_policy_path(root).write_text(toml, encoding="utf-8")


def _repo(root, toml: str) -> None:
    (root / "vectora.toml").write_text(toml, encoding="utf-8")


def test_sem_nenhum_arquivo_segue_o_padrao_do_ambiente(tmp_path):
    assert resolve_policy(tmp_path) == DISABLED_POLICY


def test_politica_do_usuario_fica_fora_do_workspace(tmp_path):
    assert not trusted_policy_path(tmp_path).is_relative_to(tmp_path)


def test_caminhos_de_workspaces_diferentes_nao_colidem(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()

    assert trusted_policy_path(a) != trusted_policy_path(b)


def test_politica_do_usuario_define_o_que_o_repositorio_nao_pode(tmp_path):
    _user(
        tmp_path,
        '[sandbox]\nrw_paths = ["/dados"]\nenv_pass = ["NPM_TOKEN"]\n'
        "git_writable = true\nallow_tcp_ports = [8080]\n",
    )

    policy = resolve_policy(tmp_path)

    assert policy.enabled is True
    assert policy.rw_paths == ("/dados",)
    assert policy.env_pass == ("NPM_TOKEN",)
    assert policy.git_writable is True
    assert policy.allow_tcp_ports == (8080,)


# ── o repositório só restringe ──────────────────────────────────────────────


def test_repositorio_nao_amplia_caminhos_graveis(tmp_path):
    _repo(tmp_path, '[sandbox]\nrw_paths = ["/etc", "/home"]\n')

    policy = resolve_policy(tmp_path)

    assert policy.rw_paths == ()
    assert "rw_paths" in policy.ignored_repo_keys


def test_repositorio_nao_amplia_leitura_nem_ambiente_nem_portas(tmp_path):
    _repo(
        tmp_path,
        '[sandbox]\nro_paths = ["/segredos"]\nenv_pass = ["AWS_SECRET"]\n'
        "allow_tcp_ports = [22]\n",
    )

    policy = resolve_policy(tmp_path)

    assert policy.ro_paths == ()
    assert policy.env_pass == ()
    assert policy.allow_tcp_ports == ()
    assert set(policy.ignored_repo_keys) == {"ro_paths", "env_pass", "allow_tcp_ports"}


def test_repositorio_nao_libera_a_escrita_no_git(tmp_path):
    _repo(tmp_path, "[sandbox]\ngit_writable = true\n")

    policy = resolve_policy(tmp_path)

    assert policy.git_writable is False
    assert "git_writable" in policy.ignored_repo_keys


def test_repositorio_nao_troca_backend_nem_imagem_nem_host(tmp_path):
    _repo(
        tmp_path,
        '[sandbox]\nbackend = "docker"\ndocker_image = "evil:latest"\n'
        'remote_host = "x@y"\nssh_key_id = "k"\n',
    )

    policy = resolve_policy(tmp_path)

    assert policy.backend == "local"
    assert policy.docker_image is None
    assert policy.remote_host is None
    assert policy.ssh_key_id is None
    assert set(policy.ignored_repo_keys) == {
        "backend",
        "docker_image",
        "remote_host",
        "ssh_key_id",
    }


def test_repositorio_nao_desliga_o_sandbox_do_usuario(tmp_path):
    _user(tmp_path, "[sandbox]\nenabled = true\n")
    _repo(tmp_path, "[sandbox]\nenabled = false\n")

    policy = resolve_policy(tmp_path)

    assert policy.enabled is True
    assert "enabled" in policy.ignored_repo_keys


def test_repositorio_nao_desliga_lockdown_nem_liga_gpu_do_usuario(tmp_path):
    _user(tmp_path, "[sandbox]\nlockdown = true\nno_gpu = true\n")
    _repo(tmp_path, "[sandbox]\nlockdown = false\nno_gpu = false\n")

    policy = resolve_policy(tmp_path)

    assert policy.lockdown is True
    assert policy.no_gpu is True
    assert set(policy.ignored_repo_keys) == {"lockdown", "no_gpu"}


def test_repositorio_pode_ligar_o_sandbox_e_o_lockdown(tmp_path):
    _repo(tmp_path, "[sandbox]\nlockdown = true\n")

    policy = resolve_policy(tmp_path)

    assert policy.enabled is True
    assert policy.lockdown is True
    assert policy.ignored_repo_keys == ()


def test_repositorio_acrescenta_mascaras_sem_perder_as_do_usuario(tmp_path):
    _user(tmp_path, '[sandbox]\nmask = ["segredo.txt"]\n')
    _repo(tmp_path, '[sandbox]\nmask = ["outro.key"]\n')

    policy = resolve_policy(tmp_path)

    assert policy.mask == ("segredo.txt", "outro.key")


def test_repositorio_pode_estreitar_o_que_o_usuario_permite(tmp_path):
    _user(
        tmp_path,
        '[sandbox]\nrw_paths = ["/a", "/b"]\nenv_pass = ["X", "Y"]\n'
        "git_writable = true\nallow_tcp_ports = [80, 443]\n",
    )
    _repo(
        tmp_path,
        '[sandbox]\nrw_paths = ["/a"]\nenv_pass = ["Y"]\n'
        "git_writable = false\nallow_tcp_ports = [443]\n",
    )

    policy = resolve_policy(tmp_path)

    assert policy.rw_paths == ("/a",)
    assert policy.env_pass == ("Y",)
    assert policy.git_writable is False
    assert policy.allow_tcp_ports == (443,)
    assert policy.ignored_repo_keys == ()


def test_repositorio_pode_exigir_isolamento_completo(tmp_path):
    _repo(tmp_path, "[sandbox]\nrequire_full_isolation = true\n")

    assert resolve_policy(tmp_path).require_full_isolation is True


def test_repositorio_sem_secao_sandbox_mantem_desabilitado_sem_politica_do_usuario(
    tmp_path,
):
    _repo(tmp_path, '[hooks]\npost_file_write = ["true"]\n')

    assert resolve_policy(tmp_path).enabled is False


def test_repositorio_sem_secao_sandbox_nao_mexe_na_politica_do_usuario(tmp_path):
    _user(tmp_path, '[sandbox]\nrw_paths = ["/a"]\n')
    _repo(tmp_path, '[hooks]\npost_file_write = ["true"]\n')

    policy = resolve_policy(tmp_path)

    assert policy.enabled is True
    assert policy.rw_paths == ("/a",)


# ── falha fechada ───────────────────────────────────────────────────────────


def test_vectora_toml_malformado_vira_lockdown(tmp_path):
    _repo(tmp_path, "[sandbox\nquebrado")

    policy = resolve_policy(tmp_path)

    assert policy.enabled is True
    assert policy.lockdown is True
    assert policy.rw_paths == ()


def test_politica_do_usuario_malformada_vira_lockdown(tmp_path):
    _user(tmp_path, "[sandbox\nquebrado")

    policy = resolve_policy(tmp_path)

    assert policy.enabled is True
    assert policy.lockdown is True


def test_campo_com_tipo_errado_no_repositorio_vira_lockdown(tmp_path):
    _repo(tmp_path, '[sandbox]\nrw_paths = "nao-e-lista"\n')

    policy = resolve_policy(tmp_path)

    assert policy.lockdown is True


# ── gravação ────────────────────────────────────────────────────────────────


def test_write_trusted_policy_vai_e_volta(tmp_path):
    original = SandboxPolicy(
        enabled=True,
        rw_paths=("/dados",),
        mask=("a", "b"),
        allow_tcp_ports=(8080,),
        env_pass=("NPM_TOKEN",),
        git_writable=True,
        require_full_isolation=True,
        lockdown=False,
    )

    path = write_trusted_policy(tmp_path, original)
    relida = policy_module.parse_policy(path)

    assert path == trusted_policy_path(tmp_path)
    assert relida == original


def test_policy_to_toml_escapa_caminhos_com_aspas_e_unicode():
    texto = policy_to_toml(SandboxPolicy(enabled=True, rw_paths=('/a "b"', "/ção")))

    assert '"/a \\"b\\""' in texto
    assert '"/ção"' in texto


def test_aviso_de_chaves_ignoradas_aparece_uma_vez_por_workspace(tmp_path, caplog):
    policy_module._logged_ignored.clear()
    _repo(tmp_path, '[sandbox]\nrw_paths = ["/etc"]\n')

    with caplog.at_level("WARNING"):
        resolve_policy(tmp_path)
        resolve_policy(tmp_path)

    avisos = [r for r in caplog.records if "tenta ampliar" in r.getMessage()]
    assert len(avisos) == 1


# ── limites de recursos ─────────────────────────────────────────────────────


def test_limites_padrao():
    policy = SandboxPolicy(enabled=True)

    assert (policy.max_file_mb, policy.memory_limit_mb, policy.tmp_size_mb) == (
        4096,
        None,
        1024,
    )


def test_parse_le_os_limites(tmp_path):
    arquivo = tmp_path / "p.toml"
    arquivo.write_text(
        "[sandbox]\nmax_file_mb = 100\nmemory_limit_mb = 2048\ntmp_size_mb = 256\n"
    )

    policy = policy_module.parse_policy(arquivo)

    assert (policy.max_file_mb, policy.memory_limit_mb, policy.tmp_size_mb) == (
        100,
        2048,
        256,
    )


@pytest.mark.parametrize(
    "linha",
    [
        "max_file_mb = 0",
        "max_file_mb = -1",
        'tmp_size_mb = "1g"',
        "memory_limit_mb = true",
    ],
)
def test_limite_invalido_falha_fechado(tmp_path, linha):
    arquivo = tmp_path / "p.toml"
    arquivo.write_text(f"[sandbox]\n{linha}\n")

    assert policy_module.parse_policy(arquivo).lockdown is True


def test_repositorio_so_pode_baixar_os_limites(tmp_path):
    trusted_policy_path(tmp_path).write_text(
        "[sandbox]\nmax_file_mb = 1000\ntmp_size_mb = 500\nmemory_limit_mb = 4000\n"
    )
    (tmp_path / "vectora.toml").write_text(
        "[sandbox]\nmax_file_mb = 200\ntmp_size_mb = 2000\nmemory_limit_mb = 8000\n"
    )

    policy = resolve_policy(tmp_path)

    assert policy.max_file_mb == 200  # mais estrito: vale
    assert policy.tmp_size_mb == 500  # mais frouxo: ignorado
    assert policy.memory_limit_mb == 4000
    assert set(policy.ignored_repo_keys) == {"tmp_size_mb", "memory_limit_mb"}


def test_repositorio_pode_impor_limite_de_memoria_quando_o_usuario_nao_tem(tmp_path):
    (tmp_path / "vectora.toml").write_text("[sandbox]\nmemory_limit_mb = 1024\n")

    policy = resolve_policy(tmp_path)

    assert policy.memory_limit_mb == 1024
    assert policy.ignored_repo_keys == ()


def test_limites_vao_e_voltam_pelo_toml(tmp_path):
    original = SandboxPolicy(
        enabled=True, max_file_mb=50, memory_limit_mb=512, tmp_size_mb=64
    )

    path = write_trusted_policy(tmp_path, original)

    assert policy_module.parse_policy(path) == original
