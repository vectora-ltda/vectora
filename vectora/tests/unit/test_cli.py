"""Tests — CLI operacional (backend/cli) e parser do main.

Cobre o parser pós-remoção do TUI (sem chat/server, com start/config) e os
comandos novos de operação (config keys/docker/qdrant/redis), com 1 happy + 1
erro por comando conforme o padrão de TDD do projeto.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console as _Console

from backend.cli import infra, keys, marketplace
from backend.main import _build_parser


def _null_console(*_a: object, **_kw: object) -> _Console:
    return _Console(file=io.StringIO())


# ---------------------------------------------------------------------------
# Parser — TUI removido, start/config presentes
# ---------------------------------------------------------------------------


def test_parser_sem_subcomando_nao_define_command():
    parser = _build_parser()
    args = parser.parse_args([])
    assert getattr(args, "command", None) is None


def test_parser_remove_chat_e_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stderr", io.StringIO())
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["chat"])
    with pytest.raises(SystemExit):
        parser.parse_args(["server", "web"])


def test_parser_start_headless_e_porta():
    parser = _build_parser()
    args = parser.parse_args(["start", "--headless", "--port", "9000"])
    assert args.command == "start"
    assert args.headless is True
    assert args.port == 9000


def test_parser_config_keys_docker_qdrant_redis():
    parser = _build_parser()
    assert parser.parse_args(["config", "keys"]).config_action == "keys"

    docker = parser.parse_args(["config", "docker", "up"])
    assert docker.config_action == "docker"
    assert docker.config_arg == "up"

    qdrant = parser.parse_args(
        ["config", "qdrant", "https://q.example", "--api-key", "k"]
    )
    assert qdrant.config_action == "qdrant"
    assert qdrant.config_arg == "https://q.example"
    assert qdrant.api_key == "k"

    redis = parser.parse_args(["config", "redis", "redis://localhost:6379/0"])
    assert redis.config_action == "redis"
    assert redis.config_arg == "redis://localhost:6379/0"


def test_parser_config_sem_acao_aceita_set():
    parser = _build_parser()
    args = parser.parse_args(["config", "--set", "verbosity=2"])
    assert args.config_action is None
    assert args.set_values == ["verbosity=2"]


def test_parser_marketplace_define_matriz_de_comandos() -> None:
    parser = _build_parser()
    assert parser.parse_args(["mcp", "list", "--output", "json"]).output == "json"
    assert parser.parse_args(["mcp", "install", "github"]).identifier == "github"
    assert parser.parse_args(["skills", "search", "rag"]).query == "rag"
    publish = parser.parse_args(
        ["skills", "publish", "src", "Name", "Description", "--tag", "ai"]
    )
    assert publish.tags == ["ai"]


def test_marketplace_json_envelope_nao_vaza_caminho(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = marketplace._emit(
        marketplace._envelope(
            "ok",
            marketplace._public_skill({"path": "C:/private/.vectora", "name": "demo"}),
        ),
        "json",
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "1"
    assert "path" not in payload["data"]


def test_mcp_install_remove_reutiliza_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _empty_registry() -> list[object]:
        return []

    async def _install(request: object, user_id: str) -> dict[str, str]:
        calls.append(f"install:{user_id}")
        return {"status": "installed", "mcp_id": "github"}

    async def _remove(request: object, user_id: str) -> dict[str, str]:
        calls.append(f"remove:{user_id}")
        return {"status": "removed", "mcp_id": "github"}

    monkeypatch.setattr(marketplace.mcp_marketplace, "install_mcp", _install)
    monkeypatch.setattr(marketplace.mcp_marketplace, "uninstall_mcp", _remove)
    monkeypatch.setattr(
        marketplace.mcp_marketplace,
        "list_registry",
        _empty_registry,
    )
    install_args = SimpleNamespace(action="install", identifier="github", query=None)
    remove_args = SimpleNamespace(action="remove", identifier="github", query=None)
    assert asyncio.run(marketplace._mcp(install_args))["status"] == "ok"
    assert asyncio.run(marketplace._mcp(remove_args))["status"] == "ok"
    assert calls == ["install:local", "remove:local"]


# ---------------------------------------------------------------------------
# keys.upsert_env_key — escreve e atualiza idempotente
# ---------------------------------------------------------------------------


def test_upsert_env_key_insere_e_atualiza(tmp_path: Path):
    env = tmp_path / ".env"

    keys.upsert_env_key(env, "FOO", "1")
    assert "FOO=1" in env.read_text(encoding="utf-8")

    keys.upsert_env_key(env, "BAR", "2")
    body = env.read_text(encoding="utf-8")
    assert "FOO=1" in body
    assert "BAR=2" in body

    # Atualização: FOO muda de valor sem duplicar a linha.
    keys.upsert_env_key(env, "FOO", "9")
    body = env.read_text(encoding="utf-8")
    assert "FOO=9" in body
    assert "FOO=1" not in body
    assert body.count("FOO=") == 1


# ---------------------------------------------------------------------------
# infra.run_docker — happy (ok) e erro (not ok → SystemExit)
# ---------------------------------------------------------------------------


def test_run_docker_status_ok(monkeypatch, capsys):
    from backend.storage.dev_stack import StackResult

    monkeypatch.setattr(
        "backend.storage.dev_stack.stack_status",
        lambda: StackResult(ok=True, messages=["vectora-postgres: rodando"]),
    )
    infra.run_docker("status")
    assert "rodando" in capsys.readouterr().out


def test_run_docker_falha_sai_com_erro(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.storage.dev_stack import StackResult

    monkeypatch.setattr("backend.cli.infra.Console", _null_console)
    monkeypatch.setattr(
        "backend.storage.dev_stack.stack_up",
        lambda: StackResult(ok=False, messages=["Docker não encontrado"]),
    )
    with pytest.raises(SystemExit):
        infra.run_docker("up")


# ---------------------------------------------------------------------------
# infra.run_qdrant / run_redis — happy (persiste) e erro (conexão falha)
# ---------------------------------------------------------------------------


def test_run_qdrant_ok_persiste_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from backend.workspace import runtime_settings as rs_module
    from backend.workspace.runtime_settings import RuntimeSettings

    monkeypatch.setattr("backend.cli.infra.Console", _null_console)
    monkeypatch.setattr(infra.settings, "vectora_home", tmp_path)

    fresh = RuntimeSettings(path=tmp_path / "checkpoints.db")
    monkeypatch.setattr(rs_module, "runtime_settings", fresh)

    async def _ok(url: str, api_key: str | None) -> None:
        return None

    monkeypatch.setattr(infra, "_test_qdrant", _ok)
    infra.run_qdrant("https://q.example", "secret")

    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "QDRANT_URL=https://q.example" in env
    assert "QDRANT_API_KEY=secret" in env
    # storage_mode agora vive em app_settings (SQLite), não no .env.
    assert fresh.storage_mode == "complete"


def test_run_qdrant_sem_url_sai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.cli.infra.Console", _null_console)
    with pytest.raises(SystemExit):
        infra.run_qdrant("", None)


def test_run_redis_falha_conexao_sai(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("backend.cli.infra.Console", _null_console)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    async def _boom(url: str) -> None:
        raise ConnectionError("recusado")

    monkeypatch.setattr(infra, "_test_redis", _boom)
    with pytest.raises(SystemExit):
        infra.run_redis("redis://localhost:6379/0")
