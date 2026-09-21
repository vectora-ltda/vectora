"""Regression tests for generated changelog normalization."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast


def _load_normalizer() -> Callable[[Path], bool]:
    path = Path(__file__).parents[3] / "utils" / "normalize_changelog_lists.py"
    spec = importlib.util.spec_from_file_location("normalize_changelog_lists", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"não foi possível carregar {path}")
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)
    spec.loader.exec_module(module)
    return cast("Callable[[Path], bool]", module.normalize_changelog)


normalize_changelog: Callable[[Path], bool] = _load_normalizer()


def _run(tmp_path: Path, content: str) -> tuple[str, bool, bool]:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(content, encoding="utf-8", newline="")
    changed = normalize_changelog(path)
    second_changed = normalize_changelog(path)
    return path.read_text(encoding="utf-8"), changed, second_changed


def test_normalizes_lists_and_adds_introductions(tmp_path: Path) -> None:
    result, changed, second_changed = _run(
        tmp_path,
        "## [1.0.0]\n\n### Features\n\n* new feature\n\n### Bug Fixes\n\n* fixed bug\n",
    )

    assert changed is True
    assert second_changed is False
    assert "Esta seção reúne as alterações publicadas nesta versão." in result
    assert "Os recursos incluídos nesta versão são listados abaixo." in result
    assert "As correções incluídas nesta versão são listadas abaixo." in result
    assert "\n- new feature\n" in result
    assert "\n- fixed bug\n" in result


def test_preserves_backtick_and_tilde_fenced_code(tmp_path: Path) -> None:
    result, changed, _ = _run(
        tmp_path,
        "## [1.0.0]\n\n### Features\n\n````python\n* inside backticks\n```\n* still inside\n````\n\n~~~~text\n* inside tildes\n~~~\n* still inside\n~~~~\n\n* outside\n",
    )

    assert changed is True
    assert "* inside backticks" in result
    assert "* still inside\n````" in result
    assert "* inside tildes" in result
    assert "* still inside\n~~~~" in result
    assert "\n- outside\n" in result


def test_preserves_headings_and_lists_inside_fenced_code(tmp_path: Path) -> None:
    result, changed, _ = _run(
        tmp_path,
        "## [1.0.0]\n\n### Features\n\n```text\n### Example\n\n* inside code\n```\n\n* outside\n",
    )

    assert changed is True
    assert "### Example\n\n* inside code\n```" in result
    assert "Esta seção reúne as alterações publicadas nesta versão." in result
    assert "\n- outside\n" in result


def test_preserves_indented_code_list_markers(tmp_path: Path) -> None:
    result, changed, _ = _run(
        tmp_path,
        "## [1.0.0]\n\n### Features\n\n    * indented code\n\t* tabbed code\n* outside\n",
    )

    assert changed is True
    assert "    * indented code\n\t* tabbed code\n- outside\n" in result


def test_ignores_mixed_fence_delimiters(tmp_path: Path) -> None:
    result, changed, _ = _run(
        tmp_path,
        "## [1.0.0]\n\n### Features\n\n~~~text\n* inside code\n~~~```\n* still inside\n~~~\n* outside\n",
    )

    assert changed is True
    assert "* inside code\n~~~```\n* still inside\n~~~" in result
    assert "\n- outside\n" in result


def test_allows_opposite_delimiter_at_start_of_fence_info(tmp_path: Path) -> None:
    result, changed, _ = _run(
        tmp_path,
        "## [1.0.0]\n\n### Features\n\n~~~`python\n* inside tilde fence\n~~~\n\n```~~~\n* inside backtick fence\n```\n\n* outside\n",
    )

    assert changed is True
    assert "* inside tilde fence" in result
    assert "* inside backtick fence" in result
    assert "\n- outside\n" in result


def test_preserves_newline_style_and_unchanged_content(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_bytes(b"## [1.0.0]\r\n\r\nTexto introdutorio.\r\n\r\n- pronto\r\n")
    assert normalize_changelog(path) is False
    assert normalize_changelog(path) is False
    assert path.read_bytes().endswith(b"\r\n")
