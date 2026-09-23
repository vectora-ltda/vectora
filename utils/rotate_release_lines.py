"""Calcula e aplica a próxima rotação das linhas de release."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Literal, TypedDict

from select_release_line import ReleaseLines, load_release_lines

SEMVER_TAG: re.Pattern[str] = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.0$"
)


class Rotation(TypedDict):
    """Metadados necessários para rotacionar as linhas de release ativas."""

    release_tag: str
    release_version: str
    maintenance_branch: str
    maintenance_milestone: str
    development_branch: str
    development_milestone: str
    previous_maintenance_branch: str
    previous_maintenance_milestone: str
    previous_development_milestone: str


class OpenPullRequest(TypedDict):
    """Metadados relevantes de uma pull request aberta durante a rotação."""

    number: int
    base_branch: str
    milestone: str | None


class RotationOperation(TypedDict):
    """Uma operação determinística executada pelo workflow de rotação do GitHub."""

    kind: Literal[
        "create_branch",
        "ensure_milestone",
        "update_base",
        "update_milestone",
        "publish_config",
    ]
    pull_request: int | None
    value: str


def build_rotation_plan(
    rotation: Rotation,
    pull_requests: list[OpenPullRequest],
) -> list[RotationOperation]:
    """Monta as operações de branch, milestone, PR e configuração de uma rotação.

    Manter essa fronteira de decisão pura torna o workflow que chama a API
    testável sem contato com o GitHub. O workflow continua responsável por
    aplicar cada operação e falhar quando uma chamada à API não puder ser concluída.
    """
    plan: list[RotationOperation] = [
        {
            "kind": "create_branch",
            "pull_request": None,
            "value": rotation["maintenance_branch"],
        },
        {
            "kind": "ensure_milestone",
            "pull_request": None,
            "value": rotation["maintenance_milestone"],
        },
        {
            "kind": "ensure_milestone",
            "pull_request": None,
            "value": rotation["development_milestone"],
        },
    ]
    for pull_request in pull_requests:
        maintenance_match = (
            pull_request["base_branch"] == rotation["previous_maintenance_branch"]
        )
        development_match = (
            pull_request["milestone"] == rotation["previous_development_milestone"]
        )
        if not maintenance_match and not development_match:
            continue
        if maintenance_match:
            plan.append(
                {
                    "kind": "update_base",
                    "pull_request": pull_request["number"],
                    "value": rotation["maintenance_branch"],
                }
            )
        if maintenance_match and (
            pull_request["milestone"] == rotation["previous_maintenance_milestone"]
        ):
            milestone = rotation["maintenance_milestone"]
        elif development_match:
            milestone = rotation["development_milestone"]
        else:
            milestone = None
        if milestone is not None:
            plan.append(
                {
                    "kind": "update_milestone",
                    "pull_request": pull_request["number"],
                    "value": milestone,
                }
            )
    plan.append(
        {
            "kind": "publish_config",
            "pull_request": None,
            "value": rotation["release_tag"],
        }
    )
    return plan


def rotation_for_release(
    tag: str, config: ReleaseLines, target_branch: str | None = None
) -> Rotation | None:
    """Retorna as próximas linhas quando ``tag`` encerra a linha configurada de desenvolvimento."""
    match = SEMVER_TAG.fullmatch(tag)
    if match is None:
        return None
    if target_branch != config.development.branch and not (
        target_branch is not None
        and re.fullmatch(r"[0-9a-fA-F]{7,64}", target_branch) is not None
    ):
        return None

    major = int(match.group("major"))
    minor = int(match.group("minor"))
    expected = f"{major}.{minor}"
    release_version = f"{major}.{minor}.0"
    configured = config.development.milestone
    configured_match = re.fullmatch(r"(?P<major>\d+)\.(?P<minor>\d+)", configured)
    if configured_match is None:
        raise ValueError(f"invalid development milestone: {configured}")
    configured_version = (
        int(configured_match.group("major")),
        int(configured_match.group("minor")),
    )
    if (major, minor) > configured_version:
        raise ValueError(
            f"published release {expected} is newer than configured development "
            f"milestone {configured}; merge the pending rotation before retrying"
        )
    if configured != expected:
        return None

    next_minor = minor + 1
    return {
        "release_tag": tag,
        "release_version": release_version,
        "maintenance_branch": f"release/{expected}",
        "maintenance_milestone": f"{expected}.x",
        "development_branch": config.development.branch,
        "development_milestone": f"{major}.{next_minor}",
        "previous_maintenance_branch": config.maintenance.branch,
        "previous_maintenance_milestone": config.maintenance.milestone,
        "previous_development_milestone": config.development.milestone,
    }


def rotated_config(config: ReleaseLines, rotation: Rotation) -> ReleaseLines:
    """Monta a próxima configuração sem alterar o mapeamento carregado."""
    return ReleaseLines(
        development={
            "branch": rotation["development_branch"],
            "milestone": rotation["development_milestone"],
            "release_please_config": config.development.release_please_config,
            "release_please_manifest": config.development.release_please_manifest,
        },
        maintenance={
            "branch": rotation["maintenance_branch"],
            "milestone": rotation["maintenance_milestone"],
            "release_please_config": config.maintenance.release_please_config,
            "release_please_manifest": config.maintenance.release_please_manifest,
        },
    )


def write_rotated_config(path: Path, config: ReleaseLines, rotation: Rotation) -> None:
    """Persiste a próxima configuração das linhas de release como JSON formatado."""
    path.write_text(
        json.dumps(rotated_config(config, rotation).model_dump(), indent=2) + "\n",
        encoding="utf-8",
    )


def _event_values(event_path: Path) -> tuple[str, str | None]:
    event = json.loads(event_path.read_text(encoding="utf-8"))
    release = event.get("release")
    if not isinstance(release, dict):
        raise ValueError("release event is missing release metadata")
    tag = release.get("tag_name")
    target = release.get("target_commitish")
    if not isinstance(tag, str) or not tag:
        raise ValueError("release event is missing tag_name")
    return tag, target if isinstance(target, str) else None


def main() -> int:
    """Imprime as saídas do GitHub Actions e opcionalmente atualiza o arquivo de configuração."""
    if len(sys.argv) not in (2, 4) or (len(sys.argv) == 4 and sys.argv[2] != "--write"):
        print(
            "usage: rotate_release_lines.py EVENT_JSON [--write CONFIG_PATH]",
            file=sys.stderr,
        )
        return 2
    try:
        event_path = Path(sys.argv[1])
        config_path = (
            Path(sys.argv[3])
            if len(sys.argv) == 4
            else Path(__file__).parents[1] / ".github" / "release-lines.json"
        )
        config = load_release_lines(config_path)
        tag, target = _event_values(event_path)
        rotation = rotation_for_release(tag, config, target)
        if rotation is None:
            print("rotate=false")
            return 0
        if len(sys.argv) == 4:
            write_rotated_config(config_path, config, rotation)
        for key, value in rotation.items():
            print(f"{key}={value}")
        print("rotate=true")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"invalid release event or release-line configuration: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
