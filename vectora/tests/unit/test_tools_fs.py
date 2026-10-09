"""Tests for src/tools/fs.py"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.tools.context import ctx_from_config
from backend.vtypes import Workspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def trusted_ws(tmp_path, monkeypatch):
    """Workspace confiável apontando para tmp_path + config para as tools.

    As tools de fs confinam toda operação ao workspace ativo. Os testes
    registram um workspace confiável em tmp_path e passam seu id no config para
    que leituras/escritas ocorram dentro da pasta de teste.
    """
    from backend.workspace import workspace as ws_mod

    ws = Workspace(
        id="testws",
        name="testws",
        cwd=str(tmp_path),
        created_at="2024-01-01T00:00:00+00:00",
        trusted=True,
        # Hooks pedem aprovação própria (distinta de trust) — testes que não
        # exercem esse gate especificamente já rodam "aprovados", como no
        # comportamento anterior à introdução do gate.
        hooks_approved=True,
    )
    monkeypatch.setattr(
        ws_mod.workspace_registry,
        "get",
        lambda wid: ws if wid == "testws" else None,
    )
    monkeypatch.setattr(
        ws_mod.workspace_registry,
        "get_or_create",
        lambda cwd=None: ws,
    )
    return {"configurable": {"workspace_id": "testws"}}


# ---------------------------------------------------------------------------
# file_read
# ---------------------------------------------------------------------------


class TestFileRead:
    async def test_reads_existing_file(self, tmp_path, trusted_ws):
        from backend.tools.fs import file_read

        f = tmp_path / "hello.txt"
        f.write_text("conteudo do arquivo", encoding="utf-8")
        result = await file_read(file_path=str(f), ctx=ctx_from_config(trusted_ws))
        assert result == "conteudo do arquivo"

    async def test_file_not_found(self, tmp_path, trusted_ws):
        from backend.tools.fs import file_read

        result = await file_read(
            file_path=str(tmp_path / "nao_existe.txt"), ctx=ctx_from_config(trusted_ws)
        )
        assert "not found" in result.lower() or "error" in result.lower()

    async def test_blocked_path(self, tmp_path, trusted_ws):
        from backend.tools.fs import file_read

        outside = tmp_path.parent / "fora_do_workspace.txt"
        result = await file_read(
            file_path=str(outside), ctx=ctx_from_config(trusted_ws)
        )
        assert "fora do workspace" in result.lower() or "error" in result.lower()

    async def test_blocked_credencial_sensivel_mesmo_dentro_do_workspace(
        self, tmp_path, trusted_ws
    ):
        """Chave SSH versionada por engano dentro do workspace confiável
        continua bloqueada — segunda camada de defesa independente do
        sandbox nativo estar ativo."""
        from backend.tools.fs import file_read

        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        chave = ssh_dir / "id_rsa"
        chave.write_text("chave-privada-fake", encoding="utf-8")
        result = await file_read(file_path=str(chave), ctx=ctx_from_config(trusted_ws))
        assert "sensível" in result.lower() or "error" in result.lower()
        assert "chave-privada-fake" not in result


# ---------------------------------------------------------------------------
# Files Workbench parity
# ---------------------------------------------------------------------------


class TestFileWorkbenchParity:
    async def test_create_move_search_delete(self, tmp_path, trusted_ws):
        from backend.tools.fs import (
            file_create_dir,
            file_delete,
            file_move,
            file_search,
        )

        ctx = ctx_from_config(trusted_ws)
        assert "OK" in await file_create_dir("nested", ctx)
        (tmp_path / "nested" / "note.txt").write_text("github workbench")
        assert "github" in (await file_search("github", ctx, "nested")).lower()
        assert "OK" in await file_move("nested/note.txt", "renamed.txt", ctx)
        assert (tmp_path / "renamed.txt").exists()
        assert "OK" in await file_delete("renamed.txt", ctx, permanent=True)
        assert not (tmp_path / "renamed.txt").exists()


# ---------------------------------------------------------------------------
# file_write
# ---------------------------------------------------------------------------


class TestFileWrite:
    async def test_creates_new_file(self, tmp_path, trusted_ws):
        from backend.tools.fs import file_write

        dest = tmp_path / "novo.txt"
        result = await file_write(
            file_path=str(dest), content="ola mundo", ctx=ctx_from_config(trusted_ws)
        )
        assert "ok" in result.lower() or "written" in result.lower()
        assert dest.read_text(encoding="utf-8") == "ola mundo"

    async def test_overwrites_existing_file(self, tmp_path, trusted_ws):
        from backend.tools.fs import file_write

        dest = tmp_path / "existente.txt"
        dest.write_text("velho", encoding="utf-8")
        await file_write(
            file_path=str(dest), content="novo", ctx=ctx_from_config(trusted_ws)
        )
        assert dest.read_text(encoding="utf-8") == "novo"

    async def test_creates_parent_dirs(self, tmp_path, trusted_ws):
        from backend.tools.fs import file_write

        dest = tmp_path / "sub" / "dir" / "arquivo.txt"
        await file_write(
            file_path=str(dest), content="x", ctx=ctx_from_config(trusted_ws)
        )
        assert dest.exists()

    async def test_blocked_path(self, tmp_path, trusted_ws):
        from backend.tools.fs import file_write

        outside = tmp_path.parent / "evil.txt"
        result = await file_write(
            file_path=str(outside), content="x", ctx=ctx_from_config(trusted_ws)
        )
        assert "fora do workspace" in result.lower() or "error" in result.lower()

    async def test_requires_trust(self, tmp_path, monkeypatch):
        from backend.tools.fs import file_write
        from backend.workspace import workspace as ws_mod

        untrusted = Workspace(
            id="untrusted",
            name="untrusted",
            cwd=str(tmp_path),
            created_at="2024-01-01T00:00:00+00:00",
            trusted=False,
        )
        monkeypatch.setattr(
            ws_mod.workspace_registry,
            "get",
            lambda wid: untrusted if wid == "untrusted" else None,
        )
        monkeypatch.setattr(
            ws_mod.workspace_registry, "get_or_create", lambda cwd=None: untrusted
        )
        cfg = {"configurable": {"workspace_id": "untrusted"}}
        result = await file_write(
            file_path=str(tmp_path / "x.txt"),
            content="x",
            ctx=ctx_from_config(cfg),
        )
        assert "confi" in result.lower()  # "não é confiável" / "confiança"


# ---------------------------------------------------------------------------
# file_edit
# ---------------------------------------------------------------------------


class TestFileEdit:
    async def test_replaces_text(self, tmp_path, trusted_ws):
        from backend.tools.fs import file_edit

        f = tmp_path / "code.py"
        f.write_text("foo = 1\nbar = 2\n", encoding="utf-8")
        result = await file_edit(
            file_path=str(f),
            old_text="foo = 1",
            new_text="foo = 99",
            ctx=ctx_from_config(trusted_ws),
        )
        assert "ok" in result.lower()
        assert "foo = 99" in f.read_text(encoding="utf-8")

    async def test_replace_all(self, tmp_path, trusted_ws):
        from backend.tools.fs import file_edit

        f = tmp_path / "rep.txt"
        f.write_text("a a a", encoding="utf-8")
        await file_edit(
            file_path=str(f),
            old_text="a",
            new_text="b",
            replace_all=True,
            ctx=ctx_from_config(trusted_ws),
        )
        assert f.read_text(encoding="utf-8") == "b b b"

    async def test_creates_file_when_old_text_empty(self, tmp_path, trusted_ws):
        from backend.tools.fs import file_edit

        dest = tmp_path / "new.txt"
        result = await file_edit(
            file_path=str(dest),
            old_text="",
            new_text="criado",
            ctx=ctx_from_config(trusted_ws),
        )
        assert "ok" in result.lower() or "created" in result.lower()
        assert dest.read_text(encoding="utf-8") == "criado"

    async def test_text_not_found(self, tmp_path, trusted_ws):
        from backend.tools.fs import file_edit

        f = tmp_path / "f.txt"
        f.write_text("abc", encoding="utf-8")
        result = await file_edit(
            file_path=str(f),
            old_text="xyz",
            new_text="nope",
            ctx=ctx_from_config(trusted_ws),
        )
        assert "not found" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


class TestGrep:
    async def test_finds_pattern_in_file(self, tmp_path, trusted_ws):
        from backend.tools.fs import grep

        f = tmp_path / "backend.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")
        result = await grep(
            pattern="def foo", path=str(tmp_path), ctx=ctx_from_config(trusted_ws)
        )
        assert "def foo" in result

    async def test_no_match_returns_message(self, tmp_path, trusted_ws):
        from backend.tools.fs import grep

        f = tmp_path / "empty.py"
        f.write_text("nothing here", encoding="utf-8")
        result = await grep(
            pattern="xyz_not_present",
            path=str(tmp_path),
            ctx=ctx_from_config(trusted_ws),
        )
        assert "no matches" in result.lower()

    async def test_invalid_pattern(self, trusted_ws):
        from backend.tools.fs import grep

        with patch("backend.tools.fs.is_safe_regex_pattern", return_value=False):
            result = await grep(
                pattern="[invalid", path=".", ctx=ctx_from_config(trusted_ws)
            )
        assert "invalid" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------


class TestListDir:
    async def test_lists_files(self, tmp_path, trusted_ws):
        from backend.tools.fs import list_dir

        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "b.txt").write_text("y")
        result = await list_dir(path=str(tmp_path), ctx=ctx_from_config(trusted_ws))
        assert "a.txt" in result
        assert "b.txt" in result

    async def test_recursive(self, tmp_path, trusted_ws):
        from backend.tools.fs import list_dir

        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.txt").write_text("z")
        result = await list_dir(
            path=str(tmp_path), recursive=True, ctx=ctx_from_config(trusted_ws)
        )
        assert "deep.txt" in result

    async def test_nonexistent_dir(self, tmp_path, trusted_ws):
        from backend.tools.fs import list_dir

        result = await list_dir(
            path=str(tmp_path / "nope"), ctx=ctx_from_config(trusted_ws)
        )
        assert "not found" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# _active_workspace — resolução de fallback sem workspace_id
# ---------------------------------------------------------------------------


class TestActiveWorkspaceFallback:
    """Sem `ctx.workspace_id` (ou apontando pra um id que não existe mais no
    registry), a resolução precisa passar pelo workspace ativo do usuário
    antes de cair no último recurso — nunca deve escrever silenciosamente
    no diretório de trabalho do processo do backend."""

    async def test_sem_workspace_id_usa_o_workspace_ativo_do_usuario(
        self, tmp_path, monkeypatch
    ):
        from backend.tools.context import ToolContext
        from backend.tools.fs import _active_workspace
        from backend.workspace import workspace as ws_mod

        ativo = Workspace(
            id="ws-ativo",
            name="ws-ativo",
            cwd=str(tmp_path / "ativo"),
            created_at="2024-01-01T00:00:00+00:00",
            trusted=True,
        )
        monkeypatch.setattr(ws_mod.workspace_registry, "get", lambda _wid: None)
        monkeypatch.setattr(ws_mod.workspace_registry, "get_active", lambda _uid: ativo)

        def _boom(*_a, **_kw):
            raise AssertionError(
                "não deveria cair no diretório de trabalho do processo "
                "quando há um workspace ativo registrado"
            )

        monkeypatch.setattr(ws_mod.workspace_registry, "get_or_create", _boom)

        ctx = ToolContext(user_id="local", workspace_id="")
        resolved = _active_workspace(ctx)

        assert resolved.id == "ws-ativo"

    async def test_sem_workspace_ativo_cai_no_ultimo_recurso(
        self, tmp_path, monkeypatch
    ):
        """Erro/borda: nenhum workspace ativo registrado (primeiro uso,
        registry vazio) — só aí o último recurso (cwd do processo) é
        aceitável."""
        from backend.tools.context import ToolContext
        from backend.tools.fs import _active_workspace
        from backend.workspace import workspace as ws_mod

        fallback = Workspace(
            id="fallback",
            name="fallback",
            cwd=str(tmp_path),
            created_at="2024-01-01T00:00:00+00:00",
            trusted=True,
        )
        monkeypatch.setattr(ws_mod.workspace_registry, "get", lambda _wid: None)
        monkeypatch.setattr(ws_mod.workspace_registry, "get_active", lambda _uid: None)
        monkeypatch.setattr(
            ws_mod.workspace_registry, "get_or_create", lambda: fallback
        )

        ctx = ToolContext(user_id="local", workspace_id="")
        resolved = _active_workspace(ctx)

        assert resolved.id == "fallback"


# ---------------------------------------------------------------------------
# create_artifact
# ---------------------------------------------------------------------------


def _cfg(thread_id: str) -> dict:
    """Config mínimo — thread_id vem de configurable["thread_id"], nunca de
    um kwarg que dependeria do modelo "lembrar" de passar o ID certo."""
    return {"configurable": {"thread_id": thread_id}}


class TestCreateArtifact:
    async def test_creates_file_and_returns_json(self, tmp_path, monkeypatch):
        from backend.tools.fs import create_artifact

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = await create_artifact(
            artifact_type="plan",
            title="Plano de Implementacao Auth",
            content="# Auth\n\nPasso 1: definir schema\nPasso 2: implementar JWT",
            ctx=ctx_from_config(_cfg("042731")),
        )
        data = json.loads(result)
        assert "path" in data
        assert data["artifact_type"] == "plan"
        assert data["session_id"] == "042731"
        assert data["title"] == "Plano de Implementacao Auth"
        assert Path(data["path"]).exists()
        content = Path(data["path"]).read_text(encoding="utf-8")
        assert "Auth" in content

    async def test_saved_under_session_dir(self, tmp_path, monkeypatch):
        from backend.tools.fs import create_artifact

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = await create_artifact(
            artifact_type="spec",
            title="API de Pagamentos",
            content="Spec content",
            ctx=ctx_from_config(_cfg("000001")),
        )
        data = json.loads(result)
        artifact_path = Path(data["path"])
        # deve estar em .src/artifacts/000001/
        assert "000001" in str(artifact_path)
        assert artifact_path.suffix == ".md"

    async def test_same_title_rewrites_current_and_keeps_previous_as_history(
        self, tmp_path, monkeypatch
    ):
        """Versionamento: salvar de novo com o MESMO título mantém o path
        atual estável (a UI sempre acha a versão mais recente no mesmo
        lugar) — a versão anterior vira histórico imutável numerado, não é
        perdida nem gera um path novo pra cada save."""
        from backend.tools.fs import create_artifact

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cfg = _cfg("999999")
        r1 = json.loads(
            await create_artifact(
                artifact_type="guide",
                title="Guia de Setup do Ambiente de Deploy",
                content="versão 1",
                ctx=ctx_from_config(cfg),
            )
        )
        r2 = json.loads(
            await create_artifact(
                artifact_type="guide",
                title="Guia de Setup do Ambiente de Deploy",
                content="versão 2",
                ctx=ctx_from_config(cfg),
            )
        )
        assert r1["path"] == r2["path"]  # path da versão atual é estável
        current = Path(r2["path"])
        assert current.read_text(encoding="utf-8") == "versão 2"

        history = current.with_name(f"{current.stem}-1.md")
        assert history.exists()
        assert history.read_text(encoding="utf-8") == "versão 1"

    async def test_third_write_rotates_history_without_losing_the_first(
        self, tmp_path, monkeypatch
    ):
        from backend.tools.fs import create_artifact

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cfg = _cfg("777777")
        kwargs = {
            "artifact_type": "spec",
            "title": "Especificação da API de Pagamentos v2",
        }
        r1 = json.loads(
            await create_artifact(
                **{**kwargs, "content": "v1"}, ctx=ctx_from_config(cfg)
            )
        )
        json.loads(
            await create_artifact(
                **{**kwargs, "content": "v2"}, ctx=ctx_from_config(cfg)
            )
        )
        r3 = json.loads(
            await create_artifact(
                **{**kwargs, "content": "v3"}, ctx=ctx_from_config(cfg)
            )
        )

        current = Path(r3["path"])
        assert current.read_text(encoding="utf-8") == "v3"
        h1 = current.with_name(f"{current.stem}-1.md")
        h2 = current.with_name(f"{current.stem}-2.md")
        assert h1.read_text(encoding="utf-8") == "v1"
        assert h2.read_text(encoding="utf-8") == "v2"
        assert r1["path"] == str(current)  # 1ª chamada já grava no path final

    async def test_writes_artifact_type_sidecar_read_by_api(
        self, tmp_path, monkeypatch
    ):
        """A API (handlers/artifacts.py) lê `{slug}.artifact_type` pra
        colorir/iconizar a Plan tab por tipo — precisa existir ao lado do
        .md da versão atual."""
        from backend.tools.fs import create_artifact

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = await create_artifact(
            artifact_type="architecture",
            title="Decisão de Arquitetura do Cache Distribuído",
            content="conteudo",
            ctx=ctx_from_config(_cfg("side-1")),
        )
        data = json.loads(result)
        current = Path(data["path"])
        sidecar = current.with_suffix(".artifact_type")
        assert sidecar.exists()
        assert sidecar.read_text(encoding="utf-8") == "architecture"

    async def test_generic_title_rejected(self, tmp_path, monkeypatch):
        """Erro/borda: título só com o nome do tipo ('Plano') é rejeitado —
        viraria plan.md sem dizer do que trata (bug real observado ao vivo:
        artifact criado sem título descritivo)."""
        from backend.tools.fs import create_artifact

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = await create_artifact(
            artifact_type="plan",
            title="Plano",
            content="conteudo",
            ctx=ctx_from_config(_cfg("gen-1")),
        )
        data = json.loads(result)
        assert "error" in data
        assert not (tmp_path / ".vectora").exists()  # nada foi escrito

    async def test_descriptive_title_still_accepted(self, tmp_path, monkeypatch):
        from backend.tools.fs import create_artifact

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = await create_artifact(
            artifact_type="plan",
            title="Plano de Implementação do Jogo da Cobrinha em Godot 4.7",
            content="conteudo",
            ctx=ctx_from_config(_cfg("gen-2")),
        )
        data = json.loads(result)
        assert "error" not in data
        assert Path(data["path"]).exists()

    async def test_mirrors_current_version_to_active_workspace(
        self, tmp_path, monkeypatch
    ):
        """Com workspace_id no config, a versão atual é espelhada em
        <workspace>/.vectora/{tipo}s/{slug}.md — sem histórico, sempre a
        última versão, visível direto no projeto (não só em ~/.vectora)."""
        from backend.tools.fs import create_artifact
        from backend.workspace import workspace as ws_mod

        home_dir = tmp_path / "home"
        home_dir.mkdir()
        ws_dir = tmp_path / "proj"
        ws_dir.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home_dir)

        ws = Workspace(
            id="testws",
            name="testws",
            cwd=str(ws_dir),
            created_at="2024-01-01T00:00:00+00:00",
            trusted=True,
        )
        monkeypatch.setattr(
            ws_mod.workspace_registry,
            "get",
            lambda wid: ws if wid == "testws" else None,
        )
        cfg = {"configurable": {"thread_id": "t1", "workspace_id": "testws"}}
        result = await create_artifact(
            artifact_type="plan",
            title="Plano de Implementação do Jogo da Cobrinha em Godot 4.7",
            content="conteudo do plano",
            ctx=ctx_from_config(cfg),
        )
        data = json.loads(result)
        assert "error" not in data

        mirror = ws_dir / ".vectora" / "plans" / Path(data["path"]).name
        assert mirror.exists()
        assert mirror.read_text(encoding="utf-8") == "conteudo do plano"

    async def test_sem_workspace_id_nao_resolve_workspace_default(
        self, tmp_path, monkeypatch
    ):
        """Erro/borda: sem workspace_id explícito, nunca chama
        get_or_create (não força a criação/uso de um workspace default só
        pra tentar espelhar o artifact)."""
        from backend.tools.fs import create_artifact
        from backend.workspace import workspace as ws_mod

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        def _boom(*args, **kwargs):
            raise AssertionError("não deveria resolver workspace sem workspace_id")

        monkeypatch.setattr(ws_mod.workspace_registry, "get_or_create", _boom)
        result = await create_artifact(
            artifact_type="plan",
            title="Plano de Migração do Banco de Dados Legado",
            content="conteudo",
            ctx=ctx_from_config(_cfg("sem-ws")),
        )
        data = json.loads(result)
        assert "error" not in data

    async def test_invalid_type_returns_error(self, tmp_path, monkeypatch):
        from backend.tools.fs import create_artifact

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = await create_artifact(
            artifact_type="invalid_type",
            title="Titulo",
            content="Conteudo",
            ctx=ctx_from_config(_cfg("000000")),
        )
        data = json.loads(result)
        assert "error" in data

    async def test_empty_title_returns_error(self, tmp_path, monkeypatch):
        from backend.tools.fs import create_artifact

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = await create_artifact(
            artifact_type="plan",
            title="   ",
            content="Conteudo",
            ctx=ctx_from_config(_cfg("000000")),
        )
        data = json.loads(result)
        assert "error" in data

    async def test_empty_content_returns_error(self, tmp_path, monkeypatch):
        from backend.tools.fs import create_artifact

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = await create_artifact(
            artifact_type="plan",
            title="Titulo valido",
            content="",
            ctx=ctx_from_config(_cfg("000000")),
        )
        data = json.loads(result)
        assert "error" in data

    async def test_all_valid_types_accepted(self, tmp_path, monkeypatch):
        from backend.tools.fs import _VALID_ARTIFACT_TYPES, create_artifact

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        for artifact_type in _VALID_ARTIFACT_TYPES:
            result = await create_artifact(
                artifact_type=artifact_type,
                title=f"Teste {artifact_type}",
                content="Conteudo de teste",
                ctx=ctx_from_config(_cfg("000000")),
            )
            data = json.loads(result)
            assert "error" not in data, f"tipo '{artifact_type}' falhou: {data}"

    async def test_missing_thread_id_returns_error_instead_of_silent_default(
        self, tmp_path, monkeypatch
    ):
        """Regressão: session_id="000000" como default oculto fazia o artifact
        cair num diretório errado sempre que o config não trazia o thread_id
        real (ex.: modelo "esquecia" de passar o parâmetro, já que dependia
        dele lembrar de um valor visto só no texto do prompt) — o painel
        Plano da UI, que busca pelo threadId real, mostrava "Sem planos"
        mesmo com o Vectora confirmando que salvou. Agora falha alto (erro
        explícito) em vez de salvar silenciosamente no lugar errado."""
        from backend.tools.fs import create_artifact

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = await create_artifact(
            artifact_type="overview",
            title="Visao Geral",
            content="Conteudo",
            ctx=ctx_from_config({"configurable": {}}),
        )
        data = json.loads(result)
        assert "error" in data


# ---------------------------------------------------------------------------
# _artifact_slug (helper interno)
# ---------------------------------------------------------------------------


class TestArtifactSlug:
    def test_basic_slug(self):
        from backend.tools.fs import _artifact_slug

        slug = _artifact_slug("Plano de Implementacao")
        assert slug == "plano-de-implementacao"

    def test_long_title_not_truncated(self):
        """Sem limite de comprimento (ver tests/unit/test_artifact_slug.py —
        um limite de 50 chars foi um bug já corrigido de propósito)."""
        from backend.tools.fs import _artifact_slug

        long_title = "a" * 100
        assert len(_artifact_slug(long_title)) == 100

    def test_empty_falls_back(self):
        from backend.tools.fs import _artifact_slug

        assert _artifact_slug("") == "artifact"
        assert _artifact_slug("   !@#  ") == "artifact"

    def test_spaces_become_hyphens(self):
        from backend.tools.fs import _artifact_slug

        assert _artifact_slug("hello world") == "hello-world"

    def test_no_consecutive_hyphens(self):
        from backend.tools.fs import _artifact_slug

        slug = _artifact_slug("a  b   c")
        assert "--" not in slug


# ---------------------------------------------------------------------------
# Hooks pós-escrita + auto-commit (Trilha B) — vectora.toml [hooks]/[agent]
# ---------------------------------------------------------------------------


class TestPostWriteHooksAndAutoCommit:
    async def test_file_write_runs_post_write_hook(self, tmp_path, trusted_ws):
        """[hooks] post_file_write roda com {file} substituído pelo path real."""
        from backend.tools.fs import file_write

        marker = tmp_path / "hook-ran.txt"
        (tmp_path / "vectora.toml").write_text(
            "[hooks]\npost_file_write = [\"python -c \\\"open(r'{file}' + '.marker', 'w').close()\\\"\"]\n",
            encoding="utf-8",
        )

        dest = tmp_path / "novo.txt"
        await file_write(
            file_path=str(dest), content="ola", ctx=ctx_from_config(trusted_ws)
        )

        marker_file = Path(str(dest) + ".marker")
        assert marker_file.exists()

    async def test_file_write_without_config_does_not_run_hooks(
        self, tmp_path, trusted_ws
    ):
        """Sem vectora.toml (ou sem seções configuradas), nenhum hook roda — edge."""
        from backend.tools.fs import file_write

        dest = tmp_path / "sem-config.txt"
        result = await file_write(
            file_path=str(dest), content="ola", ctx=ctx_from_config(trusted_ws)
        )
        assert "ok" in result.lower() or "written" in result.lower()

    async def test_hook_not_approved_does_not_run_and_warns(
        self, tmp_path, monkeypatch
    ):
        """Workspace confiável mas sem aprovação de hooks — hook NÃO roda, e a
        resposta da tool avisa o motivo: efeito de shell arbitrário vindo de
        vectora.toml não herda a confiança de leitura/escrita."""
        from backend.tools.fs import file_write
        from backend.workspace import workspace as ws_mod

        ws = Workspace(
            id="testws-noapprove",
            name="testws-noapprove",
            cwd=str(tmp_path),
            created_at="2024-01-01T00:00:00+00:00",
            trusted=True,
            hooks_approved=False,
        )
        monkeypatch.setattr(
            ws_mod.workspace_registry,
            "get",
            lambda wid: ws if wid == "testws-noapprove" else None,
        )
        config = {"configurable": {"workspace_id": "testws-noapprove"}}

        (tmp_path / "vectora.toml").write_text(
            "[hooks]\npost_file_write = [\"python -c \\\"open(r'{file}' + '.marker', 'w').close()\\\"\"]\n",
            encoding="utf-8",
        )

        dest = tmp_path / "novo.txt"
        result = await file_write(
            file_path=str(dest), content="ola", ctx=ctx_from_config(config)
        )

        marker_file = Path(str(dest) + ".marker")
        assert not marker_file.exists()
        assert "aprovad" in result.lower()

    async def test_hook_approved_after_approve_hooks_runs(self, tmp_path, monkeypatch):
        """Depois de WorkspaceRegistry.approve_hooks(...), o hook passa a rodar."""
        from backend.tools.fs import file_write
        from backend.workspace.workspace import WorkspaceRegistry

        registry = WorkspaceRegistry()
        registry._loaded = True
        monkeypatch.setattr(registry, "_save", lambda: None)
        monkeypatch.setattr("backend.workspace.workspace.workspace_registry", registry)

        proj = tmp_path / "proj"
        proj.mkdir()
        ws = registry.create(str(proj), trust=True)
        registry.approve_hooks(ws.id)

        (proj / "vectora.toml").write_text(
            "[hooks]\npost_file_write = [\"python -c \\\"open(r'{file}' + '.marker', 'w').close()\\\"\"]\n",
            encoding="utf-8",
        )

        dest = proj / "novo.txt"
        await file_write(
            file_path=str(dest),
            content="ola",
            ctx=ctx_from_config({"configurable": {"workspace_id": ws.id}}),
        )

        marker_file = Path(str(dest) + ".marker")
        assert marker_file.exists()

    def test_build_hook_argv_never_reinterprets_file_as_shell(self, tmp_path):
        """Nome de arquivo com metacaracteres de shell vira argumento literal,
        não comando novo — a classe de injeção que motivou a troca de
        create_subprocess_shell por create_subprocess_exec."""
        from backend.tools.fs import _build_hook_argv

        malicious = tmp_path / "arquivo; rm -rf /tmp/x.txt"
        argv = _build_hook_argv("python -c \"print(r'{file}')\"", malicious)

        assert argv[0] == "python"
        assert argv[1] == "-c"
        assert str(malicious) in argv[2]
        # Nenhum token isolado é "rm" — o ";" nunca separa comandos porque
        # não há shell no meio, é só substring dentro de um argv item.
        assert "rm" not in argv

    async def test_file_edit_auto_commit_creates_git_commit(self, tmp_path, trusted_ws):
        """agent.auto_commit=true faz file_edit gerar um commit git automático."""
        import git

        from backend.tools.fs import file_edit

        repo = git.Repo.init(tmp_path)
        repo.config_writer().set_value("user", "name", "Test").release()
        repo.config_writer().set_value("user", "email", "t@t.com").release()

        target = tmp_path / "arquivo.py"
        target.write_text("x = 1\n", encoding="utf-8")
        repo.index.add(["arquivo.py"])
        repo.index.commit("initial")

        (tmp_path / "vectora.toml").write_text(
            "[agent]\nauto_commit = true\n", encoding="utf-8"
        )

        await file_edit(
            file_path=str(target),
            old_text="x = 1",
            new_text="x = 2",
            ctx=ctx_from_config(trusted_ws),
        )

        latest = next(repo.iter_commits())
        assert "arquivo.py" in str(latest.message)
        assert repo.is_dirty() is False

    async def test_file_edit_auto_commit_disabled_by_default_leaves_changes_uncommitted(
        self, tmp_path, trusted_ws
    ):
        """Edge — sem auto_commit=true (default), a edição NÃO vira commit."""
        import git

        from backend.tools.fs import file_edit

        repo = git.Repo.init(tmp_path)
        repo.config_writer().set_value("user", "name", "Test").release()
        repo.config_writer().set_value("user", "email", "t@t.com").release()

        target = tmp_path / "arquivo.py"
        target.write_text("x = 1\n", encoding="utf-8")
        repo.index.add(["arquivo.py"])
        repo.index.commit("initial")

        await file_edit(
            file_path=str(target),
            old_text="x = 1",
            new_text="x = 2",
            ctx=ctx_from_config(trusted_ws),
        )

        assert repo.is_dirty() is True
