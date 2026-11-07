#!/usr/bin/env python3
"""Seleciona os arquivos que a GitHub Release anexa: instaladores e seus blockmaps.

Uso: `python utils/release_assets.py stage ORIGEM DESTINO`

Move para a pasta plana DESTINO apenas os instaladores (e os `.blockmap` deles,
que o `electron-updater` usa nas atualizações diferenciais) encontrados em
ORIGEM, e deixa tudo o mais onde estava. O nome do arquivo é a única regra de
seleção; ela só admite letras, números e `._+-`, então um arquivo de runtime
(como os `[Content_Types].xml` de uma pasta do PyInstaller), um manifesto de
atualização ou uma assinatura nunca chegam ao `gh release upload`, que trata
colchetes e asteriscos como glob.

Só depende da stdlib, para rodar no runner sem instalar nada.
"""

from __future__ import annotations

import re
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

#: Tamanho máximo de um anexo de release no GitHub.
GITHUB_ASSET_LIMIT_BYTES = 2 * 1024**3

_INSTALLER_NAME = re.compile(
    r"^Vectora-\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)*-(?:win|mac|linux)-[A-Za-z0-9_]+"
    r"\.(?:exe|msi|dmg|zip|AppImage|deb|rpm|pacman|snap|flatpak|tar\.gz|pkg\.tar\.zst)"
    r"(?:\.blockmap)?$"
)


def is_installer_name(name: str) -> bool:
    """Diz se `name` é o nome de um instalador (ou blockmap) que a release anexa."""
    return _INSTALLER_NAME.fullmatch(name) is not None


@dataclass(frozen=True)
class Selection:
    """Resultado de uma seleção: o que anexar e o que passou do limite do GitHub."""

    assets: tuple[Path, ...]
    oversized: tuple[Path, ...]


def select_release_assets(root: Path, *, max_bytes: int | None = None) -> Selection:
    """Lista os instaladores de `root`, em qualquer profundidade, em ordem estável.

    Levanta `FileNotFoundError` se `root` não for um diretório e `ValueError` se
    dois instaladores tiverem o mesmo nome, porque o anexo é identificado pelo
    nome e o segundo substituiria o primeiro.
    """
    limit = GITHUB_ASSET_LIMIT_BYTES if max_bytes is None else max_bytes
    if not root.is_dir():
        raise FileNotFoundError(f"Diretório de artefatos inexistente: {root}")

    by_name: dict[str, Path] = {}
    assets: list[Path] = []
    oversized: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if not is_installer_name(path.name):
            continue
        if path.name in by_name:
            raise ValueError(
                f"Dois instaladores com o mesmo nome de anexo: {by_name[path.name]} e {path}"
            )
        by_name[path.name] = path
        if path.stat().st_size > limit:
            oversized.append(path)
        else:
            assets.append(path)
    return Selection(tuple(assets), tuple(oversized))


def stage_release_assets(
    source: Path, destination: Path, *, max_bytes: int | None = None
) -> Selection:
    """Move os instaladores de `source` para a pasta plana `destination`."""
    selection = select_release_assets(source, max_bytes=max_bytes)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError(f"O diretório de destino precisa estar vazio: {destination}")
    for path in selection.assets:
        shutil.move(str(path), str(destination / path.name))
    return selection


def main(argv: Sequence[str] | None = None) -> int:
    """Valida argumentos, prepara anexos elegíveis e informa falhas ao runner."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3 or args[0] != "stage":
        print("uso: release_assets.py stage ORIGEM DESTINO", file=sys.stderr)
        return 2
    try:
        selection = stage_release_assets(
            Path(args[1]),
            Path(args[2]),
            max_bytes=GITHUB_ASSET_LIMIT_BYTES,
        )
    except (FileNotFoundError, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    for path in selection.oversized:
        print(
            f"::warning::Instalador acima de 2 GiB, não será anexado à release: {path.name}",
            file=sys.stderr,
        )
    if not selection.assets:
        print(f"::error::Nenhum instalador encontrado em {args[1]}.", file=sys.stderr)
        return 1
    print(
        f"{len(selection.assets)} instalador(es) selecionado(s) para a release.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
