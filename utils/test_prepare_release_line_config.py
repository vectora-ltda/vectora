"""Testes da atualização automática das linhas de release."""

from __future__ import annotations

import json
from pathlib import Path

from prepare_release_line_config import update_release_line_files


def _write_inputs(tmp_path: Path, version: str = "0.2.0") -> tuple[Path, Path, Path]:
    changelog = tmp_path / "CHANGELOG.md"
    lines = tmp_path / "release-lines.json"
    manifest = tmp_path / "manifest.json"
    changelog.write_text(f"## [{version}]\n\nAtual release.\n", encoding="utf-8")
    lines.write_text(
        json.dumps(
            {
                "development": {
                    "branch": "master",
                    "milestone": "0.2",
                    "release_please_config": "dev.json",
                    "release_please_manifest": "dev-manifest.json",
                },
                "maintenance": {
                    "branch": "release/0.1",
                    "milestone": "0.1.x",
                    "release_please_config": "maint.json",
                    "release_please_manifest": "manifest.json",
                },
            }
        ),
        encoding="utf-8",
    )
    manifest.write_text('{".": "0.1.23"}\n', encoding="utf-8")
    return changelog, lines, manifest


def test_minor_release_updates_next_lines(tmp_path: Path) -> None:
    changelog, lines, manifest = _write_inputs(tmp_path)

    assert update_release_line_files(changelog, lines, manifest, "master") is True
    result = json.loads(lines.read_text(encoding="utf-8"))
    assert result["development"]["milestone"] == "0.3"
    assert result["maintenance"] == {
        "branch": "release/0.2",
        "milestone": "0.2.x",
        "release_please_config": "maint.json",
        "release_please_manifest": "manifest.json",
    }
    assert json.loads(manifest.read_text(encoding="utf-8")) == {".": "0.2.0"}


def test_patch_release_does_not_rotate_lines(tmp_path: Path) -> None:
    changelog, lines, manifest = _write_inputs(tmp_path, "0.2.1")

    assert update_release_line_files(changelog, lines, manifest, "release/0.1") is False
    assert json.loads(manifest.read_text(encoding="utf-8")) == {".": "0.1.23"}
