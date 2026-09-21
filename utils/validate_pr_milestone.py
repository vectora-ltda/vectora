"""Validate the release line declared by a pull request milestone."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import NotRequired, TypedDict, cast


class LabelPayload(TypedDict):
    """Subset of a GitHub label included in pull-request events."""

    name: NotRequired[str]


class RepositoryPayload(TypedDict):
    """Repository identity attached to a pull-request head."""

    full_name: NotRequired[str]
    nameWithOwner: NotRequired[str]


class HeadPayload(TypedDict):
    """Branch and repository identity for a pull-request head."""

    ref: NotRequired[str]
    repo: NotRequired[RepositoryPayload | None]


class BasePayload(TypedDict):
    """Base branch identity for a pull request."""

    ref: NotRequired[str]


class MilestonePayload(TypedDict):
    """Milestone metadata from a pull-request event."""

    title: NotRequired[str]


class PullRequestPayload(TypedDict):
    """Event fields consumed by the release-line validator."""

    base: NotRequired[BasePayload | None]
    head: NotRequired[HeadPayload | None]
    labels: NotRequired[list[LabelPayload]]
    milestone: NotRequired[MilestonePayload | None]
    title: NotRequired[str]


class RepositoryEventPayload(TypedDict):
    """Repository identity from the webhook envelope."""

    full_name: NotRequired[str]


class PullRequestEvent(TypedDict):
    """Subset of the GitHub pull-request webhook envelope we validate."""

    repository: NotRequired[RepositoryEventPayload | None]
    pull_request: NotRequired[PullRequestPayload | None]


_MAINTENANCE_MILESTONE = "0.1.x"
_NEXT_MINOR_MILESTONE = "0.2"
_VEXT_TOKEN = re.compile(r"(?:^|[-_/ ])vext(?:$|[-_/ ])", re.IGNORECASE)
_RELEASE_PLEASE_HEAD = re.compile(r"^release-please--branches--")
_RELEASE_PLEASE_LABEL = "autorelease: pending"
_SUPPORTED_BASES = {"master", "release/0.1"}


def _labels(pull_request: PullRequestPayload) -> set[str]:
    """Return label names from the GitHub event payload."""
    return {
        str(label.get("name", ""))
        for label in pull_request.get("labels", [])
        if isinstance(label, dict)
    }


def _is_release_please_pr(
    event: PullRequestEvent, pull_request: PullRequestPayload
) -> bool:
    """Recognize an automated release PR using immutable repository context."""
    head = pull_request.get("head") or {}
    head_repo = (head.get("repo") or {}).get("full_name")
    repository = event.get("repository") or {}
    return (
        head_repo == repository.get("full_name")
        and bool(_RELEASE_PLEASE_HEAD.match(str(head.get("ref", ""))))
        and _RELEASE_PLEASE_LABEL in _labels(pull_request)
    )


def _is_vext_pr(pull_request: PullRequestPayload) -> bool:
    """Identify the VEXT stream that is the sole ``0.2`` exception."""
    title = str(pull_request.get("title", ""))
    head_ref = str((pull_request.get("head") or {}).get("ref", ""))
    return bool(_VEXT_TOKEN.search(title) or _VEXT_TOKEN.search(head_ref))


def validate_pull_request(event: PullRequestEvent) -> list[str]:
    """Return actionable validation errors for a pull-request event."""
    pull_request = event.get("pull_request")
    if pull_request is None:
        return []

    errors: list[str] = []
    base_payload = pull_request.get("base") or {}
    base = str(base_payload.get("ref", ""))
    if _is_release_please_pr(event, pull_request):
        return []
    if base not in _SUPPORTED_BASES:
        errors.append(
            "PRs de código devem usar base `master` ou `release/0.1`; "
            f"base recebida: `{base or '(vazia)'}`."
        )
    else:
        milestone = pull_request.get("milestone")
        title = str(milestone.get("title", "").strip()) if milestone else ""
        vext = _is_vext_pr(pull_request)
        expected = _NEXT_MINOR_MILESTONE if vext else _MAINTENANCE_MILESTONE
        if not title:
            errors.append(
                "Atribua exatamente uma milestone de release: "
                f"`{expected}` para esta linha."
            )
        elif base == "master" and title != expected:
            errors.append(
                f"A milestone `{title}` não é compatível com a base `master`; "
                f"PRs VEXT devem usar `{_NEXT_MINOR_MILESTONE}` e os demais "
                f"`{_MAINTENANCE_MILESTONE}`."
            )
        elif base == "release/0.1" and title != _MAINTENANCE_MILESTONE:
            errors.append(
                f"A milestone `{title}` não é compatível com `release/0.1`; "
                f"use `{_MAINTENANCE_MILESTONE}`."
            )
    return errors


def main() -> int:
    """Validate the event file supplied by GitHub Actions."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        print("GITHUB_EVENT_PATH não foi definido.", file=sys.stderr)
        return 2
    payload = cast(
        "PullRequestEvent",
        json.loads(Path(event_path).read_text(encoding="utf-8")),
    )
    errors = validate_pull_request(payload)
    if errors:
        for error in errors:
            print(f"::error::{error}")
        return 1
    print("Milestone de release compatível com a branch base.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
