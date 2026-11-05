#!/usr/bin/env python3
"""Compara o SARIF do ``snyk code test`` com a linha de base de achados aceitos.

O Snyk Code não aplica arquivo de política nem comentários de supressão, então
os achados já analisados e aceitos ficam em ``.github/snyk-code-baseline.json``,
cada um com motivo e prazo. A verificação falha para qualquer achado fora da
linha de base e avisa sobre entradas vencidas ou que não aparecem mais.

Uso: ``snyk_baseline.py <sarif> <baseline.json>``
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


def _line_digest(root: Path, uri: str, line: int) -> str:
    """Hash do texto da linha apontada; muda se a linha mudar, não se ela se mover."""
    try:
        lines = (root / uri).read_text(encoding="utf-8").splitlines()
        text = lines[line - 1].strip()
    except (OSError, IndexError, UnicodeDecodeError):
        text = ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def finding_key(result: dict[str, Any], root: Path) -> str:
    """Identidade estável do achado: regra, arquivo e hash da linha apontada."""
    location = result["locations"][0]["physicalLocation"]
    uri = location["artifactLocation"]["uri"]
    line = location.get("region", {}).get("startLine", 0)
    return f"{result['ruleId']}|{uri}|{_line_digest(root, uri, line)}"


def load_findings(sarif_path: Path, root: Path) -> dict[str, dict[str, Any]]:
    """Achados do SARIF por chave; linhas idênticas no mesmo arquivo ganham #n."""
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    results = [
        result for run in sarif.get("runs", []) for result in run.get("results", [])
    ]

    def position(result: dict[str, Any]) -> tuple[str, int]:
        location = result["locations"][0]["physicalLocation"]
        return location["artifactLocation"]["uri"], location.get("region", {}).get(
            "startLine", 0
        )

    findings: dict[str, dict[str, Any]] = {}
    seen: dict[str, int] = {}
    for result in sorted(results, key=position):
        base = finding_key(result, root)
        seen[base] = seen.get(base, 0) + 1
        key = base if seen[base] == 1 else f"{base}#{seen[base]}"
        findings[key] = result
    return findings


def compare(
    findings: dict[str, dict[str, Any]],
    baseline: list[dict[str, str]],
    today: date,
) -> tuple[list[str], list[str], list[str]]:
    """Devolve (novos, vencidos, ausentes).

    Um achado coberto por entrada vencida conta como novo.
    """
    accepted = {entry["key"]: entry for entry in baseline}
    new: list[str] = []
    expired: list[str] = []
    for key in findings:
        entry = accepted.get(key)
        if entry is None:
            new.append(key)
        elif date.fromisoformat(entry["expires"]) < today:
            expired.append(key)
            new.append(key)
    missing = [key for key in accepted if key not in findings]
    return new, expired, missing


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    findings = load_findings(Path(argv[1]), Path.cwd())
    baseline = json.loads(Path(argv[2]).read_text(encoding="utf-8"))["accepted"]
    new, expired, missing = compare(findings, baseline, datetime.now(UTC).date())
    for key in missing:
        print(f"::warning::entrada da linha de base sem achado correspondente: {key}")
    for key in expired:
        print(f"::error::entrada da linha de base vencida: {key}")
    for key in new:
        message = findings[key]["message"]["text"]
        print(f"::error::achado do Snyk Code fora da linha de base: {key}: {message}")
    if new:
        return 1
    print(f"Snyk Code: {len(findings)} achado(s), todos na linha de base.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
