"""Testes de regressão do contrato de linha de release das pull requests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import NotRequired, Protocol, TypedDict, cast

import pytest


class RepositoryPayload(TypedDict):
    full_name: NotRequired[str]


class HeadPayload(TypedDict):
    ref: NotRequired[str]
    repo: NotRequired[RepositoryPayload | None]


class BasePayload(TypedDict):
    ref: NotRequired[str]


class MilestonePayload(TypedDict):
    title: NotRequired[str]


class LabelPayload(TypedDict):
    name: NotRequired[str]


class PullRequestPayload(TypedDict):
    base: NotRequired[BasePayload]
    head: NotRequired[HeadPayload]
    title: NotRequired[str]
    milestone: NotRequired[MilestonePayload | None]
    labels: NotRequired[list[LabelPayload]]


class RepositoryEventPayload(TypedDict):
    full_name: NotRequired[str]


class PullRequestEvent(TypedDict):
    repository: NotRequired[RepositoryEventPayload]
    pull_request: NotRequired[PullRequestPayload | None]


class ValidatorModule(Protocol):
    def validate_pull_request(
        self, event: PullRequestEvent | dict[str, object]
    ) -> list[str]: ...

    def main(self) -> int: ...


def _load_validator() -> ValidatorModule:
    path = Path(__file__).parents[3] / "utils" / "validate_pr_milestone.py"
    spec = importlib.util.spec_from_file_location("validate_pr_milestone", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"não foi possível carregar {path}")
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)
    spec.loader.exec_module(module)
    return cast("ValidatorModule", module)


validator = _load_validator()
RELEASE_CONFIG: dict[str, dict[str, str]] = json.loads(
    (Path(__file__).parents[3] / ".github" / "release-lines.json").read_text(
        encoding="utf-8"
    )
)
DEVELOPMENT_BRANCH: str = RELEASE_CONFIG["development"]["branch"]
DEVELOPMENT_MILESTONE: str = RELEASE_CONFIG["development"]["milestone"]
MAINTENANCE_BRANCH: str = RELEASE_CONFIG["maintenance"]["branch"]
MAINTENANCE_MILESTONE: str = RELEASE_CONFIG["maintenance"]["milestone"]


def _event(
    *,
    base: str,
    milestone: str | None,
    head: str = "feature/example",
    labels: list[str] | None = None,
    head_repo: str = "vectora-ltda/vectora",
    title: str = "fix: routine maintenance",
) -> PullRequestEvent:
    return {
        "repository": {"full_name": "vectora-ltda/vectora"},
        "pull_request": {
            "base": {"ref": base},
            "head": {"ref": head, "repo": {"full_name": head_repo}},
            "title": title,
            "milestone": {"title": milestone} if milestone else None,
            "labels": [{"name": label} for label in labels or []],
        },
    }


def test_empty_event_is_accepted() -> None:
    assert validator.validate_pull_request({}) == []


def test_main_rejects_malformed_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text('{"pull_request": "malformed"}', encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    assert validator.main() == 2


def test_main_uses_milestone_assigned_during_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valida a milestone atualizada em vez do snapshot obsoleto do webhook."""
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(_event(base=MAINTENANCE_BRANCH, milestone=None)),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("CURRENT_RELEASE_MILESTONE", DEVELOPMENT_MILESTONE)
    monkeypatch.setenv("CURRENT_PR_BASE", DEVELOPMENT_BRANCH)

    assert validator.main() == 0


def test_master_feature_pr_is_accepted() -> None:
    assert (
        validator.validate_pull_request(
            _event(base=DEVELOPMENT_BRANCH, milestone=DEVELOPMENT_MILESTONE)
        )
        == []
    )


def test_master_pr_rejects_maintenance_milestone() -> None:
    errors = validator.validate_pull_request(
        _event(base=DEVELOPMENT_BRANCH, milestone=MAINTENANCE_MILESTONE)
    )
    assert errors and DEVELOPMENT_MILESTONE in errors[0]


def test_scoped_vext_pr_accepts_minor_milestone() -> None:
    event = _event(
        base=DEVELOPMENT_BRANCH,
        milestone=DEVELOPMENT_MILESTONE,
        title="feat(vext): build ecosystem artifacts",
    )
    assert validator.validate_pull_request(event) == []


def test_vext_pr_rejects_maintenance_milestone() -> None:
    event = _event(
        base=DEVELOPMENT_BRANCH,
        milestone=MAINTENANCE_MILESTONE,
        title="feat(vext): build ecosystem artifacts",
    )
    errors = validator.validate_pull_request(event)
    assert errors and DEVELOPMENT_MILESTONE in errors[0] and "VEXT" in errors[0]


def test_master_pr_requires_a_milestone() -> None:
    errors = validator.validate_pull_request(
        _event(base=DEVELOPMENT_BRANCH, milestone=None)
    )
    assert errors and "milestone" in errors[0]


def test_maintenance_accepts_the_rolling_patch_milestone() -> None:
    assert (
        validator.validate_pull_request(
            _event(base=MAINTENANCE_BRANCH, milestone=MAINTENANCE_MILESTONE)
        )
        == []
    )


def test_maintenance_rejects_exact_patch() -> None:
    errors = validator.validate_pull_request(
        _event(base=MAINTENANCE_BRANCH, milestone="0.1.23")
    )
    assert errors and MAINTENANCE_MILESTONE in errors[0]


def test_maintenance_rejects_minor() -> None:
    errors = validator.validate_pull_request(
        _event(base=MAINTENANCE_BRANCH, milestone=DEVELOPMENT_MILESTONE)
    )
    assert errors and MAINTENANCE_MILESTONE in errors[0]


def test_release_please_pr_is_exempt_with_controlled_source_and_label() -> None:
    event = _event(
        base=DEVELOPMENT_BRANCH,
        milestone=None,
        head=f"release-please--branches--{DEVELOPMENT_BRANCH}--components--vectora",
        labels=["autorelease: pending"],
    )
    assert validator.validate_pull_request(event) == []


def test_release_please_pr_from_another_repo_is_rejected() -> None:
    event = _event(
        base=DEVELOPMENT_BRANCH,
        milestone=None,
        head=f"release-please--branches--{DEVELOPMENT_BRANCH}--components--vectora",
        labels=["autorelease: pending"],
        head_repo="attacker/vectora",
    )
    errors = validator.validate_pull_request(event)
    assert errors and "milestone" in errors[0]


def test_release_please_pr_without_pending_label_is_rejected() -> None:
    event = _event(
        base=DEVELOPMENT_BRANCH,
        milestone=None,
        head=f"release-please--branches--{DEVELOPMENT_BRANCH}--components--vectora",
    )
    errors = validator.validate_pull_request(event)
    assert errors and "milestone" in errors[0]


def test_release_please_lookalike_branch_is_rejected() -> None:
    event = _event(
        base=DEVELOPMENT_BRANCH,
        milestone=None,
        head=f"release-please--branches--{DEVELOPMENT_BRANCH}--components--vectora-lookalike",
        labels=["autorelease: pending"],
    )
    errors = validator.validate_pull_request(event)
    assert errors and "milestone" in errors[0]


def test_release_please_branch_retargeted_to_maintenance_is_rejected() -> None:
    event = _event(
        base=MAINTENANCE_BRANCH,
        milestone=None,
        head=f"release-please--branches--{DEVELOPMENT_BRANCH}--components--vectora",
        labels=["autorelease: pending"],
    )
    errors = validator.validate_pull_request(event)
    assert errors and "milestone" in errors[0]


def test_unsupported_base_is_rejected() -> None:
    errors = validator.validate_pull_request(
        _event(base="develop", milestone=DEVELOPMENT_MILESTONE)
    )
    assert errors and DEVELOPMENT_BRANCH in errors[0]
