"""Regressions for the D1 compatibility preflight used by production deploys."""

from __future__ import annotations

import os
import runpy
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT: Path = Path(__file__).resolve().parents[2]


class _FakeEnvironment:
    def Command(self, *_args: object, **_kwargs: object) -> object:  # noqa: N802
        return object()

    def AlwaysBuild(self, *_args: object) -> None:  # noqa: N802
        return None

    def Alias(self, *_args: object) -> None:  # noqa: N802
        return None

    def Decider(self, *_args: object) -> None:  # noqa: N802
        return None


class _FakeDir:
    abspath = str(ROOT)


# runpy exposes dynamic SCons globals; no narrower namespace type is available.
def _load_sconstruct() -> dict[str, Any]:
    return runpy.run_path(
        str(ROOT.parent / "SConstruct"),
        run_name="__sconstruct__",
        init_globals={
            "AddOption": lambda *_args, **_kwargs: None,
            "Default": lambda *_args: None,
            "Dir": lambda _path: _FakeDir(),
            "Environment": lambda **_kwargs: _FakeEnvironment(),
        },
    )


def _run_preflight(
    monkeypatch: pytest.MonkeyPatch,
    *,
    table_output: str,
    pragma_output: str = '{"name":"id"}',
    table_returncode: int = 0,
) -> list[list[str]]:
    namespace = _load_sconstruct()
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        sql = command[command.index("--command") + 1]
        if "sqlite_master" in sql:
            return SimpleNamespace(returncode=table_returncode, stdout=table_output)
        return SimpleNamespace(returncode=0, stdout=pragma_output)

    def fake_upgrade(command: list[str], **_kwargs: object) -> None:
        commands.append(command)

    namespace["_upgrade_d1_schema"].__globals__["_run"] = fake_upgrade
    monkeypatch.setattr(subprocess, "run", fake_run)
    namespace["_upgrade_d1_schema"](
        SimpleNamespace(),
        skip_missing_tables=True,
        tables_filter={"skills_catalog"},
    )
    return commands


def test_existing_skills_catalog_gets_missing_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _run_preflight(
        monkeypatch,
        table_output='{"name":"skills_catalog"}',
    )

    alters = [command for command in commands if "ALTER TABLE" in command[-1]]
    assert len(alters) == 10
    assert any("ADD COLUMN package_name TEXT" in command[-1] for command in alters)


def test_missing_skills_catalog_is_left_for_base_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _run_preflight(monkeypatch, table_output="{}")

    assert not any("ALTER TABLE" in command[-1] for command in commands)


def test_table_probe_failure_aborts_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    namespace = _load_sconstruct()

    def failing_run(_command: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="authentication failed")

    namespace["_upgrade_d1_schema"].__globals__["_run"] = lambda *_args, **_kwargs: None
    monkeypatch.setattr(subprocess, "run", failing_run)

    with pytest.raises(SystemExit, match="existência da tabela"):
        namespace["_upgrade_d1_schema"](
            SimpleNamespace(),
            skip_missing_tables=True,
            tables_filter={"skills_catalog"},
        )


def test_workflow_gates_schema_on_preflight() -> None:
    workflow = (ROOT.parent / ".github" / "workflows" / "edge.yml").read_text(
        encoding="utf-8",
    )

    assert "id: services_schema_catalog_preflight" in workflow
    assert "steps.services_schema_catalog_preflight.outcome == 'success'" in workflow
    assert "bash scripts/d1_catalog_preflight.sh" in workflow
    assert "services_schema_legacy_catalog" not in workflow


@pytest.mark.skipif(os.name == "nt", reason="bash script test runs on Unix CI")
def test_workflow_script_upgrades_existing_catalog(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls.txt"
    fake_pnpm = fake_bin / "pnpm"
    fake_pnpm.write_text(
        """#!/usr/bin/env sh
case "$*" in
  *sqlite_master*) printf '{"name":"skills_catalog"}' ;;
  *table_info*) printf '{"name":"id"}' ;;
  *ALTER*) printf '%s\\n' "$*" >> "$FAKE_CALLS" ;;
esac
""",
        encoding="utf-8",
    )
    fake_pnpm.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["FAKE_CALLS"] = str(calls)
    bash_path = shutil.which("bash")
    assert bash_path is not None

    result = subprocess.run(  # noqa: S603
        [bash_path, str(ROOT.parent / "services/scripts/d1_catalog_preflight.sh")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 10


@pytest.mark.skipif(os.name == "nt", reason="bash script test runs on Unix CI")
def test_workflow_script_stops_on_table_probe_failure(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_pnpm = fake_bin / "pnpm"
    fake_pnpm.write_text(
        """#!/usr/bin/env sh
case "$*" in
  *sqlite_master*) printf 'authentication failed\\n' >&2; exit 7 ;;
esac
""",
        encoding="utf-8",
    )
    fake_pnpm.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    bash_path = shutil.which("bash")
    assert bash_path is not None

    result = subprocess.run(  # noqa: S603
        [bash_path, str(ROOT.parent / "services/scripts/d1_catalog_preflight.sh")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "Falha ao consultar a existência" in result.stderr
