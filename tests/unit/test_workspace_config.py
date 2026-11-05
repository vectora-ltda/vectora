"""Testes de src/services/workspace_config.py."""

from __future__ import annotations

from backend.workspace.workspace_config import (
    WORKSPACE_LOCAL_DIR,
    ensure_workspace_files,
    load_workspace_config,
)


def test_ensure_workspace_files_creates_toml_and_local_dir(tmp_path):
    ensure_workspace_files(tmp_path, name="meu-projeto")

    toml_path = tmp_path / "vectora.toml"
    local_dir = tmp_path / WORKSPACE_LOCAL_DIR

    assert toml_path.is_file()
    assert 'name = "meu-projeto"' in toml_path.read_text(encoding="utf-8")
    assert (local_dir / "plans").is_dir()
    assert (local_dir / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_ensure_workspace_files_is_idempotent(tmp_path):
    ensure_workspace_files(tmp_path, name="meu-projeto")
    toml_path = tmp_path / "vectora.toml"
    toml_path.write_text("# editado pelo usuário\n", encoding="utf-8")

    ensure_workspace_files(tmp_path, name="meu-projeto")

    assert toml_path.read_text(encoding="utf-8") == "# editado pelo usuário\n"


def test_ensure_workspace_files_skips_nonexistent_dir(tmp_path):
    missing = tmp_path / "does-not-exist"

    ensure_workspace_files(missing, name="x")

    assert not missing.exists()


def test_ensure_workspace_files_appends_gitignore_entry(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")

    ensure_workspace_files(tmp_path, name="meu-projeto")

    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in content
    assert ".vectora/" in content


def test_ensure_workspace_files_does_not_create_gitignore(tmp_path):
    ensure_workspace_files(tmp_path, name="meu-projeto")

    assert not (tmp_path / ".gitignore").exists()


def test_ensure_workspace_files_does_not_duplicate_gitignore_entry(tmp_path):
    (tmp_path / ".gitignore").write_text(".vectora/\n", encoding="utf-8")

    ensure_workspace_files(tmp_path, name="meu-projeto")

    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert content.splitlines().count(".vectora/") == 1
    assert "!.vectora/skills.lock.json" in content.splitlines()


def test_load_workspace_config_returns_none_when_missing(tmp_path):
    assert load_workspace_config(tmp_path) is None


def test_load_workspace_config_parses_and_resolves_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEUPROJETO_POSTGRES_DSN", "postgresql://x/y")
    (tmp_path / "vectora.toml").write_text(
        """
[workspace]
name = "demo"

[storage]
mode = "complete"
postgres_dsn = "${MEUPROJETO_POSTGRES_DSN}"
qdrant_collection = "demo_articles"

[rag]
chunk_size = 500

[agent]
allowed_models = ["claude-sonnet-4-6"]
""",
        encoding="utf-8",
    )

    cfg = load_workspace_config(tmp_path)

    assert cfg is not None
    assert cfg.workspace.name == "demo"
    assert cfg.storage.mode == "complete"
    assert cfg.storage.postgres_dsn == "postgresql://x/y"
    assert cfg.storage.qdrant_collection == "demo_articles"
    assert cfg.rag.chunk_size == 500
    assert cfg.agent.allowed_models == ["claude-sonnet-4-6"]


def test_load_workspace_config_reads_hooks_and_auto_commit(tmp_path):
    (tmp_path / "vectora.toml").write_text(
        """
[agent]
auto_commit = true

[hooks]
post_file_write = ["ruff format {file}", "prettier --write {file}"]
""",
        encoding="utf-8",
    )

    cfg = load_workspace_config(tmp_path)

    assert cfg is not None
    assert cfg.agent.auto_commit is True
    assert cfg.hooks.post_file_write == [
        "ruff format {file}",
        "prettier --write {file}",
    ]


def test_load_workspace_config_defaults_hooks_and_auto_commit_when_absent(tmp_path):
    """Edge — sem [hooks]/[agent].auto_commit no toml, defaults seguros (off)."""
    (tmp_path / "vectora.toml").write_text(
        '[workspace]\nname = "demo"\n', encoding="utf-8"
    )

    cfg = load_workspace_config(tmp_path)

    assert cfg is not None
    assert cfg.agent.auto_commit is False
    assert cfg.hooks.post_file_write == []


def test_load_workspace_config_returns_none_on_invalid_toml(tmp_path):
    (tmp_path / "vectora.toml").write_text("not valid toml [[[", encoding="utf-8")

    assert load_workspace_config(tmp_path) is None
