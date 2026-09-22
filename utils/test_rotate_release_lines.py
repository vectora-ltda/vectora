"""Tests for release-line rotation decisions and config writes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rotate_release_lines import (
    build_rotation_plan,
    rotation_for_release,
    write_rotated_config,
)
from select_release_line import ReleaseLines

CONFIG: ReleaseLines = {
    "development": {"branch": "master", "milestone": "0.2"},
    "maintenance": {"branch": "release/0.1", "milestone": "0.1.x"},
}


def test_release_tag_rotates_active_lines() -> None:
    """A published development tag advances maintenance and development lines."""
    rotation = rotation_for_release("v0.2.0", CONFIG, "master")

    assert rotation is not None
    assert rotation["maintenance_branch"] == "release/0.2"
    assert rotation["maintenance_milestone"] == "0.2.x"
    assert rotation["development_milestone"] == "0.3"
    assert rotation["previous_maintenance_branch"] == "release/0.1"


def test_unrelated_tag_does_not_rotate() -> None:
    """Tags outside the active development line are ignored safely."""
    assert rotation_for_release("v0.1.23", CONFIG, "master") is None
    assert rotation_for_release("v0.2.0", CONFIG, "release/0.1") is None
    assert rotation_for_release("v0.2.1", CONFIG, "master") is None
    assert rotation_for_release("", CONFIG, "master") is None
    assert rotation_for_release("v0.2.0", CONFIG, None) is not None
    assert rotation_for_release("v0.2.0", CONFIG, "") is None


def test_rotation_writes_next_config(tmp_path: Path) -> None:
    """A valid rotation persists the next branch and milestone mapping."""
    rotation = rotation_for_release("0.2.0", CONFIG, "master")
    assert rotation is not None
    path = tmp_path / "release-lines.json"

    write_rotated_config(path, CONFIG, rotation)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "development": {"branch": "master", "milestone": "0.3"},
        "maintenance": {"branch": "release/0.2", "milestone": "0.2.x"},
    }


def test_rotation_plan_executes_all_success_paths() -> None:
    """The plan covers branch, milestones, PR metadata, and publication."""
    rotation = rotation_for_release("v0.2.0", CONFIG, "master")
    assert rotation is not None

    plan = build_rotation_plan(
        rotation,
        [
            {
                "number": 10,
                "base_branch": "release/0.1",
                "milestone": "0.1.x",
            },
            {"number": 11, "base_branch": "master", "milestone": "0.2"},
            {"number": 12, "base_branch": "master", "milestone": "0.1.x"},
            {"number": 13, "base_branch": "feature/other", "milestone": None},
        ],
    )

    assert plan == [
        {"kind": "create_branch", "pull_request": None, "value": "release/0.2"},
        {"kind": "ensure_milestone", "pull_request": None, "value": "0.2.x"},
        {"kind": "ensure_milestone", "pull_request": None, "value": "0.3"},
        {"kind": "update_base", "pull_request": 10, "value": "release/0.2"},
        {"kind": "update_milestone", "pull_request": 10, "value": "0.2.x"},
        {"kind": "update_milestone", "pull_request": 11, "value": "0.3"},
        {"kind": "publish_config", "pull_request": None, "value": "v0.2.0"},
    ]


def test_rotation_plan_ignores_unrelated_pull_requests() -> None:
    """Unrelated PRs produce no API update operations."""
    rotation = rotation_for_release("v0.2.0", CONFIG, "master")
    assert rotation is not None

    plan = build_rotation_plan(
        rotation,
        [{"number": 99, "base_branch": "feature/work", "milestone": "0.1.x"}],
    )

    assert [operation["kind"] for operation in plan] == [
        "create_branch",
        "ensure_milestone",
        "ensure_milestone",
        "publish_config",
    ]


def test_invalid_release_event_returns_failure(tmp_path: Path, monkeypatch) -> None:
    """Malformed release input fails before any rotation can be published."""
    from rotate_release_lines import main

    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["rotate_release_lines.py", str(event)])

    assert main() == 1
