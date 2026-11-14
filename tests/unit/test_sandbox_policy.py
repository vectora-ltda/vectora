"""Sandbox — parser de `vectora.toml` [sandbox]. Fail-closed sempre que o
arquivo existe mas está malformado ou tem campo de tipo errado."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.sandbox import policy as policy_module
from backend.sandbox.policy import (
    AUTO_ENABLED_POLICY,
    DISABLED_POLICY,
    LOCKED_DOWN_POLICY,
    detect_wsl2,
    parse_policy,
    warm_wsl2_cache,
    wsl2_diagnostic,
)


@pytest.mark.usefixtures("sem_bwrap_automatico")
def test_missing_file_returns_disabled_policy(tmp_path):
    result = parse_policy(tmp_path / "vectora.toml")

    assert result == DISABLED_POLICY
    assert result.enabled is False


def test_file_without_sandbox_section_returns_disabled_policy(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text('[other]\nkey = "value"\n', encoding="utf-8")

    result = parse_policy(toml_path)

    assert result == DISABLED_POLICY


def test_valid_sandbox_section_parses_all_fields(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text(
        """
[sandbox]
enabled = true
backend = "local"
rw_paths = ["/workspace"]
ro_paths = ["/opt/tools"]
mask = [".env"]
no_gpu = false
lockdown = true
""",
        encoding="utf-8",
    )

    result = parse_policy(toml_path)

    assert result.enabled is True
    assert result.backend == "local"
    assert result.rw_paths == ("/workspace",)
    assert result.ro_paths == ("/opt/tools",)
    assert result.mask == (".env",)
    assert result.no_gpu is False
    assert result.lockdown is True


def test_malformed_toml_fails_closed(tmp_path):
    # Erro/borda: TOML quebrado nunca deve desabilitar a proteção
    # silenciosamente — a política mais restritiva é o resultado seguro.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[sandbox\nenabled = true", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result == LOCKED_DOWN_POLICY
    assert result.enabled is True
    assert result.lockdown is True


def test_wrong_field_type_fails_closed(tmp_path):
    # rw_paths deveria ser array de strings — aqui é um int solto.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[sandbox]\nrw_paths = 42\n", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result == LOCKED_DOWN_POLICY


def test_allow_tcp_ports_parses_int_list(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[sandbox]\nallow_tcp_ports = [443, 8080]\n", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result.allow_tcp_ports == (443, 8080)


def test_allow_tcp_ports_omitted_defaults_to_empty(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[sandbox]\n", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result.allow_tcp_ports == ()


def test_allow_tcp_ports_non_numeric_value_fails_closed(tmp_path):
    # Erro/borda: valor não-numérico na lista cai no mesmo fail-closed já
    # existente da política, em vez de propagar ValueError ou ignorar.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text(
        '[sandbox]\nallow_tcp_ports = ["nao-e-porta"]\n', encoding="utf-8"
    )

    result = parse_policy(toml_path)

    assert result == LOCKED_DOWN_POLICY


def test_defaults_applied_when_fields_omitted(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[sandbox]\n", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result.enabled is True
    assert result.backend == "local"
    assert result.no_gpu is True
    assert result.lockdown is False
    assert ".env" in result.mask


def test_secao_sandbox_vazia_como_dict_usa_defaults(tmp_path):
    # [sandbox] presente mas sem nenhuma chave — dict vazio, não None.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[sandbox]\n", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result != DISABLED_POLICY
    assert result.rw_paths == ()
    assert result.ro_paths == ()


def test_sandbox_como_lista_em_vez_de_tabela_falha_fechado(tmp_path):
    # Erro/borda: [[sandbox]] TOML array-of-tables vira lista, não dict —
    # isinstance(section, dict) deve rejeitar e não vira DISABLED silencioso.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[[sandbox]]\nenabled = true\n", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result == DISABLED_POLICY


def test_rw_paths_com_entradas_duplicadas_preserva_duplicatas(tmp_path):
    # Duplicado: parser não deduplica — é decisão do backend de execução,
    # não da camada de parsing (dedupe silencioso esconderia intenção).
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text(
        '[sandbox]\nrw_paths = ["/workspace", "/workspace"]\n', encoding="utf-8"
    )

    result = parse_policy(toml_path)

    assert result.rw_paths == ("/workspace", "/workspace")


def test_rw_e_ro_paths_vazios_explicitos_nao_quebram(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text(
        "[sandbox]\nrw_paths = []\nro_paths = []\nmask = []\n", encoding="utf-8"
    )

    result = parse_policy(toml_path)

    assert result.rw_paths == ()
    assert result.ro_paths == ()
    assert result.mask == ()


def test_mask_customizado_substitui_default_por_completo(tmp_path):
    # Borda: mask fornecido não faz merge com DEFAULT_MASK, substitui.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text('[sandbox]\nmask = ["**/*.key"]\n', encoding="utf-8")

    result = parse_policy(toml_path)

    assert result.mask == ("**/*.key",)
    assert ".env" not in result.mask


def test_docker_image_ausente_fica_none(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text('[sandbox]\nbackend = "docker"\n', encoding="utf-8")

    result = parse_policy(toml_path)

    assert result.docker_image is None
    assert result.remote_host is None
    assert result.ssh_key_id is None


def test_docker_image_com_tipo_numerico_e_coagido_para_string(tmp_path):
    # docker_image = 123 não é erro de tipo fatal — str() converte.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[sandbox]\ndocker_image = 123\n", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result.docker_image == "123"


def test_backend_desconhecido_e_aceito_pelo_parser_mas_nao_pelo_dispatch(tmp_path):
    # O parser não valida o valor de backend (é o runner.py que faz dispatch
    # fail-closed) — só confirma que não quebra aqui, string livre é aceita.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text(
        '[sandbox]\nbackend = "backend-inexistente"\n', encoding="utf-8"
    )

    result = parse_policy(toml_path)

    assert result.backend == "backend-inexistente"


def test_arquivo_vazio_sem_nenhuma_secao_retorna_disabled(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result == DISABLED_POLICY


def test_encoding_invalido_falha_fechado(tmp_path):
    # Erro/borda: bytes que não decodificam como UTF-8 — read_text levanta
    # UnicodeDecodeError, capturado pelo except genérico do parse do TOML.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_bytes(b"[sandbox]\nbackend = \xff\xfe\n")

    result = parse_policy(toml_path)

    assert result == LOCKED_DOWN_POLICY


def test_enabled_com_string_truthy_e_coagido_para_bool(tmp_path):
    # enabled = "false" (string) via bool() vira True — comportamento do
    # bool() do Python é documentado aqui pra não surpreender no futuro.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text('[sandbox]\nenabled = "false"\n', encoding="utf-8")

    result = parse_policy(toml_path)

    assert result.enabled is True


def test_lockdown_true_com_backend_custom_preserva_backend(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text(
        '[sandbox]\nlockdown = true\nbackend = "docker"\n', encoding="utf-8"
    )

    result = parse_policy(toml_path)

    assert result.lockdown is True
    assert result.backend == "docker"


def test_empty_toml_file_returns_disabled_policy(tmp_path):
    # Erro/borda: arquivo vazio (sem nenhuma seção) não deve quebrar o parser.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result == DISABLED_POLICY


def test_sandbox_section_not_a_table_returns_disabled_policy(tmp_path):
    # `[sandbox]` precisa ser uma tabela — um escalar solto não é seção válida.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text('sandbox = "yes"\n', encoding="utf-8")

    result = parse_policy(toml_path)

    assert result == DISABLED_POLICY


def test_ro_paths_wrong_type_fails_closed(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[sandbox]\nro_paths = 7\n", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result == LOCKED_DOWN_POLICY


def test_mask_wrong_type_fails_closed(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[sandbox]\nmask = true\n", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result == LOCKED_DOWN_POLICY


def test_duplicate_keys_in_toml_fails_closed(tmp_path):
    # TOML não permite chave duplicada na mesma tabela — é erro de sintaxe.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text(
        "[sandbox]\nenabled = true\nenabled = false\n", encoding="utf-8"
    )

    result = parse_policy(toml_path)

    assert result == LOCKED_DOWN_POLICY


def test_optional_backend_fields_parsed_when_present(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text(
        """
[sandbox]
backend = "docker"
docker_image = "node:20-slim"
remote_host = "user@host"
ssh_key_id = "key-123"
""",
        encoding="utf-8",
    )

    result = parse_policy(toml_path)

    assert result.docker_image == "node:20-slim"
    assert result.remote_host == "user@host"
    assert result.ssh_key_id == "key-123"


def test_optional_backend_fields_default_to_none(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[sandbox]\n", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result.docker_image is None
    assert result.remote_host is None
    assert result.ssh_key_id is None


def test_enabled_explicitly_false_is_respected(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[sandbox]\nenabled = false\n", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result.enabled is False


def test_unknown_backend_name_is_preserved_not_normalized(tmp_path):
    # O parser não valida o valor de `backend` — quem falha fechado por
    # backend desconhecido é o dispatcher (runner.py), não o parser.
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text('[sandbox]\nbackend = "singularity"\n', encoding="utf-8")

    result = parse_policy(toml_path)

    assert result.backend == "singularity"
    assert result.enabled is True


def test_rw_and_ro_paths_with_unicode_and_spaces(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text(
        '[sandbox]\nrw_paths = ["/home/usuário/meu projeto"]\n',
        encoding="utf-8",
    )

    result = parse_policy(toml_path)

    assert result.rw_paths == ("/home/usuário/meu projeto",)


def test_empty_rw_and_ro_paths_lists_are_valid(tmp_path):
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("[sandbox]\nrw_paths = []\nro_paths = []\n", encoding="utf-8")

    result = parse_policy(toml_path)

    assert result.rw_paths == ()
    assert result.ro_paths == ()


@pytest.fixture(autouse=True)
def _reset_wsl2_cache():
    policy_module._wsl2_distro_cache = policy_module._UNSET
    yield
    policy_module._wsl2_distro_cache = policy_module._UNSET


class TestAutoEnableSemVectoraToml:
    """Workspace sem `vectora.toml` nenhum: sandbox se auto-habilita quando
    o cache de `detect_wsl2()` (populado por `warm_wsl2_cache()` no startup
    do backend) já achou uma distro elegível — sem exigir opt-in manual."""

    @pytest.mark.usefixtures("sem_bwrap_automatico")
    def test_cache_unset_mantem_desabilitado(self, tmp_path):
        """Regressão: antes do warmup rodar (cache ainda _UNSET), nunca
        auto-habilita — nunca um falso positivo por checar cedo demais."""
        result = parse_policy(tmp_path / "vectora.toml")

        assert result == DISABLED_POLICY

    def test_cache_com_distro_elegivel_auto_habilita(self, tmp_path):
        policy_module._wsl2_distro_cache = "Ubuntu"

        result = parse_policy(tmp_path / "vectora.toml")

        assert result == AUTO_ENABLED_POLICY
        assert result.enabled is True

    @pytest.mark.usefixtures("sem_bwrap_automatico")
    def test_cache_none_mantem_desabilitado(self, tmp_path):
        """Regressão: detecção já rodou mas não achou distro elegível
        (cache=None, distinto de _UNSET) — continua desabilitado."""
        policy_module._wsl2_distro_cache = None

        result = parse_policy(tmp_path / "vectora.toml")

        assert result == DISABLED_POLICY

    @pytest.mark.asyncio
    async def test_warm_wsl2_cache_popula_o_cache_sincrono(self, monkeypatch):
        output = "  NAME      STATE           VERSION\n* Ubuntu    Running         2\n"
        monkeypatch.setattr(
            policy_module.asyncio,
            "create_subprocess_exec",
            _mock_wsl_exec(output),
        )

        await warm_wsl2_cache()

        assert policy_module._wsl2_eligible_sync() is True


class TestAutoEnableNativoLinuxMacos:
    """Linux (`bwrap`) não exige detecção assíncrona no startup, só
    `shutil.which` no hot path síncrono de `parse_policy()`. macOS nunca
    se auto-habilita: o worker persistente usa `bwrap` em qualquer host
    não Windows, então auto-habilitar ali quebraria a primeira ação."""

    def test_linux_com_bwrap_disponivel_auto_habilita(self, tmp_path, monkeypatch):
        monkeypatch.setattr(policy_module.sys, "platform", "linux")
        monkeypatch.setattr(
            policy_module.shutil, "which", lambda nome: "/usr/bin/bwrap"
        )

        result = parse_policy(tmp_path / "vectora.toml")

        assert result == AUTO_ENABLED_POLICY

    def test_linux_sem_bwrap_mantem_desabilitado(self, tmp_path, monkeypatch):
        monkeypatch.setattr(policy_module.sys, "platform", "linux")
        monkeypatch.setattr(policy_module.shutil, "which", lambda nome: None)

        result = parse_policy(tmp_path / "vectora.toml")

        assert result == DISABLED_POLICY

    def test_macos_com_sandbox_exec_disponivel_nao_auto_habilita(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(policy_module.sys, "platform", "darwin")
        monkeypatch.setattr(
            policy_module.shutil, "which", lambda nome: "/usr/bin/sandbox-exec"
        )

        result = parse_policy(tmp_path / "vectora.toml")

        assert result == DISABLED_POLICY
        assert policy_module._native_sandbox_available_sync() is False

    def test_macos_sem_sandbox_exec_mantem_desabilitado(self, tmp_path, monkeypatch):
        monkeypatch.setattr(policy_module.sys, "platform", "darwin")
        monkeypatch.setattr(policy_module.shutil, "which", lambda nome: None)

        result = parse_policy(tmp_path / "vectora.toml")

        assert result == DISABLED_POLICY

    def test_windows_sem_wsl2_nao_e_afetado_pela_checagem_nativa(
        self, tmp_path, monkeypatch
    ):
        """Regressão: Windows continua exigindo WSL2 elegível — a checagem
        nativa (bwrap/sandbox-exec) nunca dispara fora de Linux/macOS, então
        não pode virar um auto-enable espúrio em Windows sem WSL2."""
        monkeypatch.setattr(policy_module.sys, "platform", "win32")
        monkeypatch.setattr(
            policy_module.shutil, "which", lambda nome: "/algum/binario"
        )

        result = parse_policy(tmp_path / "vectora.toml")

        assert result == DISABLED_POLICY

    def test_checagem_nativa_e_sincrona_sem_cache(self, monkeypatch):
        """`_native_sandbox_available_sync` não depende de warm-up prévio
        (diferente de `_wsl2_eligible_sync`) — reflete o `shutil.which`
        atual a cada chamada."""
        monkeypatch.setattr(policy_module.sys, "platform", "linux")

        monkeypatch.setattr(policy_module.shutil, "which", lambda nome: None)
        assert policy_module._native_sandbox_available_sync() is False
        monkeypatch.setattr(
            policy_module.shutil, "which", lambda nome: "/usr/bin/bwrap"
        )
        assert policy_module._native_sandbox_available_sync() is True


def _fake_wsl_proc(stdout_bytes: bytes = b"", returncode: int = 0):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout_bytes, b""))
    proc.wait = AsyncMock(return_value=returncode)
    proc.returncode = returncode
    return proc


def _mock_wsl_exec(list_output: str, usable_distros: set[str] | None = None):
    """Mock de `create_subprocess_exec` que distingue `wsl.exe -l -v`
    (lista de distros) de `wsl.exe -d <nome> -- test -x /bin/sh` (checagem
    de shell) e de `wsl.exe --status` (disponibilidade) pelos args reais —
    reflete o fluxo de duas fases de `detect_wsl2()`."""
    stdout_bytes = list_output.encode("utf-16-le")
    usable = usable_distros if usable_distros is not None else None

    async def _exec(*args, **_kwargs):
        if args[:2] == ("wsl.exe", "-l"):
            return _fake_wsl_proc(stdout_bytes, returncode=0)
        if args[0] == "wsl.exe" and args[1] == "--status":
            return _fake_wsl_proc(returncode=0)
        if args[:2] == ("wsl.exe", "-d"):
            name = args[2]
            ok = usable is None or name in usable
            return _fake_wsl_proc(returncode=0 if ok else 1)
        return _fake_wsl_proc(returncode=1)

    return _exec


@pytest.mark.asyncio
async def test_detect_wsl2_retorna_a_distro_default_marcada_com_versao_2(
    monkeypatch,
):
    output = "  NAME      STATE           VERSION\n* Ubuntu    Running         2\n  Debian    Stopped         1\n"
    monkeypatch.setattr(
        policy_module.asyncio,
        "create_subprocess_exec",
        _mock_wsl_exec(output),
    )

    result = await detect_wsl2()

    assert result == "Ubuntu"


@pytest.mark.asyncio
async def test_detect_wsl2_sem_default_cai_pra_primeira_distro_versao_2(monkeypatch):
    output = "  NAME      STATE           VERSION\n  Debian    Stopped         1\n  Fedora    Running         2\n"
    monkeypatch.setattr(
        policy_module.asyncio,
        "create_subprocess_exec",
        _mock_wsl_exec(output),
    )

    result = await detect_wsl2()

    assert result == "Fedora"


@pytest.mark.asyncio
async def test_detect_wsl2_sem_nenhuma_distro_versao_2_retorna_none(monkeypatch):
    output = "  NAME      STATE           VERSION\n  Debian    Stopped         1\n"
    monkeypatch.setattr(
        policy_module.asyncio,
        "create_subprocess_exec",
        _mock_wsl_exec(output),
    )

    result = await detect_wsl2()

    assert result is None


@pytest.mark.asyncio
async def test_detect_wsl2_sem_wsl_exe_instalado_retorna_none_sem_lancar(monkeypatch):
    # Erro/borda: wsl.exe ausente do sistema (não é Windows, ou WSL nunca
    # foi instalado) — FileNotFoundError nunca deve propagar.
    async def _raise(*args, **kwargs):
        raise FileNotFoundError("wsl.exe not found")

    monkeypatch.setattr(policy_module.asyncio, "create_subprocess_exec", _raise)

    result = await detect_wsl2()

    assert result is None


@pytest.mark.asyncio
async def test_detect_wsl2_com_codigo_de_saida_diferente_de_zero_retorna_none(
    monkeypatch,
):
    async def _exec(*args, **_kwargs):
        return _fake_wsl_proc(b"", returncode=1)

    monkeypatch.setattr(policy_module.asyncio, "create_subprocess_exec", _exec)

    result = await detect_wsl2()

    assert result is None


@pytest.mark.asyncio
async def test_detect_wsl2_resultado_e_cacheado_entre_chamadas(monkeypatch):
    output = "  NAME      STATE           VERSION\n* Ubuntu    Running         2\n"
    spawn_mock = AsyncMock(side_effect=_mock_wsl_exec(output))
    monkeypatch.setattr(policy_module.asyncio, "create_subprocess_exec", spawn_mock)

    first = await detect_wsl2()
    second = await detect_wsl2()

    assert first == second == "Ubuntu"
    # 1ª chamada: `-l -v` + `-d Ubuntu -- test -x /bin/sh`. 2ª chamada
    # (cache hit) não dispara nenhum subprocess novo.
    assert spawn_mock.call_count == 2


@pytest.mark.asyncio
async def test_detect_wsl2_ignora_docker_desktop_mesmo_sendo_a_distro_default(
    monkeypatch,
):
    # docker-desktop é a VM interna do Docker Desktop — nunca é uma distro
    # elegível de sandbox, mesmo marcada como default (`*`) na listagem.
    output = (
        "  NAME                   STATE           VERSION\n"
        "* docker-desktop         Running         2\n"
        "  docker-desktop-data    Running         2\n"
        "  Ubuntu                 Running         2\n"
    )
    monkeypatch.setattr(
        policy_module.asyncio,
        "create_subprocess_exec",
        _mock_wsl_exec(output),
    )

    result = await detect_wsl2()

    assert result == "Ubuntu"


@pytest.mark.asyncio
async def test_detect_wsl2_so_com_docker_desktop_retorna_none(monkeypatch):
    # Regressão do bug real: `wsl --status` só mostra `docker-desktop` —
    # não deve ser aceita como distro de uso geral.
    output = "  NAME               STATE           VERSION\n* docker-desktop     Running         2\n"
    monkeypatch.setattr(
        policy_module.asyncio,
        "create_subprocess_exec",
        _mock_wsl_exec(output),
    )

    result = await detect_wsl2()

    assert result is None


@pytest.mark.asyncio
async def test_detect_wsl2_distro_listada_sem_bin_sh_utilizavel_e_pulada(
    monkeypatch,
):
    # Distro aparece na listagem (existe, WSL2) mas não passa no teste de
    # `/bin/sh -x` — não é uma distro de uso geral utilizável.
    output = (
        "  NAME      STATE           VERSION\n"
        "* Broken    Running         2\n"
        "  Ubuntu    Running         2\n"
    )
    monkeypatch.setattr(
        policy_module.asyncio,
        "create_subprocess_exec",
        _mock_wsl_exec(output, usable_distros={"Ubuntu"}),
    )

    result = await detect_wsl2()

    assert result == "Ubuntu"


@pytest.mark.asyncio
async def test_detect_wsl2_nenhuma_distro_com_shell_utilizavel_retorna_none(
    monkeypatch,
):
    output = "  NAME      STATE           VERSION\n* Broken    Running         2\n"
    monkeypatch.setattr(
        policy_module.asyncio,
        "create_subprocess_exec",
        _mock_wsl_exec(output, usable_distros=set()),
    )

    result = await detect_wsl2()

    assert result is None


@pytest.mark.asyncio
async def test_wsl2_diagnostic_sem_wsl_instalado(monkeypatch):
    async def _raise(*args, **kwargs):
        raise FileNotFoundError("wsl.exe not found")

    monkeypatch.setattr(policy_module.asyncio, "create_subprocess_exec", _raise)

    result = await wsl2_diagnostic()

    assert result == "wsl_not_installed"


@pytest.mark.asyncio
async def test_wsl2_diagnostic_so_docker_desktop_instalado(monkeypatch):
    output = "  NAME               STATE           VERSION\n* docker-desktop     Running         2\n"
    monkeypatch.setattr(
        policy_module.asyncio,
        "create_subprocess_exec",
        _mock_wsl_exec(output),
    )

    result = await wsl2_diagnostic()

    assert result == "no_general_purpose_distro"


@pytest.mark.asyncio
async def test_wsl2_diagnostic_distro_sem_shell_utilizavel(monkeypatch):
    output = "  NAME      STATE           VERSION\n* Broken    Running         2\n"
    monkeypatch.setattr(
        policy_module.asyncio,
        "create_subprocess_exec",
        _mock_wsl_exec(output, usable_distros=set()),
    )

    result = await wsl2_diagnostic()

    assert result == "distro_missing_shell"


@pytest.mark.parametrize(
    "configured",
    [
        None,
        "[workspace]\nname = 'demo'\n",
        "[sandbox]\nenabled = true\n",
        "[sandbox]\nenabled = false\n",
    ],
)
@pytest.mark.parametrize("native", [True, False])
def test_documented_auto_activation_and_explicit_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: str | None,
    native: bool,
) -> None:
    """Arquivo ausente, ausência de seção e política explícita são estados distintos."""
    monkeypatch.setattr(policy_module, "_native_sandbox_available_sync", lambda: native)
    monkeypatch.setattr(policy_module, "_wsl2_eligible_sync", lambda: False)
    path = tmp_path / "vectora.toml"
    if configured is not None:
        path.write_text(configured, encoding="utf-8")
    expected = (
        native
        if configured is None
        else configured.startswith("[sandbox]\nenabled = true")
    )
    assert parse_policy(path).enabled is expected
