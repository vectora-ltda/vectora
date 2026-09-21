"""Regression tests for the pull-request release-line contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import NotRequired, Protocol, TypedDict, cast


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
    def validate_pull_request(self, event: PullRequestEvent) -> list[str]: ...


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


def test_master_feature_pr_is_accepted() -> None:
    assert validator.validate_pull_request(_event(base="master", milestone="0.2")) == []


def test_master_pr_rejects_maintenance_milestone() -> None:
    errors = validator.validate_pull_request(_event(base="master", milestone="0.1.x"))
    assert errors and "0.2" in errors[0]


def test_scoped_vext_pr_accepts_minor_milestone() -> None:
    event = _event(
        base="master",
        milestone="0.2",
        title="feat(vext): build ecosystem artifacts",
    )
    assert validator.validate_pull_request(event) == []


def test_vext_pr_rejects_maintenance_milestone() -> None:
    event = _event(
        base="master",
        milestone="0.1.x",
        title="feat(vext): build ecosystem artifacts",
    )
    errors = validator.validate_pull_request(event)
    assert errors and "0.2" in errors[0] and "VEXT" in errors[0]


def test_master_pr_requires_a_milestone() -> None:
    errors = validator.validate_pull_request(_event(base="master", milestone=None))
    assert errors and "milestone" in errors[0]


def test_maintenance_accepts_the_rolling_patch_milestone() -> None:
    assert (
        validator.validate_pull_request(_event(base="release/0.1", milestone="0.1.x"))
        == []
    )


def test_maintenance_rejects_exact_patch() -> None:
    errors = validator.validate_pull_request(
        _event(base="release/0.1", milestone="0.1.23")
    )
    assert errors and "0.1.x" in errors[0]


def test_maintenance_rejects_minor() -> None:
    errors = validator.validate_pull_request(
        _event(base="release/0.1", milestone="0.2")
    )
    assert errors and "0.1.x" in errors[0]


def test_release_please_pr_is_exempt_with_controlled_source_and_label() -> None:
    event = _event(
        base="master",
        milestone=None,
        head="release-please--branches--master--components--vectora",
        labels=["autorelease: pending"],
    )
    assert validator.validate_pull_request(event) == []


def test_release_please_pr_from_another_repo_is_rejected() -> None:
    event = _event(
        base="master",
        milestone=None,
        head="release-please--branches--master--components--vectora",
        labels=["autorelease: pending"],
        head_repo="attacker/vectora",
    )
    errors = validator.validate_pull_request(event)
    assert errors and "milestone" in errors[0]


def test_release_please_pr_without_pending_label_is_rejected() -> None:
    event = _event(
        base="master",
        milestone=None,
        head="release-please--branches--master--components--vectora",
    )
    errors = validator.validate_pull_request(event)
    assert errors and "milestone" in errors[0]


def test_unsupported_base_is_rejected() -> None:
    errors = validator.validate_pull_request(_event(base="develop", milestone="0.2"))
    assert errors and "master" in errors[0]
