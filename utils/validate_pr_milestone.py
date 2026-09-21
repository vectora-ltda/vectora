"""Validate the release line declared by a pull request milestone."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_MAINTENANCE_MILESTONE = re.compile(r"^0\.1\.\d+$")
_RELEASE_PLEASE_HEAD = re.compile(r"^release-please--branches--")
_RELEASE_PLEASE_LABEL = "autorelease: pending"
_SUPPORTED_BASES = {"master", "release/0.1"}


def _labels(pull_request: dict[str, Any]) -> set[str]:
    """Return label names from the GitHub event payload."""
    return {
        str(label.get("name", ""))
        for label in pull_request.get("labels", [])
        if isinstance(label, dict)
    }


def _is_release_please_pr(event: dict[str, Any], pull_request: dict[str, Any]) -> bool:
    """Recognize an automated release PR using immutable repository context."""
    head = pull_request.get("head") or {}
    head_repo = (head.get("repo") or {}).get("full_name")
    repository = event.get("repository") or {}
    return (
        head_repo == repository.get("full_name")
        and bool(_RELEASE_PLEASE_HEAD.match(str(head.get("ref", ""))))
        and _RELEASE_PLEASE_LABEL in _labels(pull_request)
    )


def validate_pull_request(event: dict[str, Any]) -> list[str]:
    """Return actionable validation errors for a pull-request event."""
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        return []

    base = str((pull_request.get("base") or {}).get("ref", ""))
    if _is_release_please_pr(event, pull_request):
        return []
    if base not in _SUPPORTED_BASES:
        return [
            "PRs de código devem usar base `master` ou `release/0.1`; "
            f"base recebida: `{base or '(vazia)'}`."
        ]

    milestone = pull_request.get("milestone")
    title = (
        str(milestone.get("title", "").strip()) if isinstance(milestone, dict) else ""
    )
    if not title:
        return [
            "Atribua exatamente uma milestone de release: `0.2` para `master` "
            "ou uma milestone `0.1.x` para `release/0.1`."
        ]

    if base == "master" and title != "0.2":
        return [
            f"A milestone `{title}` não é compatível com a base `master`; "
            "PRs da próxima minor devem usar `0.2`."
        ]
    if base == "release/0.1" and not _MAINTENANCE_MILESTONE.fullmatch(title):
        return [
            f"A milestone `{title}` não é compatível com `release/0.1`; "
            "use a versão patch exata, por exemplo `0.1.23`."
        ]
    return []


def main() -> int:
    """Validate the event file supplied by GitHub Actions."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        print("GITHUB_EVENT_PATH não foi definido.", file=sys.stderr)
        return 2
    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    errors = validate_pull_request(payload)
    if errors:
        for error in errors:
            print(f"::error::{error}")
        return 1
    print("Milestone de release compatível com a branch base.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
