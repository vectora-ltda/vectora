#!/usr/bin/env python3
"""Gera `THIRD_PARTY_NOTICES.md` a partir das licenças das dependências.

Uso::

    uv run --with pip-licenses pip-licenses --from=mixed --format=json --with-urls > python.json
    pnpm --dir frontend licenses list --json > npm.json
    uv run python utils/third_party_notices.py python.json npm.json THIRD_PARTY_NOTICES.md

A saída é determinística (ordenada por nome). Licenças que não são claramente
permissivas ou de copyleft fraco/forte conhecido entram na seção "Requer
revisão" para análise humana antes de qualquer distribuição.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_TROVE: dict[str, str] = {
    "MIT License": "MIT",
    "MIT": "MIT",
    "Apache Software License": "Apache-2.0",
    "Apache License 2.0": "Apache-2.0",
    "Apache2.0": "Apache-2.0",
    "BSD License": "BSD",
    "3-Clause BSD License": "BSD-3-Clause",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0",
    "GNU General Public License v3 or later (GPLv3+)": "GPL-3.0",
    "GNU Affero General Public License v3 or later (AGPLv3+)": "AGPL-3.0",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
}

_LANGCHAIN_NOTICE = """MIT License

Copyright (c) 2024 LangChain

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""

_RANK = {"permissive": 0, "weak-copyleft": 1, "strong-copyleft": 2, "review": 3}
_PERMISSIVE = (
    "MIT",
    "BSD",
    "Apache-2.0",
    "ISC",
    "0BSD",
    "PSF-2.0",
    "Unlicense",
    "CC0-1.0",
    "BlueOak-1.0.0",
    "Python-2.0",
    "MIT-0",
    "Zlib",
    "WTFPL",
    "CNRI-Python",
    "CC-BY-4.0",
    "Public Domain",
)
_WEAK_COPYLEFT = ("LGPL-", "MPL-", "EPL-")
_STRONG_COPYLEFT = ("GPL-3.0", "AGPL-3.0")


def normalize_license(raw: str) -> str:
    """Converte nomes de classificador Trove para identificadores SPDX.

    Só a primeira linha vale: alguns pacotes preenchem o campo com o texto
    inteiro da licença. Listas separadas por `;` viram `A AND B`.
    """
    first = raw.strip().splitlines()[0].strip() if raw.strip() else ""
    parts = [
        _TROVE.get(part.strip(), part.strip())
        for part in first.split(";")
        if part.strip() and part.strip() != "DFSG approved"
    ]
    return " AND ".join(parts) if parts else "UNKNOWN"


def _single(part: str) -> str:
    if part.startswith(_PERMISSIVE) and not part.startswith("MPL"):
        return "permissive"
    if part.startswith(_WEAK_COPYLEFT):
        return "weak-copyleft"
    if part in _STRONG_COPYLEFT:
        return "strong-copyleft"
    return "review"


def _strip_exceptions(license_id: str) -> str:
    """Remove `WITH <exceção>`: exceções só afrouxam a licença base."""
    words = license_id.replace("(", " ").replace(")", " ").split()
    kept: list[str] = []
    skip = False
    for word in words:
        if skip:
            skip = False
        elif word == "WITH":
            skip = True
        else:
            kept.append(word)
    return " ".join(kept)


def classify(license_id: str) -> str:
    """Devolve `permissive`, `weak-copyleft`, `strong-copyleft` ou `review`.

    `A OR B` vale o melhor dos dois (quem usa escolhe); `A AND B` vale o pior.
    """
    text = _strip_exceptions(license_id)
    if not text:
        return "review"
    best_of: list[int] = []
    for alternative in text.split(" OR "):
        worst = max(
            _RANK[_single(part.strip())]
            for part in alternative.split(" AND ")
            if part.strip()
        )
        best_of.append(worst)
    rank = min(best_of)
    return next(name for name, value in _RANK.items() if value == rank)


def _rows(
    python: list[dict], npm: dict[str, list[dict]]
) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for item in python:
        if not {"Name", "Version", "License"} <= item.keys():
            msg = f"entrada Python inválida: {item!r}"
            raise ValueError(msg)
        rows.append(
            (
                "Python",
                item["Name"],
                item["Version"],
                normalize_license(item["License"]),
            )
        )
    for license_id, packages in npm.items():
        for package in packages:
            if "name" not in package or not package.get("versions"):
                msg = f"entrada npm inválida: {package!r}"
                raise ValueError(msg)
            rows.append(
                (
                    "npm",
                    package["name"],
                    ", ".join(package["versions"]),
                    normalize_license(license_id),
                )
            )
    return sorted(rows, key=lambda r: (r[0], r[1].lower(), r[2]))


def render(python: list[dict], npm: dict[str, list[dict]]) -> str:
    """Monta o markdown completo."""
    rows = _rows(python, npm)
    by_kind: dict[str, list[tuple[str, str, str, str]]] = {}
    for row in rows:
        by_kind.setdefault(classify(row[3]), []).append(row)

    def table(items: list[tuple[str, str, str, str]]) -> list[str]:
        out = ["| Pacote | Versão | Licença |", "| --- | --- | --- |"]
        out += [f"| {name} | {version} | {lic} |" for _, name, version, lic in items]
        return out

    lines = [
        "# Avisos de terceiros",
        "",
        "O Vectora é distribuído sob a AGPL-3.0 (ver `LICENSE`). Este arquivo lista as",
        "dependências de terceiros, com a licença declarada por cada uma. É gerado por",
        "`utils/third_party_notices.py` a partir de `uv` (Python) e `pnpm` (frontend);",
        "não edite à mão.",
        "",
        "## Resumo",
        "",
        "| Categoria | Pacotes |",
        "| --- | ---: |",
    ]
    for kind, label in (
        ("permissive", "Permissiva"),
        ("weak-copyleft", "Copyleft fraco (LGPL, MPL, EPL)"),
        ("strong-copyleft", "Copyleft forte (GPL-3.0, AGPL-3.0)"),
        ("review", "Requer revisão"),
    ):
        lines.append(f"| {label} | {len(by_kind.get(kind, []))} |")

    lines += ["", "## Requer revisão", ""]
    review = by_kind.get("review", [])
    if review:
        lines += [
            "Licença desconhecida, composta de forma incomum ou com restrição de uso.",
            "Cada item precisa de decisão antes de distribuir um build que o contenha.",
            "",
            *table(review),
        ]
    else:
        lines.append("Nenhum pacote.")

    for kind, title in (
        ("strong-copyleft", "Copyleft forte"),
        ("weak-copyleft", "Copyleft fraco"),
    ):
        lines += ["", f"## {title}", ""]
        lines += table(by_kind[kind]) if by_kind.get(kind) else ["Nenhum pacote."]

    lines += [
        "",
        "## Atribuição de código incorporado",
        "",
        "Parte do frontend pode derivar de um template da LangChain; o aviso abaixo é mantido",
        "por cautela até a proveniência ser confirmada.",
        "",
        "```text",
        _LANGCHAIN_NOTICE,
        "```",
    ]

    lines += ["", "## Permissivas", ""]
    lines += (
        table(by_kind.get("permissive", []))
        if by_kind.get("permissive")
        else ["Nenhum pacote."]
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    """Lê os dois JSONs e grava o markdown."""
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    python = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    npm = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    Path(argv[3]).write_text(render(python, npm), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
