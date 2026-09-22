"""Validate the release line declared by a pull request milestone."""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class LabelPayload(BaseModel):
    """Subset of a GitHub label included in pull-request events."""

    model_config = ConfigDict(extra="ignore")

    name: str = ""


class RepositoryPayload(BaseModel):
    """Repository identity attached to a pull-request head."""

    model_config = ConfigDict(extra="ignore")

    full_name: str | None = None


class HeadPayload(BaseModel):
    """Branch and repository identity for a pull-request head."""

    model_config = ConfigDict(extra="ignore")

    ref: str = ""
    repo: RepositoryPayload | None = None


class BasePayload(BaseModel):
    """Base branch identity for a pull request."""

    model_config = ConfigDict(extra="ignore")

    ref: str = ""


class MilestonePayload(BaseModel):
    """Milestone metadata from a pull-request event."""

    model_config = ConfigDict(extra="ignore")

    title: str = ""


class PullRequestPayload(BaseModel):
    """Event fields consumed by the release-line validator."""

    model_config = ConfigDict(extra="ignore")

    base: BasePayload | None = None
    head: HeadPayload | None = None
    labels: list[LabelPayload] = Field(default_factory=list)
    milestone: MilestonePayload | None = None
    title: str = ""


class RepositoryEventPayload(BaseModel):
    """Repository identity from the webhook envelope."""

    model_config = ConfigDict(extra="ignore")

    full_name: str = ""


class PullRequestEvent(BaseModel):
    """Validated subset of the GitHub pull-request webhook envelope."""

    model_config = ConfigDict(extra="ignore")

    repository: RepositoryEventPayload | None = None
    pull_request: PullRequestPayload | None = None


PullRequestEvent.model_rebuild()


type EventPayload = PullRequestEvent | dict[str, object]

_VEXT_TOKEN = re.compile(r"(?<![A-Za-z0-9])vext(?![A-Za-z0-9])", re.IGNORECASE)
_RELEASE_PLEASE_LABEL = "autorelease: pending"


class ReleaseLine(BaseModel):
    """Branch and milestone for one active release line."""

    model_config = ConfigDict(extra="ignore")

    branch: str
    milestone: str


class ReleaseLineConfig(BaseModel):
    """Versioned source of truth for active release lines."""

    model_config = ConfigDict(extra="ignore")

    development: ReleaseLine
    maintenance: ReleaseLine


ReleaseLineConfig.model_rebuild()


@lru_cache(maxsize=1)
def _release_lines() -> ReleaseLineConfig:
    """Load active branches and milestones from the versioned config."""
    config_path = Path(__file__).parents[1] / ".github" / "release-lines.json"
    return ReleaseLineConfig.model_validate_json(config_path.read_bytes())


def _line_for_base(base: str) -> ReleaseLine | None:
    """Return the configured release line for a pull-request base."""
    config = _release_lines()
    return next(
        (
            line
            for line in (config.development, config.maintenance)
            if line.branch == base
        ),
        None,
    )


def _parse_event(event: EventPayload) -> PullRequestEvent | None:
    """Normalize test dictionaries and reject malformed webhook structures."""
    if isinstance(event, PullRequestEvent):
        return event
    try:
        return PullRequestEvent.model_validate(event)
    except ValidationError:
        return None


def _labels(pull_request: PullRequestPayload) -> set[str]:
    """Return label names from the validated GitHub event payload."""
    return {label.name for label in pull_request.labels}


def _is_release_please_pr(
    event: PullRequestEvent, pull_request: PullRequestPayload
) -> bool:
    """Recognize an automated release PR using immutable repository context."""
    head = pull_request.head
    head_repo = head.repo.full_name if head and head.repo else None
    repository = event.repository
    base = pull_request.base.ref if pull_request.base else ""
    expected_head = f"release-please--branches--{base}--components--vectora"
    return (
        head_repo == (repository.full_name if repository else None)
        and (head.ref if head else "") == expected_head
        and _RELEASE_PLEASE_LABEL in _labels(pull_request)
    )


def _is_vext_pr(pull_request: PullRequestPayload) -> bool:
    """Identify VEXT titles and branches for clear validation diagnostics."""
    head_ref = pull_request.head.ref if pull_request.head else ""
    return bool(_VEXT_TOKEN.search(pull_request.title) or _VEXT_TOKEN.search(head_ref))


def validate_pull_request(event: EventPayload) -> list[str]:
    """Return actionable validation errors for a pull-request event."""
    parsed_event = _parse_event(event)
    if parsed_event is None:
        return ["O payload do webhook de pull request é inválido."]

    pull_request = parsed_event.pull_request
    if pull_request is None:
        return []

    errors: list[str] = []
    base = pull_request.base.ref if pull_request.base else ""
    if _is_release_please_pr(parsed_event, pull_request):
        return []
    line = _line_for_base(base)
    if line is None:
        configured_bases = ", ".join(
            configured.branch
            for configured in (
                _release_lines().development,
                _release_lines().maintenance,
            )
        )
        errors.append(
            "PRs de código devem usar uma base declarada na configuração de release "
            f"({configured_bases}); "
            f"base recebida: `{base or '(vazia)'}`."
        )
    else:
        milestone = pull_request.milestone
        title = milestone.title.strip() if milestone else ""
        expected = line.milestone
        if not title:
            errors.append(
                "Atribua exatamente uma milestone de release: "
                f"`{expected}` para esta linha."
            )
        elif line is _release_lines().development and title != expected:
            stream = "VEXT" if _is_vext_pr(pull_request) else "de funcionalidade"
            errors.append(
                f"A milestone `{title}` não é compatível com a base `{base}`; "
                f"PRs {stream} da próxima minor devem usar `{expected}`."
            )
        elif title != expected:
            errors.append(
                f"A milestone `{title}` não é compatível com `{base}`; "
                f"use `{expected}`."
            )
    return errors


def main() -> int:
    """Validate the event file supplied by GitHub Actions."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        print("GITHUB_EVENT_PATH não foi definido.", file=sys.stderr)
        return 2
    try:
        payload = PullRequestEvent.model_validate_json(Path(event_path).read_bytes())
    except (OSError, ValueError, ValidationError) as exc:
        print(f"Evento GitHub inválido: {exc}", file=sys.stderr)
        return 2
    expected_milestone = os.environ.get("EXPECTED_RELEASE_MILESTONE")
    if expected_milestone and payload.pull_request is not None:
        payload.pull_request.milestone = MilestonePayload(title=expected_milestone)
    errors = validate_pull_request(payload)
    if errors:
        for error in errors:
            print(f"::error::{error}")
        return 1
    print("Milestone de release compatível com a branch base.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
