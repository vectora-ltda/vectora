#!/usr/bin/env python3
"""Extrai do CHANGELOG.md a seção de uma versão, para o corpo da GitHub Release.

Uso: `python utils/release_notes.py CHANGELOG.md VERSAO`

Imprime a seção `## [VERSAO] (data)` sem o título (a release já tem um) e sai com
código 1 se o arquivo ou a seção não existirem ou a seção estiver vazia; quem
chama decide o que fazer (o workflow cai para as notas geradas pelo GitHub).

Só depende da stdlib, para rodar no runner sem instalar nada.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path


def section_for_version(changelog: str, version: str) -> str | None:
    """Corpo da seção de `version`, até o próximo título de segundo nível."""
    heading = re.compile(rf"^## \[{re.escape(version)}\][^\n]*$", re.MULTILINE)
    match = heading.search(changelog)
    if match is None:
        return None
    following = re.compile(r"^## ", re.MULTILINE).search(changelog, match.end())
    end = following.start() if following else len(changelog)
    body = changelog[match.end() : end].strip()
    return body or None


def main(argv: Sequence[str] | None = None) -> int:
    """Imprime as notas da versão pedida ou retorna erro para o workflow."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("uso: release_notes.py CHANGELOG.md VERSAO", file=sys.stderr)
        return 2
    path = Path(args[0])
    if not path.is_file():
        print(f"Arquivo não encontrado: {path}", file=sys.stderr)
        return 1
    section = section_for_version(path.read_text(encoding="utf-8"), args[1])
    if section is None:
        print(f"{path} não tem uma seção para a versão {args[1]}.", file=sys.stderr)
        return 1
    print(section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
