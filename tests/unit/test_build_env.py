"""Detecção do ambiente e relatório do ``scons install`` (utils/build_env.py)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from utils import build_env

REPO = Path(__file__).resolve().parents[2]

OS_RELEASE = {
    "pop": 'NAME="Pop!_OS"\nVERSION="24.04 LTS"\nID=pop\nID_LIKE="ubuntu debian"\n',
    "ubuntu": 'NAME="Ubuntu"\nVERSION="22.04.5 LTS (Jammy Jellyfish)"\nID=ubuntu\nID_LIKE=debian\n',
    "debian": 'NAME="Debian GNU/Linux"\nVERSION_ID="12"\nID=debian\n',
    "fedora": 'NAME="Fedora Linux"\nVERSION="41 (Workstation Edition)"\nID=fedora\n',
    "rocky": 'NAME="Rocky Linux"\nVERSION="9.4"\nID="rocky"\nID_LIKE="rhel centos fedora"\n',
    "arch": 'NAME="Arch Linux"\nID=arch\n',
    "manjaro": 'NAME="Manjaro Linux"\nID=manjaro\nID_LIKE=arch\n',
    "opensuse": 'NAME="openSUSE Tumbleweed"\nID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n',
    "alpine": 'NAME="Alpine Linux"\nVERSION_ID=3.20.3\nID=alpine\n',
    "gentoo": 'NAME="Gentoo"\nID=gentoo\n',
}


def _reader(files: dict[str, str]) -> Callable[[str], str]:
    def read(path: str) -> str:
        if path not in files:
            raise FileNotFoundError(path)
        return files[path]

    return read


class TestArquitetura:
    @pytest.mark.parametrize(
        ("machine", "expected"),
        [
            ("x86_64", "x64"),
            ("AMD64", "x64"),
            ("x64", "x64"),
            ("arm64", "arm64"),
            ("aarch64", "arm64"),
            ("ARM64", "arm64"),
            ("riscv64", "riscv64"),
            ("", "desconhecida"),
        ],
    )
    def test_normaliza(self, machine: str, expected: str) -> None:
        assert build_env.normalize_arch(machine) == expected


class TestDistribuicao:
    @pytest.mark.parametrize(
        ("distro", "family"),
        [
            ("pop", "debian"),
            ("ubuntu", "debian"),
            ("debian", "debian"),
            ("fedora", "rhel"),
            ("rocky", "rhel"),
            ("arch", "arch"),
            ("manjaro", "arch"),
            ("opensuse", "suse"),
            ("alpine", "alpine"),
            ("gentoo", "desconhecida"),
        ],
    )
    def test_familia_pelo_os_release(self, distro: str, family: str) -> None:
        found = build_env.read_linux_distro(
            _reader({"/etc/os-release": OS_RELEASE[distro]})
        )
        assert found.family == family

    def test_le_nome_versao_e_id_like(self) -> None:
        found = build_env.read_linux_distro(
            _reader({"/etc/os-release": OS_RELEASE["pop"]})
        )
        assert (found.id, found.name, found.version) == ("pop", "Pop!_OS", "24.04 LTS")
        assert found.id_like == "ubuntu debian"
        assert found.label == "Pop!_OS 24.04 LTS"

    def test_derivada_desconhecida_usa_id_like(self) -> None:
        assert build_env.distro_family("minhadistro", "arch") == "arch"
        assert build_env.distro_family("minhadistro", "") == "desconhecida"

    def test_usa_usr_lib_quando_etc_nao_existe(self) -> None:
        found = build_env.read_linux_distro(
            _reader({"/usr/lib/os-release": OS_RELEASE["fedora"]})
        )
        assert found.family == "rhel"

    def test_ausente_ou_ilegivel_vira_desconhecida_sem_erro(self) -> None:
        found = build_env.read_linux_distro(_reader({}))
        assert found == build_env.LinuxDistro()
        assert found.family == "desconhecida"
        assert found.label == "distribuição desconhecida"

    def test_parse_aceita_aspas_comentarios_e_linhas_invalidas(self) -> None:
        values = build_env.parse_os_release(
            "# comentário\nNAME='A B'\nID=x\n\nsem-igual\nVERSION=\"1 (c)\"\n"
        )
        assert values == {"NAME": "A B", "ID": "x", "VERSION": "1 (c)"}


class TestWsl:
    def test_variavel_de_ambiente(self) -> None:
        assert build_env.is_wsl({"WSL_DISTRO_NAME": "Ubuntu"}, "Linux") is True

    def test_proc_version(self) -> None:
        assert build_env.is_wsl({}, "Linux 5.15 microsoft-standard-WSL2") is True
        assert build_env.is_wsl({}, "Linux version 6.8.0-generic") is False


class TestDetectarMaquina:
    def test_linux_arm64_debian(self) -> None:
        host = build_env.detect_host(
            system="Linux",
            machine="aarch64",
            os_version="6.8.0",
            read_text=_reader({"/etc/os-release": OS_RELEASE["debian"]}),
            environ={},
            proc_version="Linux version 6.8",
        )
        assert (host.system, host.arch, host.machine) == ("Linux", "arm64", "aarch64")
        assert host.distro is not None and host.distro.family == "debian"
        assert host.wsl is False

    def test_windows_x64_e_arm64(self) -> None:
        x64 = build_env.detect_host(
            system="Windows", machine="AMD64", os_version="11 10.0.26100"
        )
        arm = build_env.detect_host(system="Windows", machine="ARM64", os_version="11")
        assert (x64.system, x64.arch, x64.distro) == ("Windows", "x64", None)
        assert arm.arch == "arm64"

    def test_macos_x64_e_arm64(self) -> None:
        intel = build_env.detect_host(
            system="Darwin", machine="x86_64", os_version="14.5"
        )
        silicon = build_env.detect_host(
            system="Darwin", machine="arm64", os_version="15.1"
        )
        assert (intel.system, intel.arch, intel.is_macos) == ("macOS", "x64", True)
        assert silicon.arch == "arm64"
        assert silicon.distro is None and silicon.wsl is False

    def test_sem_argumentos_descreve_a_maquina_real(self) -> None:
        host = build_env.detect_host()
        assert host.system in {"Linux", "Windows", "macOS"} or host.system
        assert host.arch


class TestFerramentas:
    def test_versao_na_primeira_linha(self) -> None:
        def runner(_command: Sequence[str]) -> str:
            return "uv 0.13.7 (abc 2026-11-01)\noutra linha"

        assert build_env.probe_version(["uv", "--version"], runner) == (
            "uv 0.13.7 (abc 2026-11-01)"
        )
        assert build_env.extract_version("uv 0.13.7 (abc)") == "0.13.7"
        assert build_env.extract_version(None) == ""

    def test_ferramenta_que_nao_roda_vira_none(self) -> None:
        def missing(_command: Sequence[str]) -> str:
            raise FileNotFoundError("uv")

        def failing(_command: Sequence[str]) -> str:
            raise subprocess.TimeoutExpired("uv", 15)

        assert build_env.probe_version(["uv"], missing) is None
        assert build_env.probe_version(["uv"], failing) is None
        assert build_env.probe_version(["uv"], lambda _c: "") is None

    def test_pre_requisitos_obrigatorios(self) -> None:
        ok = {"uv": "uv 0.13", "node": "v24.1.0", "pnpm": "11.5.0"}
        assert build_env.missing_prerequisites(ok) == []
        problems = build_env.missing_prerequisites({**ok, "pnpm": None, "node": ""})
        assert len(problems) == 2
        assert any("pnpm" in problem and "pnpm.io" in problem for problem in problems)
        assert any("node" in problem for problem in problems)


class TestEmpacotamentoLinux:
    def test_sugere_o_comando_da_familia(self) -> None:
        missing, command = build_env.packaging_hint("debian", lambda _tool: None)
        assert missing == ["flatpak", "flatpak-builder", "bsdtar", "fusermount3"]
        assert command == (
            "sudo apt-get install -y flatpak flatpak-builder fuse3 libarchive-tools"
        )
        _missing, dnf = build_env.packaging_hint("rhel", lambda _tool: None)
        assert dnf.startswith("sudo dnf install -y ") and "bsdtar" in dnf
        _missing, pacman = build_env.packaging_hint("arch", lambda _tool: None)
        assert pacman.startswith("sudo pacman -S --needed ") and "libarchive" in pacman

    def test_so_lista_o_que_falta(self) -> None:
        present = {"flatpak", "bsdtar"}
        missing, command = build_env.packaging_hint(
            "suse", lambda tool: "/usr/bin/x" if tool in present else None
        )
        assert missing == ["flatpak-builder", "fusermount3"]
        assert command == "sudo zypper install -y flatpak-builder fuse3"

    def test_familia_desconhecida_nao_sugere_comando(self) -> None:
        missing, command = build_env.packaging_hint("desconhecida", lambda _tool: None)
        assert missing and command == ""

    def test_tudo_presente(self) -> None:
        assert build_env.packaging_hint("debian", lambda _tool: "/usr/bin/x") == (
            [],
            "",
        )


class TestRelatorio:
    VERSIONS = {
        "python": "Python 3.13.18",
        "uv": "uv 0.13.7",
        "node": "v24.2.0",
        "pnpm": "11.5.0",
        "git": "git version 2.45.1",
    }

    def test_linux_com_distro_arquitetura_e_sidecars(self) -> None:
        host = build_env.detect_host(
            system="Linux",
            machine="aarch64",
            os_version="6.8",
            read_text=_reader({"/etc/os-release": OS_RELEASE["pop"]}),
            environ={},
            proc_version="Linux",
        )
        text = build_env.render_environment(
            host,
            self.VERSIONS,
            ["nats-server-v2.10.22-linux-arm64", "ffmpeg/ffprobe (ffmpeg-linux-arm64)"],
            ["flatpak"],
            "sudo apt-get install -y flatpak",
        )
        assert "Linux (Pop!_OS 24.04 LTS; id=pop; família: debian)" in text
        assert "arm64 (aarch64)" in text
        assert "uv 0.13.7" in text and "node 24.2.0" in text
        assert "nats-server-v2.10.22-linux-arm64 · ffmpeg/ffprobe" in text
        assert (
            "ausente (opcional): flatpak; instale com: sudo apt-get install -y flatpak"
            in text
        )
        assert "Swift" not in text

    def test_wsl_aparece_na_linha_do_sistema(self) -> None:
        host = build_env.detect_host(
            system="Linux",
            machine="x86_64",
            os_version="5.15",
            read_text=_reader({"/etc/os-release": OS_RELEASE["ubuntu"]}),
            environ={"WSL_DISTRO_NAME": "Ubuntu"},
        )
        assert "dentro do WSL" in build_env.render_environment(host, self.VERSIONS, [])

    def test_macos_mostra_swift_e_pede_command_line_tools(self) -> None:
        host = build_env.detect_host(
            system="Darwin", machine="arm64", os_version="15.1"
        )
        with_swift = build_env.render_environment(
            host, {**self.VERSIONS, "swiftc": "Apple Swift version 6.0.2"}, []
        )
        without = build_env.render_environment(host, self.VERSIONS, [])
        assert "macOS 15.1" in with_swift and "swiftc 6.0.2" in with_swift
        assert "xcode-select --install" in without
        assert "Empacotamento" not in with_swift

    def test_windows_sem_distro_nem_empacotamento_linux(self) -> None:
        host = build_env.detect_host(
            system="Windows", machine="AMD64", os_version="11 26100"
        )
        text = build_env.render_environment(host, {**self.VERSIONS, "pnpm": None}, [])
        assert "Windows 11 26100" in text and "x64 (AMD64)" in text
        assert "pnpm ausente" in text
        assert "Empacotamento" not in text and "id=" not in text


class TestTabelaFinal:
    COMPONENTS = [
        build_env.Component("Python (.venv)", "ok", "/repo/.venv/bin/python"),
        build_env.Component("nats-server", "ok", "2.10.22 /repo/resources/nats-server"),
        build_env.Component(
            "vectora-speech (fala do macOS)",
            "ignorado",
            "não é macOS (Linux)",
            required=False,
        ),
    ]

    def test_lista_estado_nome_e_detalhe(self) -> None:
        text = build_env.render_components(self.COMPONENTS)
        lines = text.splitlines()
        assert lines[0].lstrip().startswith("ok")
        assert "nats-server" in lines[1] and "2.10.22" in lines[1]
        assert lines[2].lstrip().startswith("ignorado")

    def test_ignorado_nao_reprova_mas_obrigatorio_ausente_sim(self) -> None:
        assert build_env.components_ok(self.COMPONENTS) is True
        broken = [*self.COMPONENTS, build_env.Component("ffmpeg", "ausente", "x")]
        assert build_env.components_ok(broken) is False
        optional = [build_env.Component("opcional", "ausente", "x", required=False)]
        assert build_env.components_ok(optional) is True
        assert "AUSENTE" in build_env.render_components(broken)


class TestContratoDoSConstruct:
    """O ``SConstruct`` não é importável; confere as ligações que importam."""

    def _text(self) -> str:
        return (REPO / "SConstruct").read_text(encoding="utf-8")

    def test_install_e_um_unico_alvo_completo(self) -> None:
        text = self._text()
        assert '_cmd("install", _action_install)' in text
        for step in (
            "uv sync",
            "_ensure_electron_binary",
            "_action_fetch_nats(None, None, None)",
            "_action_fetch_ffmpeg(None, None, None)",
            "_action_build_macos_speech(None, None, None)",
        ):
            assert step in text

    def test_helper_de_fala_so_roda_no_macos_no_install(self) -> None:
        text = self._text()
        match = re.search(r"if host\.is_macos:\s+step\(\s+7,", text)
        assert match is not None
        assert "ignorado: este sistema é" in text

    def test_release_garante_o_helper_antes_do_build_hibrido(self) -> None:
        text = self._text()
        assert '_fetch_speech  = _node("build-macos-speech"' in text
        backend = re.search(r"_build_backend = _node\((.*?)\n\)", text, re.DOTALL)
        assert backend is not None and "_fetch_speech" in backend.group(1)
