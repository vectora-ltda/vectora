"""Tests for canonical release planning."""

from __future__ import annotations

import json
from pathlib import Path

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


def test_apply_updates_all_configured_versions_and_marked_lockfile(
    tmp_path: Path,
) -> None:
    (tmp_path / "vectora").mkdir()
    (tmp_path / "services").mkdir()
    (tmp_path / "company").mkdir()
    (tmp_path / "vectora" / "pyproject.toml").write_text(
        'version = "0.3.0"\n', encoding="utf-8"
    )
    for path in (
        "vectora/frontend.json",
        "services/package.json",
        "company/package.json",
    ):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"version": "0.3.0"}), encoding="utf-8")
    (tmp_path / "vectora" / "uv.lock").write_text(
        'name = "vectora"\nversion = "0.3.0" # x-release-please-version\n',
        encoding="utf-8",
    )
    (tmp_path / "vectora" / "CHANGELOG.md").write_text(
        "# Changelog\n", encoding="utf-8"
    )
    (tmp_path / ".manifest.json").write_text('{".": "0.3.0"}\n', encoding="utf-8")
    plan = calculate_plan("0.3.0", [Commit(subject="fix: repair release")])
    apply_plan(
        tmp_path,
        plan,
        [
            "vectora/pyproject.toml",
            "vectora/frontend.json",
            "services/package.json",
            "company/package.json",
            "vectora/uv.lock",
        ],
        manifest=".manifest.json",
    )
    assert 'version = "0.3.1" # x-release-please-version' in (
        tmp_path / "vectora" / "uv.lock"
    ).read_text(encoding="utf-8")
    assert '"version": "0.3.1"' in (tmp_path / "services/package.json").read_text(
        encoding="utf-8"
    )
    assert (
        json.loads((tmp_path / ".manifest.json").read_text(encoding="utf-8"))["."]
        == "0.3.1"
    )
