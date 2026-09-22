"""Seleciona uma linha de release ativa para os workflows do GitHub Actions."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TypedDict


class ReleaseLine(TypedDict):
    """Metadados de branch e milestone de uma linha de release ativa."""

    branch: str
    milestone: str


class ReleaseLines(TypedDict):
    """Linhas configuradas de desenvolvimento e manutenção."""

    development: ReleaseLine
    maintenance: ReleaseLine


CONFIG_PATH: Path = Path(__file__).parents[1] / ".github" / "release-lines.json"


def load_release_lines(path: Path = CONFIG_PATH) -> ReleaseLines:
    """Carrega e valida a configuração versionada das linhas de release."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("release-line configuration must be an object")
    result: dict[str, ReleaseLine] = {}
    for key in ("development", "maintenance"):
        line = payload.get(key)
        if not isinstance(line, dict):
            raise ValueError(f"missing release line: {key}")
        branch = line.get("branch")
        milestone = line.get("milestone")
        if not isinstance(branch, str) or not branch:
            raise ValueError(f"invalid branch for release line: {key}")
        if not isinstance(milestone, str) or not milestone:
            raise ValueError(f"invalid milestone for release line: {key}")
        result[key] = {"branch": branch, "milestone": milestone}
    if result["development"]["branch"] == result["maintenance"]["branch"]:
        raise ValueError("development and maintenance branches must differ")
    return {"development": result["development"], "maintenance": result["maintenance"]}


def selection_for_branch(branch: str | None, config: ReleaseLines) -> dict[str, str]:
    """Retorna as saídas do workflow para uma branch, incluindo o indicador de habilitação."""
    selected = next(
        (line for line in config.values() if line["branch"] == branch),
        None,
    )
    outputs = {
        "enabled": "true" if selected else "false",
        "maintenance_branch": config["maintenance"]["branch"],
        "development_branch": config["development"]["branch"],
        "development_milestone": config["development"]["milestone"],
    }
    return outputs


def main() -> int:
    """Imprime as saídas do GitHub Actions para a branch informada."""
    if len(sys.argv) != 2:
        print("usage: select_release_line.py BRANCH", file=sys.stderr)
        return 2
    try:
        outputs = selection_for_branch(sys.argv[1], load_release_lines())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid release-line configuration: {exc}", file=sys.stderr)
        return 1
    for key, value in outputs.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
