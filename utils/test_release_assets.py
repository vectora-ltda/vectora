"""Testes de utils/release_assets.py: a release anexa só os instaladores."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import release_assets
from release_assets import select_release_assets, stage_release_assets

SAFE_NAME = re.compile(r"^[A-Za-z0-9._+-]+$")

INSTALLERS = [
    "Vectora-0.1.24-win-x64.exe",
    "Vectora-0.1.24-win-x64.msi",
    "Vectora-0.1.24-win-arm64.exe",
    "Vectora-0.1.24-win-arm64.msi",
    "Vectora-0.1.24-mac-arm64.dmg",
    "Vectora-0.1.24-mac-arm64.zip",
    "Vectora-0.1.24-linux-x64.AppImage",
    "Vectora-0.1.24-linux-x64.deb",
    "Vectora-0.1.24-linux-x64.rpm",
    "Vectora-0.1.24-linux-x64.pacman",
    "Vectora-0.1.24-linux-x64.snap",
    "Vectora-0.1.24-linux-x64.flatpak",
    "Vectora-0.1.24-linux-x64.tar.gz",
    "Vectora-0.1.24-linux-x64.pkg.tar.zst",
    "Vectora-0.1.24-linux-aarch64.rpm",
    "Vectora-0.1.24-linux-x86_64.AppImage",
    "Vectora-0.1.24-rc.1-linux-arm64.deb",
    "Vectora-0.1.24-win-x64.exe.blockmap",
    "Vectora-0.1.24-linux-x64.AppImage.blockmap",
    "Vectora-0.1.24-mac-arm64.dmg.blockmap",
]


def _touch(root: Path, relative: str, size: int = 1) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_selects_every_installer_format_including_nested_artifact_folders(
    tmp_path: Path,
) -> None:
    """Verifica formatos de instalador e busca em diretórios aninhados."""
    for index, name in enumerate(INSTALLERS):
        _touch(tmp_path, name if index % 2 else f"vectora-linux-x64/{name}")

    selection = select_release_assets(tmp_path)

    assert sorted(path.name for path in selection.assets) == sorted(INSTALLERS)
    assert selection.oversized == ()


def test_ignores_everything_that_is_not_an_installer(tmp_path: Path) -> None:
    """Metadados e binários auxiliares não entram nos anexos da release."""
    _touch(tmp_path, "Vectora-0.1.24-win-x64.exe")
    for noise in (
        "win-unpacked/Vectora.exe",
        "win-unpacked/resources/nats-server.exe",
        "Vectora-0.1.24-win-x64.exe.asc",
        "latest.yml",
        "latest-linux.yml",
        "builder-debug.yml",
        "SHA256SUMS",
        "Vectora.exe",
    ):
        _touch(tmp_path, noise)

    selection = select_release_assets(tmp_path)

    assert [path.name for path in selection.assets] == ["Vectora-0.1.24-win-x64.exe"]


def test_blockmaps_of_installers_are_attached_for_differential_updates(
    tmp_path: Path,
) -> None:
    _touch(tmp_path, "Vectora-0.1.24-win-x64.exe")
    _touch(tmp_path, "Vectora-0.1.24-win-x64.exe.blockmap")
    _touch(tmp_path, "Vectora-0.1.24-win-x64.blockmap")  # sem instalador correspondente

    selection = select_release_assets(tmp_path)

    assert [path.name for path in selection.assets] == [
        "Vectora-0.1.24-win-x64.exe",
        "Vectora-0.1.24-win-x64.exe.blockmap",
    ]


REJECTED_NAMES = [
    "Vectora-0.1.24-win-x64[1].exe",
    "Vectora-0.1.24-win-x64*.exe",
    "Vectora-0.1.24-win-x64?.exe",
    "Vectora-0.1.24-win-x64 copy.exe",
    "Vectora-0.1.24-win-x64.exe.part",
    "Vectora-0.1.24-win-x64.blockmap",
    "Vectora-0.1.24-win-x64.exe.blockmap.asc",
    "Vectora-0.1.24-win-x64.exe.asc",
    "Vectora-latest-win-x64.exe",
    "vectora-0.1.24-win-x64.exe",
    "Vectora-0.1.24-freebsd-x64.deb",
    "[Content_Types].xml",
    "latest.yml",
    "SHA256SUMS",
]


@pytest.mark.parametrize("name", REJECTED_NAMES)
def test_rejects_names_outside_the_installer_pattern(name: str) -> None:
    assert not release_assets.is_installer_name(name)


@pytest.mark.parametrize("name", INSTALLERS)
def test_accepts_every_known_installer_name(name: str) -> None:
    assert release_assets.is_installer_name(name)


def test_files_with_rejected_names_are_not_selected_from_disk(tmp_path: Path) -> None:
    """Confirma que arquivos fora do padrão de nome não entram na seleção."""
    for name in REJECTED_NAMES:
        if not any(char in name for char in "*?"):  # o Windows não cria esses nomes
            _touch(tmp_path, name)

    assert select_release_assets(tmp_path).assets == ()


def test_selected_names_never_contain_glob_characters(tmp_path: Path) -> None:
    """Garante que nomes selecionados não acionam glob no upload da release."""
    for name in INSTALLERS:
        _touch(tmp_path, name)

    names = [path.name for path in select_release_assets(tmp_path).assets]

    assert names
    assert all(SAFE_NAME.fullmatch(name) for name in names)


def test_files_above_the_github_limit_are_reported_and_not_attached(
    tmp_path: Path,
) -> None:
    small = _touch(tmp_path, "Vectora-0.1.24-win-x64.exe", size=10)
    big = _touch(tmp_path, "Vectora-0.1.24-linux-x64.AppImage", size=100)

    selection = select_release_assets(tmp_path, max_bytes=50)

    assert selection.assets == (small,)
    assert selection.oversized == (big,)


def test_the_default_limit_is_the_two_gibibytes_github_accepts() -> None:
    assert release_assets.GITHUB_ASSET_LIMIT_BYTES == 2 * 1024**3


def test_two_installers_with_the_same_name_are_rejected(tmp_path: Path) -> None:
    """Anexos são identificados pelo nome; o segundo sobrescreveria o primeiro."""
    _touch(tmp_path, "vectora-linux-x64/Vectora-0.1.24-linux-x64.deb")
    _touch(tmp_path, "vectora-linux-arm64/Vectora-0.1.24-linux-x64.deb")

    with pytest.raises(ValueError, match="mesmo nome"):
        select_release_assets(tmp_path)


def test_missing_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        select_release_assets(tmp_path / "nao-existe")


def test_stage_moves_only_the_installers_into_a_flat_directory(tmp_path: Path) -> None:
    source = tmp_path / "dist-release"
    destination = tmp_path / "release-assets"
    _touch(source, "vectora-win-x64/Vectora-0.1.24-win-x64.exe", size=7)
    _touch(source, "vectora-win-x64/latest.yml")

    selection = stage_release_assets(source, destination)

    assert [path.name for path in selection.assets] == ["Vectora-0.1.24-win-x64.exe"]
    assert [path.name for path in destination.iterdir()] == [
        "Vectora-0.1.24-win-x64.exe"
    ]
    assert (destination / "Vectora-0.1.24-win-x64.exe").stat().st_size == 7
    assert (source / "vectora-win-x64/latest.yml").exists()
    assert not (source / "vectora-win-x64/Vectora-0.1.24-win-x64.exe").exists()


def test_stage_refuses_a_destination_that_already_has_files(tmp_path: Path) -> None:
    source = tmp_path / "dist-release"
    destination = tmp_path / "release-assets"
    _touch(source, "Vectora-0.1.24-win-x64.exe")
    _touch(destination, "SHA256SUMS")

    with pytest.raises(ValueError, match="vazio"):
        stage_release_assets(source, destination)


def test_main_fails_when_no_installer_is_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _touch(tmp_path / "dist-release", "vectora-win-x64/latest.yml")

    code = release_assets.main(
        ["stage", str(tmp_path / "dist-release"), str(tmp_path / "release-assets")]
    )

    assert code == 1
    assert "Nenhum instalador" in capsys.readouterr().err


def test_main_reports_oversized_files_as_warnings_and_still_succeeds(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "dist-release"
    _touch(source, "Vectora-0.1.24-win-x64.exe", size=10)
    _touch(source, "Vectora-0.1.24-linux-x64.AppImage", size=100)
    monkeypatch.setattr(release_assets, "GITHUB_ASSET_LIMIT_BYTES", 50)

    code = release_assets.main(["stage", str(source), str(tmp_path / "release-assets")])

    assert code == 0
    err = capsys.readouterr().err
    assert "::warning::" in err
    assert "Vectora-0.1.24-linux-x64.AppImage" in err
    assert [p.name for p in (tmp_path / "release-assets").iterdir()] == [
        "Vectora-0.1.24-win-x64.exe"
    ]


def test_main_rejects_unknown_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert release_assets.main([]) == 2
    assert "uso" in capsys.readouterr().err.lower()
