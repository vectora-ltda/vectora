"""Testes de utils/release_notes.py: o corpo da release vem do changelog."""

from __future__ import annotations

from pathlib import Path

import pytest
import release_notes
from release_notes import section_for_version

CHANGELOG = """# Changelog

## [0.1.24] (2026-11-04)

Esta seção reúne as alterações publicadas nesta versão.

### Features

- feat: algo novo (#1)

### Bug Fixes

- fix: algo corrigido (#2)

## [0.1.23] (2026-09-27)

### Features

- feat: antigo (#3)

## Capacidades anteriores

- nada disso entra
"""


def test_returns_the_section_of_the_version_without_its_heading() -> None:
    section = section_for_version(CHANGELOG, "0.1.24")

    assert section is not None
    assert section.startswith("Esta seção reúne")
    assert "feat: algo novo" in section
    assert "fix: algo corrigido" in section
    assert "## [0.1.24]" not in section


def test_stops_at_the_next_second_level_heading() -> None:
    """Verifica que a extração não inclui notas da versão seguinte."""
    older = section_for_version(CHANGELOG, "0.1.23")

    assert older == "### Features\n\n- feat: antigo (#3)"
    assert "nada disso entra" not in (older or "")
    assert "algo novo" not in (older or "")


def test_unknown_version_returns_none() -> None:
    assert section_for_version(CHANGELOG, "9.9.9") is None


def test_version_is_matched_literally_not_as_a_pattern() -> None:
    assert section_for_version(CHANGELOG, "0.3.x") is None
    assert section_for_version(CHANGELOG, "0.3") is None
    assert section_for_version(CHANGELOG, ".*") is None


def test_empty_section_returns_none() -> None:
    assert (
        section_for_version("## [1.0.0] (2026-01-01)\n\n## [0.9.0]\n- x", "1.0.0")
        is None
    )


def test_main_prints_the_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")

    code = release_notes.main([str(changelog), "0.1.24"])

    assert code == 0
    assert "feat: algo novo" in capsys.readouterr().out


def test_main_fails_when_the_version_has_no_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")

    assert release_notes.main([str(changelog), "9.9.9"]) == 1
    assert "9.9.9" in capsys.readouterr().err


def test_main_fails_when_the_changelog_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert release_notes.main([str(tmp_path / "nao-existe.md"), "1.0.0"]) == 1
    assert "nao-existe.md" in capsys.readouterr().err


def test_main_rejects_wrong_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert release_notes.main(["so-um-argumento"]) == 2
    assert "uso" in capsys.readouterr().err.lower()
