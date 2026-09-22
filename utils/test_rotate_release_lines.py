"""Testes das decisões de rotação e das gravações da configuração."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
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
    """Uma tag publicada de desenvolvimento avança as linhas de manutenção e desenvolvimento."""
    rotation = rotation_for_release("v0.2.0", CONFIG, "master")

    assert rotation is not None
    assert rotation["maintenance_branch"] == "release/0.2"
    assert rotation["maintenance_milestone"] == "0.2.x"
    assert rotation["development_milestone"] == "0.3"
    assert rotation["previous_maintenance_branch"] == "release/0.1"


def test_unrelated_tag_does_not_rotate() -> None:
    """Tags fora da linha ativa de desenvolvimento são ignoradas com segurança."""
    assert rotation_for_release("v0.1.23", CONFIG, "master") is None
    assert rotation_for_release("v0.2.0", CONFIG, "release/0.1") is None
    assert rotation_for_release("v0.2.1", CONFIG, "master") is None
    assert rotation_for_release("", CONFIG, "master") is None
    assert rotation_for_release("v0.2.0", CONFIG, "") is None


def test_release_without_target_is_ignored() -> None:
    """Um evento sem branch alvo explícita não pode autorizar a rotação."""
    assert rotation_for_release("v0.2.0", CONFIG, None) is None


def test_newer_release_fails_until_configuration_rotation_is_merged() -> None:
    """Um release mais novo falha em vez de ser silenciosamente ignorado."""
    with pytest.raises(ValueError, match="newer than configured"):
        rotation_for_release("v0.3.0", CONFIG, "master")


def test_rotation_writes_next_config(tmp_path: Path) -> None:
    """Uma rotação válida persiste o próximo mapeamento de branch e milestone."""
    rotation = rotation_for_release("0.2.0", CONFIG, "master")
    assert rotation is not None
    path = tmp_path / "release-lines.json"

    write_rotated_config(path, CONFIG, rotation)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "development": {"branch": "master", "milestone": "0.3"},
        "maintenance": {"branch": "release/0.2", "milestone": "0.2.x"},
    }


def test_rotation_plan_executes_all_success_paths() -> None:
    """O plano cobre branch, milestones, metadados de PR e publicação."""
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
    """PRs não relacionadas não produzem operações de atualização da API."""
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


def test_main_rotates_valid_release_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """O ponto de entrada emite a rotação para um evento válido."""
    from rotate_release_lines import main

    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "release": {
                    "tag_name": "v0.2.0",
                    "target_commitish": "master",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["rotate_release_lines.py", str(event)])

    assert main() == 0
    assert "rotate=true" in capsys.readouterr().out


def test_main_ignores_valid_non_rotating_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """O ponto de entrada informa quando um evento válido não exige rotação."""
    from rotate_release_lines import main

    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"release": {"tag_name": "v0.1.23", "target_commitish": "master"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["rotate_release_lines.py", str(event)])

    assert main() == 0
    assert capsys.readouterr().out.strip() == "rotate=false"


def test_main_writes_valid_rotation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O ponto de entrada grava a configuração quando solicitado."""
    from rotate_release_lines import main

    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"release": {"tag_name": "v0.2.0", "target_commitish": "master"}}),
        encoding="utf-8",
    )
    config = tmp_path / "release-lines.json"
    config.write_text(json.dumps(CONFIG), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["rotate_release_lines.py", str(event), "--write", str(config)],
    )

    assert main() == 0
    assert (
        json.loads(config.read_text(encoding="utf-8"))["development"]["milestone"]
        == "0.3"
    )


def test_invalid_release_event_returns_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uma entrada de release inválida falha antes que qualquer rotação seja publicada."""
    from rotate_release_lines import main

    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["rotate_release_lines.py", str(event)])

    assert main() == 1
