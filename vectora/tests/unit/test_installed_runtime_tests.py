"""Regression coverage for the on-demand installed-app test runner."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.cli import runtime_tests


def test_missing_bundled_suite_fails_before_downloading_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = tmp_path / "missing-suite.py"
    uv = tmp_path / "uv.exe"
    monkeypatch.setattr(runtime_tests, "_runtime_test_assets", lambda: (suite, uv))
    monkeypatch.setattr(
        runtime_tests.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("runner must not start"),
    )

    assert runtime_tests.run_installed_runtime_tests() == 2


def test_missing_uv_fails_without_falling_back_to_a_global_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = tmp_path / "test_installed_runtime.py"
    suite.write_text("# packaged suite", encoding="utf-8")
    (suite.parent / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    uv = tmp_path / "missing-uv.exe"
    monkeypatch.setattr(runtime_tests, "_runtime_test_assets", lambda: (suite, uv))
    monkeypatch.setattr(
        runtime_tests.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("runner must not start"),
    )

    assert runtime_tests.run_installed_runtime_tests() == 2


def test_missing_isolated_pytest_config_fails_before_runner_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = tmp_path / "test_installed_runtime.py"
    suite.write_text("# packaged suite", encoding="utf-8")
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"uv")
    monkeypatch.setattr(runtime_tests, "_runtime_test_assets", lambda: (suite, uv))
    monkeypatch.setattr(
        runtime_tests.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("runner must not start"),
    )

    assert runtime_tests.run_installed_runtime_tests() == 2


def test_source_mode_requires_an_installed_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = tmp_path / "test_installed_runtime.py"
    suite.write_text("# packaged suite", encoding="utf-8")
    (suite.parent / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"uv")
    monkeypatch.setattr(runtime_tests, "_runtime_test_assets", lambda: (suite, uv))
    monkeypatch.setattr(runtime_tests.sys, "frozen", False, raising=False)
    monkeypatch.delenv("VECTORA_INSTALLED_EXECUTABLE", raising=False)
    monkeypatch.setattr(runtime_tests.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        runtime_tests.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("runner must not start"),
    )

    assert runtime_tests.run_installed_runtime_tests() == 2
    assert "VECTORA_INSTALLED_EXECUTABLE" in capsys.readouterr().err


@pytest.mark.parametrize("exit_code", [0, 1, 7])
def test_runner_uses_isolated_test_dependencies_and_propagates_result(
    exit_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite = tmp_path / "test_installed_runtime.py"
    uv = tmp_path / "uv.exe"
    suite.write_text("# packaged suite", encoding="utf-8")
    (suite.parent / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    uv.write_bytes(b"uv test binary placeholder")
    captured: dict[str, object] = {}
    monkeypatch.setattr(runtime_tests.sys, "frozen", True, raising=False)

    def fake_run(
        command: list[str], *, env: dict[str, str], check: bool
    ) -> SimpleNamespace:
        captured["command"] = command
        captured["env"] = env
        captured["check"] = check
        return SimpleNamespace(returncode=exit_code)

    monkeypatch.setattr(runtime_tests, "_runtime_test_assets", lambda: (suite, uv))
    monkeypatch.setattr(runtime_tests.subprocess, "run", fake_run)

    assert runtime_tests.run_installed_runtime_tests() == exit_code
    command = captured["command"]
    assert isinstance(command, list)
    assert command[:5] == [str(uv), "run", "--isolated", "--no-project", "--python"]
    assert "pytest==9.1.1" in command
    assert "pytest-timeout==2.4.0" in command
    assert str(suite.parent / "pytest.ini") in command
    assert command[-1] == str(suite)
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["VECTORA_INSTALLED_EXECUTABLE"] == str(Path(sys.executable).resolve())
    assert env["UV_CACHE_DIR"]
    assert captured["check"] is False
