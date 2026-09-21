"""Tests para o view_router de src/api/handlers/workspaces.py.

Cobre os endpoints do Workbench:
- GET /workspaces/{id}/tree            — lista dirs/files filtrando ruído.
- GET /workspaces/{id}/file            — texto truncado, detecção de binário.
- GET /workspaces/{id}/git/diff        — resumo (vazio em pastas não-git).
- GET /workspaces/{id}/git/diff/file   — hunks (vazio quando sem mudanças).

Os endpoints reusam ``services.security.resolve_within_workspace``, então
qualquer path fora do workspace devolve resposta vazia em vez de
listar/ler.
"""

from __future__ import annotations

import hashlib

import pytest

from backend.vtypes import Workspace

# ---------------------------------------------------------------------------
# Fixture — workspace confiável apontando para tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture
def trusted_ws(tmp_path, monkeypatch):
    """Registra um workspace confiável em tmp_path no registry.

    Devolve uma tupla ``(workspace_id, tmp_path)`` para os testes montarem
    arquivos dentro de tmp_path e chamarem os endpoints com o id certo.
    """
    from backend.workspace import workspace as ws_mod

    ws = Workspace(
        id="vws",
        name="vws",
        cwd=str(tmp_path),
        created_at="2024-01-01T00:00:00+00:00",
        trusted=True,
    )
    monkeypatch.setattr(
        ws_mod.workspace_registry,
        "get",
        lambda wid: ws if wid == "vws" else None,
    )
    return "vws", tmp_path


# ---------------------------------------------------------------------------
# /workspaces/{id}/tree
# ---------------------------------------------------------------------------


class TestWorkspaceTree:
    @pytest.mark.asyncio
    async def test_lists_root_when_path_empty(self, trusted_ws):
        from backend.api.handlers.workspaces import workspace_tree

        wsid, root = trusted_ws
        (root / "a.txt").write_text("a", encoding="utf-8")
        (root / "sub").mkdir()

        resp = await workspace_tree(workspace_id=wsid, path="")
        names = {e.name for e in resp.entries}
        assert "a.txt" in names
        assert "sub" in names

    @pytest.mark.asyncio
    async def test_lists_subdir(self, trusted_ws):
        from backend.api.handlers.workspaces import workspace_tree

        wsid, root = trusted_ws
        (root / "sub").mkdir()
        (root / "sub" / "inside.txt").write_text("x", encoding="utf-8")

        resp = await workspace_tree(workspace_id=wsid, path="sub")
        names = [e.name for e in resp.entries]
        assert "inside.txt" in names

    @pytest.mark.asyncio
    async def test_filters_noisy_dirs(self, trusted_ws):
        """`.git`, `node_modules`, `.venv`, `__pycache__` não aparecem."""
        from backend.api.handlers.workspaces import workspace_tree

        wsid, root = trusted_ws
        for noisy in (".git", "node_modules", ".venv", "__pycache__", ".next"):
            (root / noisy).mkdir()
        (root / "src").mkdir()

        resp = await workspace_tree(workspace_id=wsid, path="")
        names = {e.name for e in resp.entries}
        assert "src" in names
        for noisy in (".git", "node_modules", ".venv", "__pycache__", ".next"):
            assert noisy not in names

    @pytest.mark.asyncio
    async def test_marks_dirs_and_files(self, trusted_ws):
        from backend.api.handlers.workspaces import workspace_tree

        wsid, root = trusted_ws
        (root / "data").mkdir()
        (root / "x.md").write_text("x", encoding="utf-8")

        resp = await workspace_tree(workspace_id=wsid, path="")
        kinds = {e.name: e.kind for e in resp.entries}
        assert kinds["data"] == "dir"
        assert kinds["x.md"] == "file"

    @pytest.mark.asyncio
    async def test_returns_empty_for_traversal_attempt(self, trusted_ws):
        """`..` resolve fora do workspace → guard rail bloqueia."""
        from backend.api.handlers.workspaces import workspace_tree

        wsid, _ = trusted_ws
        resp = await workspace_tree(workspace_id=wsid, path="..")
        assert resp.entries == []

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_workspace(self, trusted_ws):
        from backend.api.handlers.workspaces import workspace_tree

        # Fixture só registra "vws" — mantém workspace_registry.get("nope")
        # isolado em None mesmo se outro teste registrar algo nesse id.
        assert trusted_ws[0] == "vws"
        resp = await workspace_tree(workspace_id="nope", path="")
        assert resp.entries == []


# ---------------------------------------------------------------------------
# /workspaces/{id}/file
# ---------------------------------------------------------------------------


class TestWorkspaceFile:
    @pytest.mark.asyncio
    async def test_reads_text_file(self, trusted_ws):
        from backend.api.handlers.workspaces import workspace_file

        wsid, root = trusted_ws
        # `write_bytes` evita a tradução de newline de `write_text` (que no
        # Windows trocaria "\n" por "\r\n"), mantendo o teste determinístico
        # — o handler agora lê em modo binário para que o sha256 reflita
        # exatamente os bytes em disco.
        raw = "olá mundo\n".encode()
        (root / "hello.txt").write_bytes(raw)

        resp = await workspace_file(workspace_id=wsid, path="hello.txt")
        assert resp.kind == "text"
        assert resp.content == "olá mundo\n"
        assert resp.truncated is False
        assert resp.sha256 == hashlib.sha256(raw).hexdigest()

    @pytest.mark.asyncio
    async def test_detects_binary_file(self, trusted_ws):
        """Byte nulo nos primeiros 8 kB → kind=binary, sem conteúdo."""
        from backend.api.handlers.workspaces import workspace_file

        wsid, root = trusted_ws
        (root / "data.bin").write_bytes(b"\x00\x01\x02\xff" * 100)

        resp = await workspace_file(workspace_id=wsid, path="data.bin")
        assert resp.kind == "binary"
        assert resp.content is None
        assert resp.size > 0

    @pytest.mark.asyncio
    async def test_truncates_large_text(self, trusted_ws):
        """Arquivos acima de 256 kB são truncados (campo `truncated=True`)."""
        from backend.api.handlers.workspaces import workspace_file

        wsid, root = trusted_ws
        # 300 kB de "a"
        (root / "big.txt").write_text("a" * (300 * 1024), encoding="utf-8")

        resp = await workspace_file(workspace_id=wsid, path="big.txt")
        assert resp.kind == "text"
        assert resp.truncated is True
        assert resp.content is not None
        assert len(resp.content) == 256 * 1024
        # Truncado → sem sha256 (edição inline fica desabilitada no frontend).
        assert resp.sha256 is None

    @pytest.mark.asyncio
    async def test_returns_empty_for_missing_file(self, trusted_ws):
        from backend.api.handlers.workspaces import workspace_file

        wsid, _ = trusted_ws
        resp = await workspace_file(workspace_id=wsid, path="nao-existe.txt")
        assert resp.content is None
        assert resp.size == 0

    @pytest.mark.asyncio
    async def test_blocks_traversal(self, trusted_ws):
        """Path `..` ou absoluto fora do workspace é bloqueado."""
        from backend.api.handlers.workspaces import workspace_file

        wsid, root = trusted_ws
        # Cria arquivo IRMÃO ao workspace (acima dele)
        outside = root.parent / "fora.txt"
        outside.write_text("segredo", encoding="utf-8")

        resp = await workspace_file(workspace_id=wsid, path="../fora.txt")
        # Resolver bloqueia (resolve_within_workspace devolve None) →
        # handler responde como "arquivo não encontrado".
        assert resp.content is None or "segredo" not in (resp.content or "")


# ---------------------------------------------------------------------------
# PUT /workspaces/{id}/fs/file — editor inline
# ---------------------------------------------------------------------------


class TestUpdateFsFile:
    @pytest.mark.asyncio
    async def test_writes_with_matching_sha256(self, trusted_ws):
        from backend.api.handlers.workspaces import UpdateFsFileRequest, update_fs_file

        wsid, root = trusted_ws
        raw = "olá\n".encode()
        (root / "hello.txt").write_bytes(raw)
        current_sha = hashlib.sha256(raw).hexdigest()

        resp = await update_fs_file(
            workspace_id=wsid,
            path="hello.txt",
            body=UpdateFsFileRequest(content="mundo\n", expected_sha256=current_sha),
        )
        assert resp.status == "ok"
        assert resp.sha256 == hashlib.sha256(b"mundo\n").hexdigest()
        assert (root / "hello.txt").read_text(encoding="utf-8") == "mundo\n"

    @pytest.mark.asyncio
    async def test_writes_without_expected_sha256(self, trusted_ws):
        """`expected_sha256=None` pula a checagem de conflito (sobrescreve sempre)."""
        from backend.api.handlers.workspaces import UpdateFsFileRequest, update_fs_file

        wsid, root = trusted_ws
        (root / "hello.txt").write_text("a\n", encoding="utf-8")

        resp = await update_fs_file(
            workspace_id=wsid,
            path="hello.txt",
            body=UpdateFsFileRequest(content="b\n", expected_sha256=None),
        )
        assert resp.status == "ok"
        assert (root / "hello.txt").read_text(encoding="utf-8") == "b\n"

    @pytest.mark.asyncio
    async def test_conflict_returns_412(self, trusted_ws):
        """sha256 divergente → 412, sem escrever no disco."""
        from fastapi import HTTPException

        from backend.api.handlers.workspaces import UpdateFsFileRequest, update_fs_file

        wsid, root = trusted_ws
        (root / "hello.txt").write_text("original\n", encoding="utf-8")

        with pytest.raises(HTTPException) as exc_info:
            await update_fs_file(
                workspace_id=wsid,
                path="hello.txt",
                body=UpdateFsFileRequest(content="novo\n", expected_sha256="0" * 64),
            )
        assert exc_info.value.status_code == 412
        assert (root / "hello.txt").read_text(encoding="utf-8") == "original\n"

    @pytest.mark.asyncio
    async def test_missing_file_returns_error(self, trusted_ws):
        from backend.api.handlers.workspaces import UpdateFsFileRequest, update_fs_file

        wsid, _ = trusted_ws
        resp = await update_fs_file(
            workspace_id=wsid,
            path="nao-existe.txt",
            body=UpdateFsFileRequest(content="x", expected_sha256=None),
        )
        assert resp.status == "error"

    @pytest.mark.asyncio
    async def test_blocks_traversal(self, trusted_ws):
        from backend.api.handlers.workspaces import UpdateFsFileRequest, update_fs_file

        wsid, root = trusted_ws
        outside = root.parent / "fora.txt"
        outside.write_text("segredo", encoding="utf-8")

        resp = await update_fs_file(
            workspace_id=wsid,
            path="../fora.txt",
            body=UpdateFsFileRequest(content="hackeado", expected_sha256=None),
        )
        assert resp.status == "error"
        assert outside.read_text(encoding="utf-8") == "segredo"

    @pytest.mark.asyncio
    async def test_rejects_oversized_content(self, trusted_ws):
        from backend.api.handlers.workspaces import UpdateFsFileRequest, update_fs_file

        wsid, root = trusted_ws
        (root / "hello.txt").write_text("a", encoding="utf-8")

        resp = await update_fs_file(
            workspace_id=wsid,
            path="hello.txt",
            body=UpdateFsFileRequest(
                content="a" * (3 * 1024 * 1024), expected_sha256=None
            ),
        )
        assert resp.status == "error"
        assert (root / "hello.txt").read_text(encoding="utf-8") == "a"


# ---------------------------------------------------------------------------
# /workspaces/{id}/git/diff
# ---------------------------------------------------------------------------


class TestWorkspaceGitDiff:
    @pytest.mark.asyncio
    async def test_returns_empty_for_non_git_workspace(self, trusted_ws):
        from fastapi import Response

        from backend.api.handlers.workspaces import workspace_git_diff

        wsid, _ = trusted_ws
        resp = await workspace_git_diff(workspace_id=wsid, response=Response())
        assert resp.is_git_repo is False
        assert resp.files == []
        assert resp.total_additions == 0
        assert resp.total_deletions == 0

    @pytest.mark.asyncio
    async def test_sets_diff_schema_header(self, trusted_ws):
        from fastapi import Response

        from backend.api.handlers.workspaces import workspace_git_diff

        wsid, _ = trusted_ws
        response = Response()
        await workspace_git_diff(workspace_id=wsid, response=response)
        assert response.headers["X-Vectora-Diff-Schema"] == "2"

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_workspace(self, trusted_ws):
        from fastapi import Response

        from backend.api.handlers.workspaces import workspace_git_diff

        # Fixture só registra "vws" — mantém workspace_registry.get("nope")
        # isolado em None mesmo se outro teste registrar algo nesse id.
        assert trusted_ws[0] == "vws"
        resp = await workspace_git_diff(workspace_id="nope", response=Response())
        assert resp.is_git_repo is False
        assert resp.files == []


class TestWorkspaceGitDiffFile:
    @pytest.mark.asyncio
    async def test_returns_empty_hunks_for_non_git_workspace(self, trusted_ws):
        from backend.api.handlers.workspaces import workspace_git_diff_file

        wsid, _ = trusted_ws
        resp = await workspace_git_diff_file(workspace_id=wsid, path="x.md")
        assert resp.hunks == []

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_workspace(self, trusted_ws):
        from backend.api.handlers.workspaces import workspace_git_diff_file

        # Fixture só registra "vws" — mantém workspace_registry.get("nope")
        # isolado em None mesmo se outro teste registrar algo nesse id.
        assert trusted_ws[0] == "vws"
        resp = await workspace_git_diff_file(workspace_id="nope", path="x.md")
        assert resp.hunks == []


# ---------------------------------------------------------------------------
# Parser interno _parse_unified_diff
# ---------------------------------------------------------------------------


class TestParseUnifiedDiff:
    def test_splits_hunks_by_at_at_header(self):
        from backend.api.handlers.workspaces import _parse_unified_diff

        diff = (
            "@@ -1,3 +1,3 @@\n"
            " linha 1\n"
            "-velha\n"
            "+nova\n"
            " linha 3\n"
            "@@ -10,2 +10,2 @@\n"
            "-outra velha\n"
            "+outra nova\n"
        )
        hunks = _parse_unified_diff(diff)
        assert len(hunks) == 2
        assert hunks[0].header == "@@ -1,3 +1,3 @@"
        assert "-velha" in hunks[0].lines
        assert "+nova" in hunks[0].lines
        assert hunks[1].header == "@@ -10,2 +10,2 @@"

    def test_returns_empty_for_empty_diff(self):
        from backend.api.handlers.workspaces import _parse_unified_diff

        assert _parse_unified_diff("") == []

    def test_ignores_lines_before_first_hunk(self):
        """Headers `diff --git` / `index` antes do primeiro `@@` são descartados."""
        from backend.api.handlers.workspaces import _parse_unified_diff

        diff = (
            "diff --git a/x b/x\n"
            "index abc..def 100644\n"
            "--- a/x\n"
            "+++ b/x\n"
            "@@ -1 +1 @@\n"
            "-velha\n"
            "+nova\n"
        )
        hunks = _parse_unified_diff(diff)
        assert len(hunks) == 1
        assert "-velha" in hunks[0].lines


# ---------------------------------------------------------------------------
# Git — status / branches / checkout / compare / merge / PR (endpoints novos)
# ---------------------------------------------------------------------------


def _init_repo(root):
    """Inicializa um repo git em ``root`` com 1 commit; devolve o Repo."""
    import git

    repo = git.Repo.init(root)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test")
        cw.set_value("user", "email", "test@example.com")
    (root / "a.txt").write_text("linha 1\n", encoding="utf-8")
    repo.index.add(["a.txt"])
    repo.index.commit("init")
    return repo


class TestGitStatusBranches:
    @pytest.mark.asyncio
    async def test_status_non_git(self, trusted_ws):
        from backend.api.handlers.workspaces import git_status

        wsid, _ = trusted_ws
        resp = await git_status(workspace_id=wsid)
        assert resp.is_git_repo is False

    @pytest.mark.asyncio
    async def test_status_git_repo(self, trusted_ws):
        from backend.api.handlers.workspaces import git_status

        wsid, root = trusted_ws
        _init_repo(root)
        resp = await git_status(workspace_id=wsid)
        assert resp.is_git_repo is True
        assert resp.branch
        assert resp.clean is True

    @pytest.mark.asyncio
    async def test_branches_lists_current(self, trusted_ws):
        from backend.api.handlers.workspaces import git_branches

        wsid, root = trusted_ws
        repo = _init_repo(root)
        repo.create_head("feature-x")
        resp = await git_branches(workspace_id=wsid)
        assert "feature-x" in resp.branches
        assert resp.current

    @pytest.mark.asyncio
    async def test_checkout_creates_and_switches(self, trusted_ws):
        from backend.api.handlers.workspaces import (
            GitCheckoutRequest,
            git_checkout,
            git_status,
        )

        wsid, root = trusted_ws
        _init_repo(root)
        resp = await git_checkout(
            workspace_id=wsid, body=GitCheckoutRequest(ref="nova", create=True)
        )
        assert resp.status == "ok"
        st = await git_status(workspace_id=wsid)
        assert st.branch == "nova"


class TestGitCompareMerge:
    @pytest.mark.asyncio
    async def test_compare_lists_changed_files(self, trusted_ws):
        from backend.api.handlers.workspaces import git_compare_refs

        wsid, root = trusted_ws
        repo = _init_repo(root)
        base = repo.active_branch.name
        repo.create_head("feat")
        repo.git.checkout("feat")
        (root / "b.txt").write_text("novo\n", encoding="utf-8")
        repo.index.add(["b.txt"])
        repo.index.commit("add b")
        resp = await git_compare_refs(workspace_id=wsid, base=base, head="feat")
        assert "b.txt" in {f.path for f in resp.files}
        assert resp.ahead >= 1

    @pytest.mark.asyncio
    async def test_merge_clean(self, trusted_ws):
        from backend.api.handlers.workspaces import GitMergeRequest, git_merge

        wsid, root = trusted_ws
        repo = _init_repo(root)
        main = repo.active_branch.name
        repo.create_head("feat")
        repo.git.checkout("feat")
        (root / "c.txt").write_text("c\n", encoding="utf-8")
        repo.index.add(["c.txt"])
        repo.index.commit("add c")
        repo.git.checkout(main)
        resp = await git_merge(workspace_id=wsid, body=GitMergeRequest(branch="feat"))
        assert resp.status == "ok"
        assert (root / "c.txt").exists()


class TestGitLogPagination:
    """Paginação de git_log (offset/has_more): `has_more` sinaliza quando
    há mais commits além da página atual, e `offset` avança sem repetir
    commits já vistos."""

    @pytest.mark.asyncio
    async def test_has_more_true_when_more_commits_than_page(self, trusted_ws):
        from backend.api.handlers.workspaces import git_log

        wsid, root = trusted_ws
        repo = _init_repo(root)
        for i in range(4):
            (root / f"f{i}.txt").write_text(f"{i}\n", encoding="utf-8")
            repo.index.add([f"f{i}.txt"])
            repo.index.commit(f"commit {i}")
        # 5 commits no total (init + 4). Página de 2 deixa mais pra buscar.
        resp = await git_log(workspace_id=wsid, n=2, offset=0)
        assert len(resp.commits) == 2
        assert resp.has_more is True

    @pytest.mark.asyncio
    async def test_has_more_false_on_last_page(self, trusted_ws):
        from backend.api.handlers.workspaces import git_log

        wsid, root = trusted_ws
        _init_repo(root)  # 1 commit só
        resp = await git_log(workspace_id=wsid, n=50, offset=0)
        assert len(resp.commits) == 1
        assert resp.has_more is False

    @pytest.mark.asyncio
    async def test_offset_skips_already_seen_commits(self, trusted_ws):
        from backend.api.handlers.workspaces import git_log

        wsid, root = trusted_ws
        repo = _init_repo(root)
        for i in range(3):
            (root / f"g{i}.txt").write_text(f"{i}\n", encoding="utf-8")
            repo.index.add([f"g{i}.txt"])
            repo.index.commit(f"commit {i}")
        # 4 commits no total. Página 1 (offset=0, n=2) pega os 2 mais recentes;
        # página 2 (offset=2, n=2) pega os 2 restantes, sem repetir.
        page1 = await git_log(workspace_id=wsid, n=2, offset=0)
        page2 = await git_log(workspace_id=wsid, n=2, offset=2)
        assert {c.sha for c in page1.commits}.isdisjoint({c.sha for c in page2.commits})
        assert page2.has_more is False


class TestPrEndpoints:
    @pytest.mark.asyncio
    async def test_pr_list_unavailable_when_gh_fails(self, trusted_ws, monkeypatch):
        from unittest.mock import AsyncMock

        import backend.tools.gh as gh_mod
        from backend.api.handlers import workspaces as ws_mod

        wsid, root = trusted_ws
        _init_repo(root)
        monkeypatch.setattr(
            gh_mod,
            "_gh_run",
            AsyncMock(return_value={"status": "error", "message": "gh not found"}),
        )
        resp = await ws_mod.pr_list(workspace_id=wsid)
        assert resp.available is False


class TestListRagBuckets:
    """GET /workspaces/{id}/rag/buckets — buckets do workspace, usado pelo
    seletor de publicação da Memory Library e pelo painel de buckets do
    Memory tab."""

    @pytest.fixture(autouse=True)
    def _isolated_runtime_settings(self, tmp_path, monkeypatch):
        from backend.workspace import runtime_settings as rs_mod

        isolated = rs_mod.RuntimeSettings(path=tmp_path / "checkpoints.db")
        monkeypatch.setattr(rs_mod, "runtime_settings", isolated)
        return isolated

    @pytest.mark.asyncio
    async def test_lista_buckets_do_workspace_com_flag_active(
        self, trusted_ws, _isolated_runtime_settings
    ):
        from backend.api.handlers.workspaces import list_rag_buckets
        from backend.services import rag_buckets

        wsid, _root = trusted_ws
        b1 = rag_buckets.create_bucket(
            _isolated_runtime_settings, workspace_id=wsid, name="Docs"
        )
        rag_buckets.create_bucket(
            _isolated_runtime_settings, workspace_id=wsid, name="Inativo"
        )
        rag_buckets.set_active(
            _isolated_runtime_settings,
            workspace_id=wsid,
            bucket_id=b1.id,
            active=True,
        )

        resp = await list_rag_buckets(workspace_id=wsid)

        assert {b.name: b.active for b in resp} == {"Docs": True, "Inativo": False}

    @pytest.mark.asyncio
    async def test_workspace_sem_buckets_retorna_lista_vazia(
        self, trusted_ws, _isolated_runtime_settings
    ):
        from backend.api.handlers.workspaces import list_rag_buckets

        wsid, _root = trusted_ws

        resp = await list_rag_buckets(workspace_id=wsid)

        assert resp == []

    @pytest.mark.asyncio
    async def test_nao_mistura_buckets_de_outro_workspace(
        self, trusted_ws, _isolated_runtime_settings
    ):
        from backend.api.handlers.workspaces import list_rag_buckets
        from backend.services import rag_buckets

        wsid, _root = trusted_ws
        rag_buckets.create_bucket(
            _isolated_runtime_settings,
            workspace_id="outro-ws",
            name="Não deveria aparecer",
        )

        resp = await list_rag_buckets(workspace_id=wsid)

        assert resp == []


class TestToggleAndDeleteRagBucket:
    """PATCH/DELETE /workspaces/{id}/rag/buckets/{bucket_id} — reaproveitam
    rag_buckets.set_active/delete_bucket, usados pelo toggle e pelo botão
    remover do painel de buckets."""

    @pytest.fixture(autouse=True)
    def _isolated_runtime_settings(self, tmp_path, monkeypatch):
        from backend.workspace import runtime_settings as rs_mod

        isolated = rs_mod.RuntimeSettings(path=tmp_path / "checkpoints.db")
        monkeypatch.setattr(rs_mod, "runtime_settings", isolated)
        return isolated

    @pytest.mark.asyncio
    async def test_toggle_desativa_bucket(self, trusted_ws, _isolated_runtime_settings):
        from backend.api.handlers.workspaces import (
            RagBucketToggleRequest,
            list_rag_buckets,
            toggle_rag_bucket,
        )
        from backend.services import rag_buckets

        wsid, _root = trusted_ws
        bucket = rag_buckets.create_bucket(
            _isolated_runtime_settings, workspace_id=wsid, name="Docs"
        )
        rag_buckets.set_active(
            _isolated_runtime_settings,
            workspace_id=wsid,
            bucket_id=bucket.id,
            active=True,
        )

        await toggle_rag_bucket(
            workspace_id=wsid,
            bucket_id=bucket.id,
            body=RagBucketToggleRequest(active=False),
        )

        listed = await list_rag_buckets(workspace_id=wsid)
        assert listed[0].active is False

    @pytest.mark.asyncio
    async def test_toggle_bucket_inexistente_nao_lanca(
        self, trusted_ws, _isolated_runtime_settings
    ):
        from backend.api.handlers.workspaces import (
            RagBucketToggleRequest,
            toggle_rag_bucket,
        )

        wsid, _root = trusted_ws

        resp = await toggle_rag_bucket(
            workspace_id=wsid,
            bucket_id="nao-existe",
            body=RagBucketToggleRequest(active=True),
        )

        assert resp.active is True

    @pytest.mark.asyncio
    async def test_delete_remove_do_catalogo(
        self, trusted_ws, _isolated_runtime_settings
    ):
        from backend.api.handlers.workspaces import delete_rag_bucket, list_rag_buckets
        from backend.services import rag_buckets

        wsid, _root = trusted_ws
        bucket = rag_buckets.create_bucket(
            _isolated_runtime_settings, workspace_id=wsid, name="Docs"
        )

        resp = await delete_rag_bucket(workspace_id=wsid, bucket_id=bucket.id)

        assert resp == {"ok": True}
        assert await list_rag_buckets(workspace_id=wsid) == []

    @pytest.mark.asyncio
    async def test_delete_retorna_a_tentar_purge_falho_no_request_seguinte(
        self, trusted_ws, _isolated_runtime_settings, monkeypatch
    ):
        from unittest.mock import AsyncMock

        from backend.api.handlers.workspaces import delete_rag_bucket
        from backend.services import rag_buckets

        wsid, _root = trusted_ws
        bucket = rag_buckets.create_bucket(
            _isolated_runtime_settings, workspace_id=wsid, name="Docs"
        )
        fake_backend = AsyncMock()
        fake_backend.purge.side_effect = [
            RuntimeError("temporariamente indisponível"),
            None,
        ]
        monkeypatch.setattr(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(return_value=fake_backend),
        )

        await delete_rag_bucket(workspace_id=wsid, bucket_id=bucket.id)
        assert (
            rag_buckets.get_pending_purge_workspace(
                _isolated_runtime_settings, bucket.id
            )
            == wsid
        )

        # A different workspace cannot consume the pending cleanup record.
        await delete_rag_bucket(workspace_id="outro-workspace", bucket_id=bucket.id)
        fake_backend.purge.assert_awaited_once_with(f"bucket_{bucket.id}")

        await delete_rag_bucket(workspace_id=wsid, bucket_id=bucket.id)
        assert fake_backend.purge.await_count == 2
        assert (
            rag_buckets.get_pending_purge_workspace(
                _isolated_runtime_settings, bucket.id
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_delete_idempotente_bucket_ja_ausente(
        self, trusted_ws, _isolated_runtime_settings
    ):
        from backend.api.handlers.workspaces import delete_rag_bucket

        wsid, _root = trusted_ws

        resp = await delete_rag_bucket(workspace_id=wsid, bucket_id="nao-existe")

        assert resp == {"ok": True}

    @pytest.mark.asyncio
    async def test_delete_purga_a_tabela_vetorial_orfa(
        self, trusted_ws, _isolated_runtime_settings, monkeypatch
    ):
        """Regressão: apagar o bucket só removia o registro do catálogo — a
        tabela LanceDB (`bucket_{id}`) ficava órfã com todos os itens,
        continuando a aparecer em GET /rag/workspace-summary (aba Memória →
        Coleções) mesmo com "Buckets indexados" já mostrando vazio."""
        from unittest.mock import AsyncMock

        from backend.api.handlers.workspaces import delete_rag_bucket
        from backend.services import rag_buckets

        wsid, _root = trusted_ws
        bucket = rag_buckets.create_bucket(
            _isolated_runtime_settings, workspace_id=wsid, name="Docs"
        )

        fake_backend = AsyncMock()
        monkeypatch.setattr(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(return_value=fake_backend),
        )

        await delete_rag_bucket(workspace_id=wsid, bucket_id=bucket.id)

        fake_backend.purge.assert_awaited_once_with(f"bucket_{bucket.id}")

    @pytest.mark.asyncio
    async def test_delete_falha_no_purge_nao_impede_remocao_do_catalogo(
        self, trusted_ws, _isolated_runtime_settings, monkeypatch
    ):
        """Erro/borda: storage indisponível (ou tabela nunca criada — bucket
        criado mas nunca ingerido) durante o purge não pode deixar o bucket
        preso no catálogo — a remoção do registro precisa ser garantida."""
        from unittest.mock import AsyncMock

        from backend.api.handlers.workspaces import (
            delete_rag_bucket,
            list_rag_buckets,
        )
        from backend.services import rag_buckets

        wsid, _root = trusted_ws
        bucket = rag_buckets.create_bucket(
            _isolated_runtime_settings, workspace_id=wsid, name="Docs"
        )

        monkeypatch.setattr(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(side_effect=RuntimeError("storage indisponível")),
        )

        resp = await delete_rag_bucket(workspace_id=wsid, bucket_id=bucket.id)

        assert resp == {"ok": True}
        assert await list_rag_buckets(workspace_id=wsid) == []


class TestSandboxStatus:
    """GET /workspaces/{id}/sandbox/status — reflete se o worker jailado
    (Sandbox) está habilitado, lendo vectora.toml/[sandbox], e populado com
    um `diagnostic` acionável quando desabilitado."""

    @pytest.mark.asyncio
    async def test_enabled_true_when_sandbox_configured(self, trusted_ws):
        from backend.api.handlers.workspaces import workspace_sandbox_status

        wsid, root = trusted_ws
        (root / "vectora.toml").write_text(
            "[sandbox]\nenabled = true\n", encoding="utf-8"
        )

        resp = await workspace_sandbox_status(workspace_id=wsid)

        assert resp.enabled is True
        assert resp.diagnostic is None

    @pytest.mark.asyncio
    async def test_enabled_false_without_vectora_toml_e_wsl2_disponivel(
        self, trusted_ws, monkeypatch
    ):
        from backend.api.handlers import workspaces as ws_mod

        wsid, _root = trusted_ws
        monkeypatch.setattr(ws_mod, "detect_wsl2", None, raising=False)
        import backend.sandbox.policy as policy_mod

        monkeypatch.setattr(policy_mod, "detect_wsl2", _async_return("Ubuntu"))

        resp = await ws_mod.workspace_sandbox_status(workspace_id=wsid)

        assert resp.enabled is False
        assert resp.diagnostic == "no_vectora_toml"

    @pytest.mark.asyncio
    async def test_enabled_false_without_vectora_toml_e_sem_wsl2(
        self, trusted_ws, monkeypatch
    ):
        import backend.sandbox.policy as policy_mod
        from backend.api.handlers import workspaces as ws_mod

        wsid, _root = trusted_ws
        monkeypatch.setattr(policy_mod, "detect_wsl2", _async_return(None))
        monkeypatch.setattr(
            policy_mod, "wsl2_diagnostic", _async_return("wsl_not_installed")
        )

        resp = await ws_mod.workspace_sandbox_status(workspace_id=wsid)

        assert resp.enabled is False
        assert resp.diagnostic == "wsl_not_installed"

    @pytest.mark.asyncio
    async def test_enabled_false_when_sandbox_disabled_explicitly(self, trusted_ws):
        from backend.api.handlers.workspaces import workspace_sandbox_status

        wsid, root = trusted_ws
        (root / "vectora.toml").write_text(
            "[sandbox]\nenabled = false\n", encoding="utf-8"
        )

        resp = await workspace_sandbox_status(workspace_id=wsid)

        assert resp.enabled is False
        assert resp.diagnostic == "sandbox_disabled_in_config"

    @pytest.mark.asyncio
    async def test_unknown_workspace_returns_disabled_not_error(self):
        from backend.api.handlers.workspaces import workspace_sandbox_status

        resp = await workspace_sandbox_status(workspace_id="does-not-exist")

        assert resp.enabled is False
        assert resp.diagnostic == "no_workspace"


def _async_return(value):
    async def _fn(*_args, **_kwargs):
        return value

    return _fn


class TestSandboxInit:
    """POST /workspaces/{id}/sandbox/init — cria vectora.toml com [sandbox]
    habilitado, nunca sobrescrevendo um arquivo já existente."""

    @pytest.mark.asyncio
    async def test_cria_vectora_toml_com_sandbox_habilitado(self, trusted_ws):
        from backend.api.handlers.workspaces import workspace_sandbox_init

        wsid, root = trusted_ws

        resp = await workspace_sandbox_init(workspace_id=wsid)

        assert resp.ok is True
        toml_path = root / "vectora.toml"
        assert toml_path.is_file()
        assert "[sandbox]" in toml_path.read_text(encoding="utf-8")
        assert "enabled = true" in toml_path.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_nao_sobrescreve_vectora_toml_ja_existente(self, trusted_ws):
        from fastapi import HTTPException

        from backend.api.handlers.workspaces import workspace_sandbox_init

        wsid, root = trusted_ws
        (root / "vectora.toml").write_text('[other]\nkey = "value"\n', encoding="utf-8")

        with pytest.raises(HTTPException) as exc_info:
            await workspace_sandbox_init(workspace_id=wsid)

        assert exc_info.value.status_code == 409
        # Conteúdo original preservado — não some por causa da tentativa.
        assert "[other]" in (root / "vectora.toml").read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_workspace_inexistente_retorna_404(self):
        from fastapi import HTTPException

        from backend.api.handlers.workspaces import workspace_sandbox_init

        with pytest.raises(HTTPException) as exc_info:
            await workspace_sandbox_init(workspace_id="does-not-exist")

        assert exc_info.value.status_code == 404


def _fake_request(user_id: str = "u1"):
    from unittest.mock import MagicMock

    req = MagicMock()
    req.state.user = MagicMock()
    req.state.user.id = user_id
    return req


class TestMemorySearchUnified:
    """GET /workspaces/{id}/memory/search — busca combinada em fatos +
    skills + buckets RAG, consumida pela busca da Memory Tab."""

    @pytest.mark.asyncio
    async def test_combina_os_tres_tipos(self, trusted_ws, monkeypatch):
        from backend.api.handlers.workspaces import search_memory_unified
        from backend.services.memory_index import MemoryIndexHit

        wsid, _root = trusted_ws

        async def _fake_search(query, *, user_id, workspace_id, types, limit):
            return [
                MemoryIndexHit(
                    type="fact", id="f1", title="f1", snippet="conteúdo do fato"
                ),
                MemoryIndexHit(
                    type="skill", id="s1", title="Skill", snippet="descrição"
                ),
                MemoryIndexHit(
                    type="rag_bucket", id="b1", title="Bucket", snippet="docs"
                ),
            ]

        monkeypatch.setattr(
            "backend.services.memory_index.search_unified_memory", _fake_search
        )

        resp = await search_memory_unified(
            workspace_id=wsid, request=_fake_request(), q="qualquer"
        )

        assert {h.type for h in resp.hits} == {"fact", "skill", "rag_bucket"}

    @pytest.mark.asyncio
    async def test_workspace_inexistente_retorna_404(self):
        from fastapi import HTTPException

        from backend.api.handlers.workspaces import search_memory_unified

        with pytest.raises(HTTPException) as exc_info:
            await search_memory_unified(
                workspace_id="does-not-exist", request=_fake_request(), q="x"
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_types_invalido_e_normalizado_sem_quebrar(
        self, trusted_ws, monkeypatch
    ):
        """`types=invalid` não deve derrubar a query — vira None (busca todos)."""
        from backend.api.handlers.workspaces import search_memory_unified

        wsid, _root = trusted_ws
        captured: dict = {}

        async def _fake_search(query, *, user_id, workspace_id, types, limit):
            captured["types"] = types
            return []

        monkeypatch.setattr(
            "backend.services.memory_index.search_unified_memory", _fake_search
        )

        resp = await search_memory_unified(
            workspace_id=wsid,
            request=_fake_request(),
            q="x",
            types="invalid,also-bad",
        )

        assert resp.hits == []
        assert captured["types"] is None

    @pytest.mark.asyncio
    async def test_types_valido_e_repassado_como_frozenset(
        self, trusted_ws, monkeypatch
    ):
        from backend.api.handlers.workspaces import search_memory_unified

        wsid, _root = trusted_ws
        captured: dict = {}

        async def _fake_search(query, *, user_id, workspace_id, types, limit):
            captured["types"] = types
            return []

        monkeypatch.setattr(
            "backend.services.memory_index.search_unified_memory", _fake_search
        )

        await search_memory_unified(
            workspace_id=wsid, request=_fake_request(), q="x", types="fact,invalid"
        )

        assert captured["types"] == frozenset({"fact"})

    @pytest.mark.asyncio
    async def test_workspace_de_outro_dono_retorna_403(self, monkeypatch):
        """Regressão: o endpoint precisa checar ownership via
        `require_workspace_access` (mesmo padrão de `rag.py`/`context_graph.py`)
        — sem isso, qualquer usuário autenticado que soubesse o `workspace_id`
        de outro conseguia buscar seus fatos/skills/buckets."""
        from fastapi import HTTPException

        from backend.api.handlers.workspaces import search_memory_unified
        from backend.vtypes import Workspace
        from backend.workspace import workspace as ws_mod

        owned_by_other = Workspace(
            id="alheio",
            name="alheio",
            cwd="/tmp/alheio",
            created_at="2024-01-01T00:00:00+00:00",
            trusted=True,
            owner_id="dono-verdadeiro",
        )
        monkeypatch.setattr(
            ws_mod.workspace_registry,
            "get",
            lambda wid: owned_by_other if wid == "alheio" else None,
        )

        request = _fake_request(user_id="intruso")
        request.state.user.role = "member"

        with pytest.raises(HTTPException) as exc_info:
            await search_memory_unified(workspace_id="alheio", request=request, q="x")

        assert exc_info.value.status_code == 403


class TestRagIngestPreview:
    @pytest.mark.asyncio
    async def test_lists_files_matching_current_filters(self, tmp_path):
        from backend.api.handlers.workspaces import rag_ingest_preview

        (tmp_path / "a.py").write_text("1", encoding="utf-8")
        (tmp_path / "b.md").write_text("1", encoding="utf-8")

        result = await rag_ingest_preview(
            workspace_id="vws", path=str(tmp_path), file_types="markdown"
        )

        assert result.total == 1
        assert result.files == ["b.md"]

    @pytest.mark.asyncio
    async def test_unsafe_path_raises_400(self, monkeypatch, tmp_path):
        from fastapi import HTTPException

        from backend.api.handlers import workspaces as ws_handlers_mod
        from backend.services import security as security_mod

        monkeypatch.setattr(security_mod, "is_safe_file_path", lambda _p: False)

        with pytest.raises(HTTPException) as exc_info:
            await ws_handlers_mod.rag_ingest_preview(
                workspace_id="vws", path=str(tmp_path)
            )

        assert exc_info.value.status_code == 400
