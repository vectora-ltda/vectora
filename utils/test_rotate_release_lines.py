"""Regressões da rotação de releases, inclusive entradas inválidas e repetição."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import rotate_release_lines
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
    """Publicar a minor mantém master estável e abre a próxima minor."""
    rotation = rotation_for_release("v0.3.0", CONFIG, "master")
    assert rotation is not None
    assert rotation["development_branch"] == "release/0.4"
    assert rotation["development_milestone"] == "0.4"
    assert rotation["maintenance_branch"] == "master"
    assert rotation["maintenance_milestone"] == "0.3.x"


def test_release_on_development_branch_does_not_rotate() -> None:
    """Uma publicação feita na origem de desenvolvimento não roda a rotação."""
    assert rotation_for_release("v0.3.0", CONFIG, "release/0.3") is None


def test_patch_release_does_not_rotate() -> None:
    """Patches não abrem outra linha de desenvolvimento."""
    assert rotation_for_release("v0.3.1", CONFIG, "master") is None


def test_rotation_plan_moves_open_minor_prs() -> None:
    """Move somente PRs da minor anterior, preservando patches em master."""
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
    assert not any(item["pull_request"] == 13 for item in plan)
    assert [item for item in plan if item["kind"] == "update_milestone"] == [
        {"kind": "update_milestone", "pull_request": 12, "value": "0.4"}
    ]


def test_rotation_writes_next_config(tmp_path: Path) -> None:
    """Persiste as novas linhas e preserva os caminhos dos manifestos."""
    rotation = rotation_for_release("v0.3.0", CONFIG, "master")
    assert rotation is not None
    path = tmp_path / "release-lines.json"
    write_rotated_config(path, CONFIG, rotation)
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["development"]["branch"] == "release/0.4"
    assert result["development"]["milestone"] == "0.4"
    assert result["maintenance"]["branch"] == "master"
    assert result["maintenance"]["milestone"] == "0.3.x"


@pytest.mark.parametrize("target", [None, "", "develop", "release/0.3"])
def test_invalid_target_does_not_rotate(target: str | None) -> None:
    """Uma origem não autorizada não gera operações de rotação."""
    assert rotation_for_release("v0.3.0", CONFIG, target) is None


def test_commit_target_can_rotate() -> None:
    """O workflow verifica ancestralidade quando o evento aponta para um SHA."""
    assert rotation_for_release("v0.3.0", CONFIG, "a" * 40) is not None


@pytest.mark.parametrize("tag", ["", "v0.1.0", "v0.3.1", "invalid", "v00.3.0"])
def test_unrelated_tags_do_not_rotate(tag: str) -> None:
    """Tags antigas, patches e valores inválidos não avançam a configuração."""
    assert rotation_for_release(tag, CONFIG, "master") is None


def test_newer_release_requires_configuration() -> None:
    """Uma minor futura falha explicitamente até a configuração ser atualizada."""
    with pytest.raises(ValueError, match="newer than configured"):
        rotation_for_release("v0.4.0", CONFIG, "master")


def test_already_rotated_preserves_previous_line(tmp_path: Path) -> None:
    """Reexecutar a rotação continua identificando PRs da minor publicada."""
    rotation = rotation_for_release("v0.3.0", CONFIG, "master")
    assert rotation is not None
    path = tmp_path / "lines.json"
    write_rotated_config(path, CONFIG, rotation)
    config = ReleaseLines.model_validate_json(path.read_bytes())
    repeated = rotation_for_release("v0.3.0", config, "master")
    assert repeated is not None
    assert repeated["previous_development_branch"] == "release/0.3"
    assert repeated["previous_development_milestone"] == "0.3"
    assert repeated["development_branch"] == "release/0.4"


def test_incomplete_rotation_is_not_accepted() -> None:
    """Uma linha avançada sem atualizar a manutenção não é rotação concluída."""
    config = CONFIG.model_copy(
        update={
            "development": CONFIG.development.model_copy(
                update={
                    "branch": "release/0.4",
                    "milestone": "0.4",
                }
            ),
        }
    )
    assert rotation_for_release("v0.3.0", config, "master") is None


@pytest.mark.parametrize(
    "payload", ["{}", "[]", "null", '{"release": []}', '{"release": {}}', "{"]
)
def test_main_reports_invalid_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: str,
) -> None:
    """Entradas inválidas retornam diagnóstico controlado, sem traceback."""
    event = tmp_path / "event.json"
    event.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["rotate_release_lines.py", str(event)])
    assert rotate_release_lines.main() == 1
    assert "invalid release event" in capsys.readouterr().err


@pytest.mark.parametrize("write", [False, True])
def test_main_valid_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    write: bool,
) -> None:
    """O CLI emite saídas e só grava a configuração quando solicitado."""
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"release": {"tag_name": "v0.3.0", "target_commitish": "master"}}),
        encoding="utf-8",
    )
    config = tmp_path / "lines.json"
    config.write_text(CONFIG.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(rotate_release_lines, "load_release_lines", lambda _: CONFIG)
    argv = ["rotate_release_lines.py", str(event)]
    if write:
        argv.extend(["--write", str(config)])
    monkeypatch.setattr(sys, "argv", argv)
    assert rotate_release_lines.main() == 0
    assert "rotate=true" in capsys.readouterr().out
    assert json.loads(config.read_text(encoding="utf-8"))["development"]["branch"] == (
        "release/0.4" if write else "release/0.3"
    )


def test_main_patch_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Um patch válido termina com sucesso sem produzir uma rotação."""
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"release": {"tag_name": "v0.3.1", "target_commitish": "master"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["rotate_release_lines.py", str(event)])
    assert rotate_release_lines.main() == 0
    assert capsys.readouterr().out.strip() == "rotate=false"
