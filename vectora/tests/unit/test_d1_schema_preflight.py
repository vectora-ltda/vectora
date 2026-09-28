"""Regressions for the D1 compatibility preflight used by production deploys."""

from __future__ import annotations

import os
import runpy
import shutil
import sqlite3
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
    table: str = "skills_catalog",
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
        tables_filter={table},
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
    assert any(
        "UPDATE skills_catalog SET updated_at" in command[-1] for command in commands
    )
    assert any(
        "CREATE TRIGGER IF NOT EXISTS skills_catalog_updated_at_default" in command[-1]
        for command in commands
    )


def test_missing_skills_catalog_is_left_for_base_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _run_preflight(monkeypatch, table_output="{}")

    assert not any("ALTER TABLE" in command[-1] for command in commands)


def test_existing_mcp_catalog_gets_timestamp_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _run_preflight(
        monkeypatch,
        table_output='{"name":"mcp_catalog"}',
        table="mcp_catalog",
    )

    assert any(
        "UPDATE mcp_catalog SET updated_at = datetime('now')" in command[-1]
        for command in commands
    )
    assert any(
        "CREATE TRIGGER IF NOT EXISTS mcp_catalog_updated_at_default" in command[-1]
        for command in commands
    )


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
    assert 'ensure_column gha_bot_review_jobs repository "TEXT"' in workflow
    assert "services_schema_legacy_catalog" not in workflow


def test_catalog_timestamp_commands_use_configured_d1_target() -> None:
    script = (
        ROOT.parent / "services" / "scripts" / "d1_catalog_preflight.sh"
    ).read_text(
        encoding="utf-8",
    )

    assert script.count("pnpm --silent wrangler d1 execute vectora-db --remote") == 2
    assert 'd1 execute "$DB_NAME" "$REMOTE_FLAG"' not in script


def test_legacy_timestamp_is_backfilled_and_defaulted() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE skills_catalog (id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO skills_catalog (id) VALUES ('legacy')")
    connection.execute(
        "INSERT INTO skills_catalog (id) VALUES ('existing')",
    )
    connection.execute("ALTER TABLE skills_catalog ADD COLUMN updated_at TEXT")
    connection.execute(
        "UPDATE skills_catalog SET updated_at = '2024-01-02 03:04:05' "
        "WHERE id = 'existing'",
    )

    connection.execute(
        "UPDATE skills_catalog SET updated_at = datetime('now') "
        "WHERE updated_at IS NULL",
    )
    connection.execute(
        "CREATE TRIGGER IF NOT EXISTS skills_catalog_updated_at_default "
        "AFTER INSERT ON skills_catalog WHEN NEW.updated_at IS NULL BEGIN "
        "UPDATE skills_catalog SET updated_at = datetime('now') "
        "WHERE id = NEW.id AND updated_at IS NULL; END",
    )
    connection.execute("INSERT INTO skills_catalog (id) VALUES ('new')")

    timestamps = connection.execute(
        "SELECT id, updated_at FROM skills_catalog ORDER BY id",
    ).fetchall()
    assert timestamps[0] == ("existing", "2024-01-02 03:04:05")
    assert all(timestamp and timestamp[1] for timestamp in timestamps)


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
  *UPDATE*) printf '%s\\n' "$*" >> "$FAKE_CALLS" ;;
  *CREATE\\ TRIGGER*) printf '%s\\n' "$*" >> "$FAKE_CALLS" ;;
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
    recorded = calls.read_text(encoding="utf-8").splitlines()
    assert len([line for line in recorded if "ALTER" in line]) == 10
    assert any("UPDATE skills_catalog SET updated_at" in line for line in recorded)
    assert any(
        "CREATE TRIGGER IF NOT EXISTS skills_catalog_updated_at_default" in line
        for line in recorded
    )
    trigger_index = next(
        index
        for index, line in enumerate(recorded)
        if "CREATE TRIGGER IF NOT EXISTS skills_catalog_updated_at_default" in line
    )
    update_index = next(
        index
        for index, line in enumerate(recorded)
        if "--command UPDATE skills_catalog SET updated_at" in line
    )
    assert trigger_index < update_index


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
