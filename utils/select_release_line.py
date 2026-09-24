"""Seleciona uma linha de release ativa para os workflows do GitHub Actions."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class ReleaseLine(BaseModel):
    """Metadados de branch e milestone de uma linha de release ativa."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    branch: str
    milestone: str
    release_please_config: str
    release_please_manifest: str


class ReleaseLines(BaseModel):
    """Linhas configuradas de desenvolvimento e manutenção."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    development: ReleaseLine
    maintenance: ReleaseLine


CONFIG_PATH: Path = Path(__file__).parents[1] / ".github" / "release-lines.json"


def load_release_lines(path: Path = CONFIG_PATH) -> ReleaseLines:
    """Carrega e valida a configuração versionada das linhas de release."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        config = ReleaseLines.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid release-line configuration: {exc}") from exc
    if config.development.branch == config.maintenance.branch:
        raise ValueError("development and maintenance branches must differ")
    return config


def selection_for_branch(branch: str | None, config: ReleaseLines) -> dict[str, str]:
    """Retorna as saídas do workflow para uma branch, incluindo o indicador de habilitação."""
    selected = next(
        (
            line
            for line in (config.development, config.maintenance)
            if line.branch == branch
        ),
        None,
    )
    outputs = {
        "enabled": "true" if selected else "false",
        "maintenance_branch": config.maintenance.branch,
        "development_branch": config.development.branch,
        "development_milestone": config.development.milestone,
        "config_file": selected.release_please_config if selected else "",
        "manifest_file": selected.release_please_manifest if selected else "",
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
