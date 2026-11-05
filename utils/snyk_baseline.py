#!/usr/bin/env python3
"""Compara o SARIF do ``snyk code test`` com a linha de base de achados aceitos.

O Snyk Code não aplica arquivo de política nem comentários de supressão, então
os achados já analisados e aceitos ficam em ``.github/snyk-code-baseline.json``,
cada um com motivo e prazo. A verificação falha para qualquer achado fora da
linha de base e avisa sobre entradas vencidas ou que não aparecem mais.

Uso: ``snyk_baseline.py <sarif> <baseline.json>``
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


def finding_key(result: dict[str, Any]) -> str:
    """Identidade estável do achado: regra, arquivo e hash da linha."""
    location = result["locations"][0]["physicalLocation"]
    uri = location["artifactLocation"]["uri"]
    line_hash = (result.get("partialFingerprints") or {}).get(
        "primaryLocationLineHash", ""
    )
    return f"{result['ruleId']}|{uri}|{line_hash}"


def load_findings(sarif_path: Path) -> dict[str, dict[str, Any]]:
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    findings: dict[str, dict[str, Any]] = {}
    for run in sarif.get("runs", []):
        for result in run.get("results", []):
            findings[finding_key(result)] = result
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
    findings = load_findings(Path(argv[1]))
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
