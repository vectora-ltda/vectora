"""Testes da seleção de linhas de release orientada pela configuração."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from select_release_line import load_release_lines, selection_for_branch


def test_configured_branches_enable_release_workflows() -> None:
    """Branches configuradas de desenvolvimento e manutenção habilitam os jobs de promoção."""
    config = load_release_lines()

    assert selection_for_branch(config.development.branch, config)["enabled"] == "true"
    assert selection_for_branch(config.maintenance.branch, config)["enabled"] == "true"


def test_unknown_branch_skips_release_workflows() -> None:
    """Branches de feature não podem habilitar workflows de release que usam token."""
    config = load_release_lines()

    assert selection_for_branch("feat/untrusted", config)["enabled"] == "false"


def test_empty_branch_skips_release_workflows() -> None:
    """Um nome de branch vazio não pode autorizar a automação de release."""
    config = load_release_lines()
    assert selection_for_branch("", config)["enabled"] == "false"


def test_missing_branch_skips_release_workflows() -> None:
    """A ausência do nome da branch não pode autorizar a automação de release."""
    config = load_release_lines()
    assert selection_for_branch(None, config)["enabled"] == "false"


def test_invalid_configuration_is_rejected(tmp_path: Path) -> None:
    """Linhas de release inválidas ou duplicadas falham antes do uso pelo workflow."""
    path = tmp_path / "release-lines.json"
    path.write_text(
        json.dumps(
            {
                "development": {"branch": "same", "milestone": "0.2"},
                "maintenance": {"branch": "same", "milestone": "0.1.x"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must differ"):
        load_release_lines(path)
