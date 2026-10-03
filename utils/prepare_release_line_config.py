"""Atualiza as linhas de release no prÃ³prio branch gerado pelo Release Please."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from select_release_line import ReleaseLines

RELEASE_HEADING = re.compile(r"^## \[(?P<version>\d+\.\d+\.\d+)\]", re.MULTILINE)
MINOR_ZERO = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.0$")


def current_release_version(changelog: str) -> str | None:
    """Retorna a versÃ£o da primeira seÃ§Ã£o gerada no changelog."""
    match = RELEASE_HEADING.search(changelog)
    return match.group("version") if match else None


def update_release_line_files(
    changelog_path: Path,
    lines_path: Path,
    maintenance_manifest_path: Path,
    target_branch: str,
) -> bool:
    """Atualiza a configuraÃ§Ã£o quando o PR representa uma nova minor."""
    version = current_release_version(changelog_path.read_text(encoding="utf-8"))
    if version is None:
        return False
    match = MINOR_ZERO.fullmatch(version)
    if match is None:
        return False

    config = ReleaseLines.model_validate(
        json.loads(lines_path.read_text(encoding="utf-8"))
    )
    if target_branch != config.development.branch:
        return False
    expected = f"{match.group('major')}.{match.group('minor')}"
    if config.development.milestone != expected:
        return False

    next_minor = int(match.group("minor")) + 1
    next_development = f"release/{match.group('major')}.{next_minor}"
    config = ReleaseLines(
        development=config.development.model_copy(
            update={
                "branch": next_development,
                "milestone": f"{match.group('major')}.{next_minor}",
            }
        ),
        maintenance=config.maintenance.model_copy(
            update={
                "branch": "master",
                "milestone": f"{match.group('major')}.{match.group('minor')}.x",
            }
        ),
    )
    lines_path.write_text(
        json.dumps(config.model_dump(), indent=2) + "\n", encoding="utf-8"
    )
    maintenance_manifest_path.write_text(
        json.dumps({".": version}, indent=2) + "\n", encoding="utf-8"
    )
    return True


def main() -> int:
    """Executa a atualizaÃ§Ã£o com argumentos prÃ³prios do workflow."""
    if len(sys.argv) != 5:
        print(
            "usage: prepare_release_line_config.py CHANGELOG LINES MANIFEST TARGET_BRANCH",
            file=sys.stderr,
        )
        return 2
    try:
        changed = update_release_line_files(
            Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid release-line inputs: {exc}", file=sys.stderr)
        return 1
    print(f"updated={'true' if changed else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
