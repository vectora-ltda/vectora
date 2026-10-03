from __future__ import annotations

import json
from pathlib import Path

from rotate_release_lines import (
    build_rotation_plan,
    rotation_for_release,
    write_rotated_config,
)
from select_release_line import ReleaseLines

CONFIG = ReleaseLines(
    development={
        "branch": "release/0.3",
        "milestone": "0.3",
        "release_please_config": "dev.json",
        "release_please_manifest": "dev-manifest.json",
    },
    maintenance={
        "branch": "master",
        "milestone": "0.2.x",
        "release_please_config": "maint.json",
        "release_please_manifest": "manifest.json",
    },
)


def test_minor_release_opens_next_release_branch() -> None:
    rotation = rotation_for_release("v0.3.0", CONFIG, "master")
    assert rotation is not None
    assert rotation["development_branch"] == "release/0.4"
    assert rotation["development_milestone"] == "0.4"
    assert rotation["maintenance_branch"] == "master"
    assert rotation["maintenance_milestone"] == "0.3.x"


def test_release_on_development_branch_does_not_rotate() -> None:
    assert rotation_for_release("v0.3.0", CONFIG, "release/0.3") is None


def test_patch_release_does_not_rotate() -> None:
    assert rotation_for_release("v0.3.1", CONFIG, "master") is None


def test_rotation_plan_moves_open_minor_prs() -> None:
    rotation = rotation_for_release("v0.3.0", CONFIG, "master")
    assert rotation is not None
    plan = build_rotation_plan(
        rotation,
        [
            {"number": 12, "base_branch": "release/0.3", "milestone": "0.3"},
            {"number": 13, "base_branch": "master", "milestone": "0.3.x"},
        ],
    )
    assert {item["kind"] for item in plan} == {
        "create_branch",
        "ensure_milestone",
        "update_base",
        "update_milestone",
        "publish_config",
    }
    assert {item["value"] for item in plan if item["kind"] == "update_base"} == {
        "release/0.4"
    }


def test_rotation_writes_next_config(tmp_path: Path) -> None:
    rotation = rotation_for_release("v0.3.0", CONFIG, "master")
    assert rotation is not None
    path = tmp_path / "release-lines.json"
    write_rotated_config(path, CONFIG, rotation)
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["development"]["branch"] == "release/0.4"
    assert result["development"]["milestone"] == "0.4"
    assert result["maintenance"]["branch"] == "master"
    assert result["maintenance"]["milestone"] == "0.3.x"
