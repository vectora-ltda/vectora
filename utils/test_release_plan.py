"""Tests for canonical release planning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from release_plan import Commit, apply_plan, calculate_plan


def test_feature_bumps_minor_before_one() -> None:
    plan = calculate_plan("0.3.0", [Commit(subject="feat: add canonical release PR")])
    assert plan.next_version == "0.4.0"
    assert plan.bump == "minor"


def test_fix_bumps_patch() -> None:
    plan = calculate_plan("1.2.3", [Commit(subject="fix: repair release branch")])
    assert plan.next_version == "1.2.4"


def test_breaking_change_uses_minor_before_one() -> None:
    plan = calculate_plan("0.3.0", [Commit(subject="feat!: change release contract")])
    assert plan.next_version == "0.4.0"


def test_breaking_change_footer_uses_major_after_one() -> None:
    plan = calculate_plan(
        "1.3.0",
        [
            Commit(
                subject="feat: change release contract",
                body="BREAKING CHANGE: callers must provide a workspace",
            )
        ],
    )
    assert plan.next_version == "2.0.0"
    assert plan.breaking_entries == ["feat: change release contract"]


def test_non_release_commits_do_not_create_plan() -> None:
    plan = calculate_plan("0.3.0", [Commit(subject="docs: explain release flow")])
    assert plan.next_version == "0.3.0"
    assert plan.bump == "none"


_LOCK_TEMPLATE = """version = 1

[[package]]
name = "pydantic"
version = "0.3.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "vectora"
version = "0.3.0"
source = { editable = "." }
dependencies = [
    { name = "pydantic" },
]
"""


def test_apply_updates_all_configured_versions_and_lockfile(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text('version = "0.3.0"\n', encoding="utf-8")
    frontend_package = tmp_path / "frontend" / "package.json"
    frontend_package.parent.mkdir()
    frontend_package.write_text(json.dumps({"version": "0.3.0"}), encoding="utf-8")
    lock_path = tmp_path / "uv.lock"
    lock_path.write_text(_LOCK_TEMPLATE, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    (tmp_path / ".manifest.json").write_text('{".": "0.3.0"}\n', encoding="utf-8")
    plan = calculate_plan("0.3.0", [Commit(subject="fix: repair release")])
    apply_plan(
        tmp_path,
        plan,
        ["pyproject.toml", "frontend/package.json", "uv.lock"],
        manifest=".manifest.json",
    )
    assert lock_path.read_text(encoding="utf-8") == _LOCK_TEMPLATE.replace(
        'name = "vectora"\nversion = "0.3.0"', 'name = "vectora"\nversion = "0.3.1"'
    )
    assert '"version": "0.3.1"' in frontend_package.read_text(encoding="utf-8")
    assert (
        json.loads((tmp_path / ".manifest.json").read_text(encoding="utf-8"))["."]
        == "0.3.1"
    )

    without_package = _LOCK_TEMPLATE.replace('name = "vectora"', 'name = "other"')
    lock_path.write_text(without_package, encoding="utf-8")
    with pytest.raises(ValueError, match="vectora"):
        apply_plan(tmp_path, plan, ["uv.lock"])
    assert lock_path.read_text(encoding="utf-8") == without_package

    duplicated = _LOCK_TEMPLATE.replace('name = "pydantic"', 'name = "vectora"')
    lock_path.write_text(duplicated, encoding="utf-8")
    with pytest.raises(ValueError, match="vectora"):
        apply_plan(tmp_path, plan, ["uv.lock"])
    assert lock_path.read_text(encoding="utf-8") == duplicated
