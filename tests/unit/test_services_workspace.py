"""Tests para src/services/workspace.py — WorkspaceRegistry."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.workspace.workspace import Workspace, WorkspaceRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_registry(tmp_path: Path) -> Iterator[WorkspaceRegistry]:
    """Cria um registry novo com workspaces.json em tmp_path."""
    reg = WorkspaceRegistry()
    json_file = tmp_path / "workspaces.json"
    with patch("backend.workspace.workspace._workspaces_file", return_value=json_file):
        # Resetar estado interno
        reg._workspaces = {}
        reg._loaded = False
        yield reg


@pytest.fixture
def registry(tmp_path: Path):
    """Registry isolado com arquivo temporário."""
    json_file = tmp_path / "workspaces.json"
    reg = WorkspaceRegistry()
    reg._workspaces = {}
    reg._loaded = False
    # Patch o arquivo global
    with patch("backend.workspace.workspace._workspaces_file", return_value=json_file):
        yield reg, json_file


# ---------------------------------------------------------------------------
# derive_id
# ---------------------------------------------------------------------------


def test_derive_id_is_deterministic(tmp_path: Path):
    """Mesmo caminho sempre gera mesmo ID."""
    cwd = str(tmp_path)
    id1 = WorkspaceRegistry.derive_id(cwd)
    id2 = WorkspaceRegistry.derive_id(cwd)
    assert id1 == id2


def test_derive_id_length():
    """ID tem exatamente 8 caracteres."""
    wid = WorkspaceRegistry.derive_id("/some/path")
    assert len(wid) == 8


def test_derive_id_different_paths():
    """Caminhos diferentes geram IDs diferentes."""
    id1 = WorkspaceRegistry.derive_id("/project/a")
    id2 = WorkspaceRegistry.derive_id("/project/b")
    assert id1 != id2


def test_derive_id_normalizes_path():
    """Paths equivalentes mas escritos diferente geram mesmo ID."""
    id1 = WorkspaceRegistry.derive_id("/project/a")
    id2 = WorkspaceRegistry.derive_id(str(Path("/project/a").resolve()))
    assert id1 == id2


# ---------------------------------------------------------------------------
# get_or_create
# ---------------------------------------------------------------------------


def test_get_or_create_creates_new(registry):
    """Cria workspace novo para diretório desconhecido."""
    reg, _ = registry
    ws = reg.get_or_create("/new/project")
    assert ws.id == WorkspaceRegistry.derive_id("/new/project")
    assert ws.name == "project"
    assert "/new/project" in ws.cwd or "new" in ws.cwd  # normalizado


def test_get_or_create_returns_existing(registry):
    """Retorna workspace existente na segunda chamada."""
    reg, _ = registry
    ws1 = reg.get_or_create("/my/project")
    ws2 = reg.get_or_create("/my/project")
    assert ws1.id == ws2.id


def test_get_or_create_persists(registry, tmp_path: Path):
    """Workspace persiste em workspaces.json."""
    reg, json_file = registry
    reg.get_or_create(str(tmp_path))
    assert json_file.exists()
    data = json.loads(json_file.read_text())
    assert len(data["workspaces"]) == 1


# ---------------------------------------------------------------------------
# create_remote
# ---------------------------------------------------------------------------


def test_create_remote_ssh_registers_workspace(registry):
    """transport="ssh" cria workspace com placeholder local e persiste."""
    reg, json_file = registry
    ws = reg.create_remote(
        name="meu-servidor",
        transport="ssh",
        remote_host="10.0.0.5",
        remote_path="/srv/app",
    )
    assert ws.transport == "ssh"
    assert ws.remote_host == "10.0.0.5"
    assert reg.get(ws.id) is not None
    assert json_file.exists()


def test_create_remote_same_host_returns_existing(registry):
    """Chamar create_remote duas vezes com o mesmo host é idempotente."""
    reg, _ = registry
    ws1 = reg.create_remote(name="a", transport="ssh", remote_host="10.0.0.5")
    ws2 = reg.create_remote(name="a", transport="ssh", remote_host="10.0.0.5")
    assert ws1.id == ws2.id


def test_create_remote_rejects_invalid_transport(registry):
    """transport fora de {"ssh", "codespace"} levanta ValueError — a
    validação em runtime é a última linha de defesa quando quem chama
    contorna a checagem estática do type checker."""
    reg, _ = registry
    with pytest.raises(ValueError, match="transport remoto inválido"):
        reg.create_remote(name="x", transport="local")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_returns_none_for_unknown(registry):
    """Retorna None para workspace_id desconhecido."""
    reg, _ = registry
    assert reg.get("ffffffff") is None


def test_get_after_create(registry):
    """Retorna workspace após criação."""
    reg, _ = registry
    ws = reg.get_or_create("/some/dir")
    found = reg.get(ws.id)
    assert found is not None
    assert found.id == ws.id


def test_get_does_not_clobber_in_memory_entry_with_stale_disk_data(
    registry, tmp_path: Path
):
    """Uma entrada já presente em `_workspaces` (registrada antes do
    primeiro `_load()`) não pode ser sobrescrita pelo `workspaces.json` em
    disco quando os dois têm o mesmo id — o disco só preenche o que ainda
    não existe em memória, nunca substitui um registro mais recente."""
    reg, json_file = registry
    stale_cwd = str(tmp_path / "stale-deleted-dir")
    json_file.write_text(
        json.dumps(
            {
                "workspaces": [
                    {
                        "id": "ws-fixed",
                        "name": "old",
                        "cwd": stale_cwd,
                        "created_at": "2020-01-01T00:00:00+00:00",
                        "trusted": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    fresh_cwd = str(tmp_path / "fresh-live-dir")
    reg._workspaces["ws-fixed"] = Workspace(
        id="ws-fixed",
        name="fresh",
        cwd=fresh_cwd,
        created_at="2026-01-01T00:00:00+00:00",
        trusted=True,
    )

    found = reg.get("ws-fixed")

    assert found is not None
    assert found.cwd == fresh_cwd


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


def test_list_all_empty(registry):
    """Retorna lista vazia se não houver workspaces."""
    reg, _ = registry
    assert reg.list_all() == []


def test_list_all_multiple(registry, tmp_path: Path):
    """Retorna todos os workspaces registrados."""
    reg, _ = registry
    reg.get_or_create(str(tmp_path / "a"))
    reg.get_or_create(str(tmp_path / "b"))
    all_ws = reg.list_all()
    assert len(all_ws) == 2


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


def test_rename_existing(registry):
    """Renomeia workspace existente."""
    reg, _ = registry
    ws = reg.get_or_create("/project")
    result = reg.rename(ws.id, "meu-projeto")
    assert result is True
    assert reg.get(ws.id).name == "meu-projeto"


def test_rename_nonexistent(registry):
    """Retorna False para workspace inexistente."""
    reg, _ = registry
    assert reg.rename("00000000", "novo") is False


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_existing(registry):
    """Remove workspace do registry."""
    reg, _ = registry
    ws = reg.get_or_create("/to-delete")
    assert reg.delete(ws.id) is True
    assert reg.get(ws.id) is None


def test_delete_nonexistent(registry):
    """Retorna False para workspace inexistente."""
    reg, _ = registry
    assert reg.delete("00000000") is False


# ---------------------------------------------------------------------------
# bump_version
# ---------------------------------------------------------------------------


def test_bump_version_increments(registry):
    """Incrementa manifest_version a cada chamada."""
    reg, _ = registry
    ws = reg.get_or_create("/project")
    assert ws.manifest_version == 0
    v1 = reg.bump_version(ws.id)
    assert v1 == 1
    v2 = reg.bump_version(ws.id)
    assert v2 == 2


def test_bump_version_unknown(registry):
    """Retorna 0 para workspace inexistente."""
    reg, _ = registry
    assert reg.bump_version("xxxxxxxx") == 0


# ---------------------------------------------------------------------------
# Workspace paths
# ---------------------------------------------------------------------------


def test_workspace_manifest_path():
    """manifest_path() retorna caminho esperado."""
    ws = Workspace(id="abc12345", name="test", cwd="/test", created_at="")
    path = ws.manifest_path()
    assert "abc12345" in str(path)
    assert path.name == "MANIFEST.md"


def test_workspace_bucket_manifest_path():
    """bucket_manifest_path() retorna caminho esperado."""
    ws = Workspace(id="abc12345", name="test", cwd="/test", created_at="")
    path = ws.bucket_manifest_path("code")
    assert "abc12345" in str(path)
    assert path.name == "code.md"


# ---------------------------------------------------------------------------
# Persistência round-trip
# ---------------------------------------------------------------------------


def test_persistence_roundtrip(registry, tmp_path: Path):
    """Workspace sobrevive a recriação do registry (deserialização)."""
    reg, json_file = registry
    ws = reg.get_or_create(str(tmp_path))
    reg.rename(ws.id, "meu-projeto")

    # Cria novo registry carregando do mesmo arquivo
    reg2 = WorkspaceRegistry()
    reg2._workspaces = {}
    reg2._loaded = False
    with patch("backend.workspace.workspace._workspaces_file", return_value=json_file):
        loaded = reg2.get(ws.id)
    assert loaded is not None
    assert loaded.name == "meu-projeto"
    assert loaded.id == ws.id
