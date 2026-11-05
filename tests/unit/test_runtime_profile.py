"""Regression tests for runtime profile isolation."""

from pathlib import Path

from backend.runtime_profile import (
    normalize_runtime_home,
    resolve_runtime_home,
    runtime_home_for_profile,
    sanitize_runtime_profile,
)


def test_profile_sanitization_is_stable_and_non_empty() -> None:
    assert sanitize_runtime_profile(" dev/0.2.x ") == "dev-0-2-x"
    assert sanitize_runtime_profile("../") == "---"
    assert sanitize_runtime_profile("   ", "dev") == "dev"


def test_profile_home_mapping_keeps_named_profiles_isolated(tmp_path: Path) -> None:
    assert runtime_home_for_profile("stable", tmp_path) == tmp_path / ".vectora"
    assert runtime_home_for_profile("dev", tmp_path) == tmp_path / ".vectora-dev"
    assert (
        runtime_home_for_profile("preview", tmp_path) == tmp_path / ".vectora-preview"
    )


def test_explicit_home_and_profile_only_resolution(tmp_path: Path) -> None:
    assert normalize_runtime_home("~/instance", tmp_path) == tmp_path / "instance"
    assert (
        resolve_runtime_home(
            {"VECTORA_RUNTIME_PROFILE": "preview"}, home_directory=tmp_path
        )
        == tmp_path / ".vectora-preview"
    )
    assert (
        resolve_runtime_home(
            {"VECTORA_HOME": "profiles/instance"}, home_directory=tmp_path
        )
        == (tmp_path / "profiles/instance").resolve()
    )
