"""Calculate and apply the next release-line rotation."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import TypedDict

from select_release_line import ReleaseLines, load_release_lines

SEMVER_TAG: re.Pattern[str] = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.0$"
)


class Rotation(TypedDict):
    """Metadata needed to rotate the active release lines."""

    release_tag: str
    release_version: str
    maintenance_branch: str
    maintenance_milestone: str
    development_branch: str
    development_milestone: str
    previous_maintenance_branch: str
    previous_maintenance_milestone: str
    previous_development_milestone: str


def rotation_for_release(
    tag: str, config: ReleaseLines, target_branch: str | None = None
) -> Rotation | None:
    """Return the next lines when ``tag`` closes the configured development line."""
    match = SEMVER_TAG.fullmatch(tag)
    if match is None:
        return None
    if target_branch is not None and target_branch != config["development"]["branch"]:
        return None

    major = int(match.group("major"))
    minor = int(match.group("minor"))
    expected = f"{major}.{minor}"
    if config["development"]["milestone"] != expected:
        return None

    next_minor = minor + 1
    return {
        "release_tag": tag,
        "release_version": expected,
        "maintenance_branch": f"release/{expected}",
        "maintenance_milestone": f"{expected}.x",
        "development_branch": config["development"]["branch"],
        "development_milestone": f"{major}.{next_minor}",
        "previous_maintenance_branch": config["maintenance"]["branch"],
        "previous_maintenance_milestone": config["maintenance"]["milestone"],
        "previous_development_milestone": config["development"]["milestone"],
    }


def rotated_config(config: ReleaseLines, rotation: Rotation) -> ReleaseLines:
    """Build the next configuration without mutating the loaded mapping."""
    return {
        "development": {
            "branch": rotation["development_branch"],
            "milestone": rotation["development_milestone"],
        },
        "maintenance": {
            "branch": rotation["maintenance_branch"],
            "milestone": rotation["maintenance_milestone"],
        },
    }


def write_rotated_config(path: Path, config: ReleaseLines, rotation: Rotation) -> None:
    """Persist the next release-line configuration as formatted JSON."""
    path.write_text(
        json.dumps(rotated_config(config, rotation), indent=2) + "\n",
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
    """Print GitHub Actions outputs and optionally update the config file."""
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
