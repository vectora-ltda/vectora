"""Regression tests for the pull-request release-line contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_validator() -> Any:
    path = Path(__file__).parents[3] / "utils" / "validate_pr_milestone.py"
    spec = importlib.util.spec_from_file_location("validate_pr_milestone", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"não foi possível carregar {path}")
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def _event(
    *,
    base: str,
    milestone: str | None,
    head: str = "feature/example",
    labels: list[str] | None = None,
    head_repo: str = "vectora-ltda/vectora",
    author: str = "contributor",
    title: str = "fix: routine maintenance",
) -> dict[str, Any]:
    return {
        "repository": {"full_name": "vectora-ltda/vectora"},
        "pull_request": {
            "base": {"ref": base},
            "head": {"ref": head, "repo": {"full_name": head_repo}},
            "title": title,
            "milestone": {"title": milestone} if milestone else None,
            "labels": [{"name": label} for label in labels or []],
            "user": {"login": author},
        },
    }


def test_master_uses_maintenance_unless_pr_is_vext() -> None:
    assert (
        validator.validate_pull_request(_event(base="master", milestone="0.1.x")) == []
    )
    assert validator.validate_pull_request(_event(base="master", milestone="0.2"))

    vext = _event(
        base="master",
        milestone="0.2",
        head="feat/vext-ecosystem",
        title="feat: build VEXT artifacts",
    )
    assert validator.validate_pull_request(vext) == []
    vext["pull_request"]["milestone"] = {"title": "0.1.x"}
    assert validator.validate_pull_request(vext)

    assert validator.validate_pull_request(_event(base="master", milestone=None))


def test_maintenance_accepts_the_rolling_patch_milestone() -> None:
    assert (
        validator.validate_pull_request(_event(base="release/0.1", milestone="0.1.x"))
        == []
    )
    assert validator.validate_pull_request(
        _event(base="release/0.1", milestone="0.1.23")
    )
    assert validator.validate_pull_request(_event(base="release/0.1", milestone="0.2"))


def test_release_please_pr_is_exempt_only_with_controlled_source_and_label() -> None:
    event = _event(
        base="master",
        milestone=None,
        head="release-please--branches--master--components--vectora",
        labels=["autorelease: pending"],
    )
    assert validator.validate_pull_request(event) == []

    event["pull_request"]["head"]["repo"]["full_name"] = "attacker/vectora"
    assert validator.validate_pull_request(event)


def test_unsupported_base_is_rejected() -> None:
    errors = validator.validate_pull_request(_event(base="develop", milestone="0.2"))
    assert errors and "master" in errors[0]
