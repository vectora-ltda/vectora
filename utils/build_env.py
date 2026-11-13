"""Detecção do ambiente e relatório do ``scons install``.

O primeiro passo do ``install`` descreve a máquina (sistema, arquitetura,
distribuição Linux, ferramentas e sidecars que serão baixados), e o último
confere o que foi instalado. A lógica fica aqui, fora do ``SConstruct``, para
ser coberta por testes sem rodar o SCons.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess  # nosec B404 - só executa ferramentas de versão conhecidas
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: Sistemas que o ``install`` conhece, pelo nome de ``platform.system()``.
SYSTEM_NAMES = {"Linux": "Linux", "Windows": "Windows", "Darwin": "macOS"}

_ARCH_ALIASES = {
    "x86_64": "x64",
    "amd64": "x64",
    "x64": "x64",
    "arm64": "arm64",
    "aarch64": "arm64",
}

#: ``ID`` e ``ID_LIKE`` do ``os-release`` agrupados pelo gerenciador de pacotes.
_FAMILIES: dict[str, frozenset[str]] = {
    "debian": frozenset(
        {
            "debian",
            "ubuntu",
            "pop",
            "linuxmint",
            "elementary",
            "raspbian",
            "kali",
            "zorin",
            "neon",
            "mx",
            "devuan",
        }
    ),
    "rhel": frozenset(
        {
            "rhel",
            "centos",
            "fedora",
            "rocky",
            "almalinux",
            "ol",
            "amzn",
            "nobara",
        }
    ),
    "arch": frozenset(
        {"arch", "archarm", "manjaro", "endeavouros", "cachyos", "garuda"}
    ),
    "suse": frozenset(
        {"suse", "sles", "sled", "opensuse", "opensuse-leap", "opensuse-tumbleweed"}
    ),
    "alpine": frozenset({"alpine"}),
}

UNKNOWN_FAMILY = "desconhecida"

#: Pacotes de sistema do empacotamento Linux (``release``) por ferramenta, e o
#: nome do pacote em cada família.
PACKAGING_TOOLS: dict[str, dict[str, str]] = {
    "flatpak": {
        "debian": "flatpak",
        "rhel": "flatpak",
        "arch": "flatpak",
        "suse": "flatpak",
        "alpine": "flatpak",
    },
    "flatpak-builder": {
        "debian": "flatpak-builder",
        "rhel": "flatpak-builder",
        "arch": "flatpak-builder",
        "suse": "flatpak-builder",
        "alpine": "flatpak-builder",
    },
    "bsdtar": {
        "debian": "libarchive-tools",
        "rhel": "bsdtar",
        "arch": "libarchive",
        "suse": "bsdtar",
        "alpine": "libarchive-tools",
    },
    "fusermount3": {
        "debian": "fuse3",
        "rhel": "fuse3",
        "arch": "fuse3",
        "suse": "fuse3",
        "alpine": "fuse3",
    },
}

_INSTALL_COMMANDS = {
    "debian": "sudo apt-get install -y",
    "rhel": "sudo dnf install -y",
    "arch": "sudo pacman -S --needed",
    "suse": "sudo zypper install -y",
    "alpine": "sudo apk add",
}


@dataclass(frozen=True, slots=True)
class LinuxDistro:
    """Distribuição Linux lida do ``os-release``."""

    id: str = ""
    name: str = ""
    version: str = ""
    id_like: str = ""
    family: str = UNKNOWN_FAMILY

    @property
    def label(self) -> str:
        """Nome e versão para exibição."""
        if not self.name:
            return "distribuição desconhecida"
        return f"{self.name} {self.version}".strip()


@dataclass(frozen=True, slots=True)
class HostInfo:
    """Sistema, arquitetura e distribuição da máquina."""

    system: str
    os_version: str
    arch: str
    machine: str
    distro: LinuxDistro | None = None
    wsl: bool = False

    @property
    def is_macos(self) -> bool:
        return self.system == "macOS"


@dataclass(frozen=True, slots=True)
class Component:
    """Um item instalado: ``ok``, ``ignorado`` (não se aplica) ou ``ausente``."""

    name: str
    state: str
    detail: str = ""
    required: bool = True


@dataclass(slots=True)
class Report:
    """Linhas do relatório do primeiro passo."""

    lines: list[str] = field(default_factory=list)

    def add(self, label: str, value: str) -> None:
        self.lines.append(f"  {label + ':':<15}{value}")

    def render(self) -> str:
        return "\n".join(self.lines)


def normalize_arch(machine: str) -> str:
    """``x86_64``/``AMD64`` viram ``x64``; ``aarch64``/``arm64`` viram ``arm64``."""
    value = machine.strip().lower()
    return _ARCH_ALIASES.get(value, value or "desconhecida")


def parse_os_release(text: str) -> dict[str, str]:
    """Lê ``CHAVE=valor`` do ``os-release``, com ou sem aspas."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def distro_family(distro_id: str, id_like: str = "") -> str:
    """Família da distribuição, pelo ``ID`` e depois pelos itens de ``ID_LIKE``."""
    candidates = [distro_id.lower(), *id_like.lower().split()]
    for candidate in candidates:
        for family, members in _FAMILIES.items():
            if candidate in members or (
                family == "suse" and candidate.startswith("opensuse")
            ):
                return family
    return UNKNOWN_FAMILY


def read_linux_distro(
    read_text: Callable[[str], str] | None = None,
) -> LinuxDistro:
    """Lê ``/etc/os-release`` (ou ``/usr/lib/os-release``); ausente vira desconhecida."""

    def default_reader(path: str) -> str:
        return Path(path).read_text(encoding="utf-8", errors="replace")

    reader = read_text or default_reader
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            values = parse_os_release(reader(path))
        except OSError:
            continue
        distro_id = values.get("ID", "")
        id_like = values.get("ID_LIKE", "")
        return LinuxDistro(
            id=distro_id,
            name=values.get("NAME", distro_id),
            version=values.get("VERSION", values.get("VERSION_ID", "")),
            id_like=id_like,
            family=distro_family(distro_id, id_like),
        )
    return LinuxDistro()


def is_wsl(
    environ: Mapping[str, str] | None = None,
    proc_version: str | None = None,
) -> bool:
    """Roda dentro do WSL (Windows Subsystem for Linux)?"""
    env = os.environ if environ is None else environ
    if env.get("WSL_DISTRO_NAME") or env.get("WSL_INTEROP"):
        return True
    if proc_version is None:
        try:
            proc_version = Path("/proc/version").read_text(encoding="utf-8")
        except OSError:
            return False
    return "microsoft" in proc_version.lower()


def detect_host(
    *,
    system: str | None = None,
    machine: str | None = None,
    os_version: str | None = None,
    read_text: Callable[[str], str] | None = None,
    environ: Mapping[str, str] | None = None,
    proc_version: str | None = None,
) -> HostInfo:
    """Descreve a máquina; todos os argumentos existem para testes."""
    raw_system = system or platform.system()
    raw_machine = machine or platform.machine()
    name = SYSTEM_NAMES.get(raw_system, raw_system or "desconhecido")
    if os_version is None:
        if raw_system == "Darwin":
            os_version = platform.mac_ver()[0]
        elif raw_system == "Windows":
            os_version = " ".join(
                part for part in (platform.release(), platform.version()) if part
            )
        else:
            os_version = platform.release()
    distro = read_linux_distro(read_text) if raw_system == "Linux" else None
    wsl = raw_system == "Linux" and is_wsl(environ, proc_version)
    return HostInfo(
        system=name,
        os_version=os_version,
        arch=normalize_arch(raw_machine),
        machine=raw_machine,
        distro=distro,
        wsl=wsl,
    )


def probe_version(
    command: Sequence[str],
    runner: Callable[[Sequence[str]], str] | None = None,
) -> str | None:
    """Primeira linha da saída de ``<ferramenta> --version``; ``None`` se não roda."""

    def default_runner(argv: Sequence[str]) -> str:
        completed = subprocess.run(  # noqa: S603  # nosec B603 - ferramenta conhecida
            list(argv),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if completed.returncode != 0:
            raise OSError(completed.stderr.strip())
        return completed.stdout or completed.stderr

    try:
        output = (runner or default_runner)(command)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = output.strip().splitlines()
    return lines[0].strip() if lines else None


def extract_version(text: str | None) -> str:
    """Número de versão dentro de uma linha de saída (``uv 0.13.7`` vira ``0.13.7``)."""
    if not text:
        return ""
    match = re.search(r"\d+(?:\.\d+)+", text)
    return match.group(0) if match else text.strip()


#: Ferramentas sem as quais o ``install`` não funciona, e como obtê-las.
REQUIRED_TOOLS: dict[str, str] = {
    "uv": "https://docs.astral.sh/uv/getting-started/installation/",
    "node": "https://nodejs.org/ (veja a versão em frontend/package.json)",
    "pnpm": "https://pnpm.io/installation",
}


def missing_prerequisites(versions: Mapping[str, str | None]) -> list[str]:
    """Mensagens para cada ferramenta obrigatória que não respondeu."""
    return [
        f"{tool} não encontrado: instale em {url}"
        for tool, url in REQUIRED_TOOLS.items()
        if not versions.get(tool)
    ]


def packaging_hint(
    family: str, which: Callable[[str], str | None] = shutil.which
) -> tuple[list[str], str]:
    """Ferramentas de empacotamento Linux ausentes e o comando para instalá-las."""
    missing = [tool for tool in PACKAGING_TOOLS if which(tool) is None]
    command = _INSTALL_COMMANDS.get(family)
    if not missing or command is None:
        return missing, ""
    packages = sorted({PACKAGING_TOOLS[tool][family] for tool in missing})
    return missing, f"{command} {' '.join(packages)}"


def describe_system(host: HostInfo) -> str:
    """Linha de sistema do relatório."""
    if host.system == "Linux" and host.distro is not None:
        distro = host.distro
        details = f"id={distro.id or '?'}; família: {distro.family}"
        text = f"Linux ({distro.label}; {details})"
        return f"{text}, dentro do WSL" if host.wsl else text
    if host.os_version:
        return f"{host.system} {host.os_version}"
    return host.system


def render_environment(
    host: HostInfo,
    versions: Mapping[str, str | None],
    sidecars: Sequence[str],
    packaging_missing: Sequence[str] = (),
    packaging_command: str = "",
) -> str:
    """Texto do primeiro passo: o que foi detectado e o que será baixado."""
    report = Report()
    report.add("Sistema", describe_system(host))
    arch = host.arch if host.arch == host.machine else f"{host.arch} ({host.machine})"
    report.add("Arquitetura", arch)
    tools = "   ".join(
        f"{tool} {extract_version(versions.get(tool)) or 'ausente'}"
        for tool in ("python", "uv", "node", "pnpm", "git")
        if tool in versions
    )
    report.add("Ferramentas", tools)
    if host.is_macos:
        swift = extract_version(versions.get("swiftc"))
        report.add(
            "Swift",
            f"swiftc {swift}" if swift else "swiftc ausente (xcode-select --install)",
        )
    report.add("Sidecars", " · ".join(sidecars) if sidecars else "nenhum")
    if host.system == "Linux":
        if packaging_missing:
            hint = f"; instale com: {packaging_command}" if packaging_command else ""
            report.add(
                "Empacotamento",
                f"ausente (opcional): {', '.join(packaging_missing)}{hint}",
            )
        else:
            report.add("Empacotamento", "ferramentas Linux presentes")
    return report.render()


def render_components(components: Sequence[Component]) -> str:
    """Tabela final do ``install``."""
    width = max((len(c.name) for c in components), default=0)
    marks = {"ok": "ok       ", "ignorado": "ignorado ", "ausente": "AUSENTE   "}
    rows = [
        f"  {marks.get(c.state, c.state):<10}{c.name:<{width}}  {c.detail}".rstrip()
        for c in components
    ]
    return "\n".join(rows)


def components_ok(components: Sequence[Component]) -> bool:
    """Falso se algum componente obrigatório está ausente."""
    return not any(c.state == "ausente" and c.required for c in components)
