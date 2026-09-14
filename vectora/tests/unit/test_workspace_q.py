"""Testes de workspace trust, git init, worktree e handlers do WorkspaceService.

O registry é isolado do disco via instância fresca com _save no-op; as operações
git rodam em repositórios temporários e worktrees redirecionadas para tmp_path.
"""

from __future__ import annotations

from types import SimpleNamespace

import git
import pytest

from backend.workspace.workspace import WorkspaceRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def reg(monkeypatch):
    """Registry isolado: não toca ~/.vectora/workspaces.json."""
    r = WorkspaceRegistry()
    r._loaded = True
    monkeypatch.setattr(r, "_save", lambda: None)
    monkeypatch.setattr("backend.workspace.workspace.workspace_registry", r)
    return r


def _req(user=None):
    """Request fake com .state.user, suficiente para os handlers."""
    return SimpleNamespace(state=SimpleNamespace(user=user))


# ---------------------------------------------------------------------------
# trust no registry
# ---------------------------------------------------------------------------


class TestTrust:
    def test_added_folder_starts_untrusted(self, reg, tmp_path):
        # Pasta diferente do cwd do processo → não confiada por padrão
        sub = tmp_path / "sub"
        sub.mkdir()
        ws = reg.get_or_create(str(sub))
        assert ws.trusted is False

    def test_trust_marks_trusted(self, reg, tmp_path):
        ws = reg.get_or_create(str(tmp_path))
        assert reg.trust(ws.id, "user1") is True
        assert reg.get(ws.id).trusted is True
        assert reg.get(ws.id).trusted_by == "user1"
        assert reg.get(ws.id).trusted_at is not None

    def test_trust_unknown_returns_false(self, reg):
        assert reg.trust("naoexiste", "user1") is False

    def test_is_trusted(self, reg, tmp_path):
        ws = reg.get_or_create(str(tmp_path))
        assert reg.is_trusted(ws.id) is False
        reg.trust(ws.id)
        assert reg.is_trusted(ws.id) is True

    def test_create_with_trust(self, reg, tmp_path):
        ws = reg.create(str(tmp_path), trust=True, user_id="u")
        assert ws.trusted is True


class TestSessionWorkspace:
    def test_registers_workspace_without_creating_folder(
        self, reg, tmp_path, monkeypatch
    ):
        """Pasta não é criada no registro; apenas na primeira operação real."""
        monkeypatch.setattr(
            "backend.workspace.workspace._session_workspaces_root",
            lambda: tmp_path / "docs",
        )
        ws = reg.get_or_create_session_workspace("thread123", "u")
        # O workspace fica registrado com o caminho correto…
        assert ws.cwd.endswith("thread123")
        # …mas a pasta em disco NÃO é criada imediatamente.
        assert not (tmp_path / "docs" / "thread123").exists()

    def test_session_workspace_is_trusted(self, reg, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.workspace.workspace._session_workspaces_root",
            lambda: tmp_path / "docs",
        )
        ws = reg.get_or_create_session_workspace("thread123", "u")
        assert ws.trusted is True

    def test_session_workspace_is_idempotent(self, reg, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.workspace.workspace._session_workspaces_root",
            lambda: tmp_path / "docs",
        )
        a = reg.get_or_create_session_workspace("thread123")
        b = reg.get_or_create_session_workspace("thread123")
        assert a.id == b.id


# ---------------------------------------------------------------------------
# git_init_repo
# ---------------------------------------------------------------------------


class TestGitInit:
    def test_init_empty_dir(self, tmp_path):
        from backend.tools.git import git_init_repo

        result = git_init_repo(str(tmp_path))
        assert result["status"] == "ok"
        assert (tmp_path / ".git").exists()

    def test_init_is_idempotent(self, tmp_path):
        from backend.tools.git import git_init_repo

        git_init_repo(str(tmp_path))
        again = git_init_repo(str(tmp_path))
        assert again["status"] == "already"

    def test_detect_reflects_init(self, tmp_path):
        from backend.tools.git import detect_git_info, git_init_repo

        assert detect_git_info(str(tmp_path)).get("is_git_repo") is False
        git_init_repo(str(tmp_path))
        assert detect_git_info(str(tmp_path)).get("is_git_repo") is True


# ---------------------------------------------------------------------------
# git_worktree
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_with_commit(tmp_path, monkeypatch):
    """Repo git temporário com um commit + worktrees redirecionadas a tmp_path."""
    repo = git.Repo.init(tmp_path)
    cw = repo.config_writer()
    cw.set_value("user", "name", "Test")
    cw.set_value("user", "email", "test@example.com")
    cw.release()
    (tmp_path / "file.txt").write_text("hello", encoding="utf-8")
    repo.index.add(["file.txt"])
    repo.index.commit("init")

    monkeypatch.setattr(
        "backend.tools.git._worktrees_root",
        lambda wid: tmp_path / "_wt" / wid,
    )
    return repo


class TestWorktree:
    def test_add_creates_worktree(self, repo_with_commit):
        from backend.tools.git import _git_worktree_impl

        result = _git_worktree_impl(
            repo_with_commit, "ws1", action="add", name="feat-x", branch="feat-x"
        )
        assert result["status"] == "ok"
        assert result["name"] == "feat-x"

    def test_list_includes_added_worktree(self, repo_with_commit):
        from backend.tools.git import _git_worktree_impl

        _git_worktree_impl(
            repo_with_commit, "ws1", action="add", name="feat-y", branch="feat-y"
        )
        listed = _git_worktree_impl(repo_with_commit, "ws1", action="list")
        assert listed["status"] == "ok"
        paths = " ".join(w.get("path", "") for w in listed["worktrees"])
        assert "feat-y" in paths

    def test_add_without_name_errors(self, repo_with_commit):
        from backend.tools.git import _git_worktree_impl

        result = _git_worktree_impl(repo_with_commit, "ws1", action="add")
        assert result["status"] == "error"

    def test_unknown_action_errors(self, repo_with_commit):
        from backend.tools.git import _git_worktree_impl

        result = _git_worktree_impl(repo_with_commit, "ws1", action="bogus")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# handlers do WorkspaceService
# ---------------------------------------------------------------------------


class TestWorkspaceHandlers:
    @pytest.mark.asyncio
    async def test_create_then_list(self, reg, tmp_path):
        from backend.api.handlers.workspaces import (
            CreateWorkspaceRequest,
            create_workspace,
            list_workspaces,
        )

        body = CreateWorkspaceRequest(path=str(tmp_path), trust=True)
        created = await create_workspace(_req(), body)
        assert created.status == "ok"
        assert created.workspace is not None
        assert created.workspace.trusted is True

        listing = await list_workspaces(_req())
        ids = {w.id for w in listing.workspaces}
        assert created.workspace.id in ids
        assert listing.active_id == created.workspace.id

    @pytest.mark.asyncio
    async def test_create_nonexistent_path(self, reg, tmp_path):
        from backend.api.handlers.workspaces import (
            CreateWorkspaceRequest,
            create_workspace,
        )

        body = CreateWorkspaceRequest(path=str(tmp_path / "nao_existe"))
        result = await create_workspace(_req(), body)
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_trust_handler(self, reg, tmp_path):
        from backend.api.handlers.workspaces import (
            TrustRequest,
            trust_workspace,
        )

        ws = reg.get_or_create(str(tmp_path))
        result = await trust_workspace(_req(), TrustRequest(workspace_id=ws.id))
        assert result.status == "ok"
        assert result.workspace is not None
        assert result.workspace.trusted is True

    @pytest.mark.asyncio
    async def test_set_active_unknown(self, reg):
        from backend.api.handlers.workspaces import (
            SetActiveRequest,
            set_active_workspace,
        )

        result = await set_active_workspace(
            _req(), SetActiveRequest(workspace_id="naoexiste")
        )
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_browse_lists_subdirs(self, tmp_path):
        from types import SimpleNamespace

        from backend.api.handlers.workspaces import browse_dir

        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()
        (tmp_path / "file.txt").write_text("x", encoding="utf-8")

        # `request.state.user = None` → modo CLI privilegiado, sem cap
        # de safe-root (necessário pra que o tmp_path arbitrário passe).
        fake_request = SimpleNamespace(state=SimpleNamespace(user=None))
        result = await browse_dir(fake_request, path=str(tmp_path))  # ty: ignore[invalid-argument-type]
        names = {e.name for e in result.entries}
        assert "alpha" in names
        assert "beta" in names
        # Arquivos não entram — só diretórios
        assert "file.txt" not in names

    @pytest.mark.asyncio
    async def test_mkdir_creates_subdir_then_relists(self, tmp_path):
        from backend.api.handlers.workspaces import MkdirRequest, mkdir_dir

        fake_request = SimpleNamespace(state=SimpleNamespace(user=None))

        result = await mkdir_dir(
            fake_request,  # ty: ignore[invalid-argument-type]
            MkdirRequest(path=str(tmp_path), name="minha-pasta"),
        )
        assert (tmp_path / "minha-pasta").is_dir()
        assert "minha-pasta" in {e.name for e in result.entries}
        assert result.created_path == str(tmp_path / "minha-pasta")

    @pytest.mark.asyncio
    async def test_mkdir_rejeita_traversal(self, tmp_path):
        from fastapi import HTTPException

        from backend.api.handlers.workspaces import MkdirRequest, mkdir_dir

        fake_request = SimpleNamespace(state=SimpleNamespace(user=None))
        with pytest.raises(HTTPException) as exc_traversal:
            await mkdir_dir(
                fake_request,  # ty: ignore[invalid-argument-type]
                MkdirRequest(path=str(tmp_path), name="../fora"),
            )
        assert exc_traversal.value.status_code == 400

    @pytest.mark.asyncio
    async def test_mkdir_rejeita_conflito(self, tmp_path):
        from fastapi import HTTPException

        from backend.api.handlers.workspaces import MkdirRequest, mkdir_dir

        fake_request = SimpleNamespace(state=SimpleNamespace(user=None))
        (tmp_path / "minha-pasta").mkdir()
        with pytest.raises(HTTPException) as exc_conflict:
            await mkdir_dir(
                fake_request,  # ty: ignore[invalid-argument-type]
                MkdirRequest(path=str(tmp_path), name="minha-pasta"),
            )
        assert exc_conflict.value.status_code == 409

    @pytest.mark.asyncio
    async def test_mkdir_common_user_outside_safe_root_forbidden(self, tmp_path):
        from fastapi import HTTPException

        from backend.api.handlers.workspaces import MkdirRequest, mkdir_dir

        # user não-None e sem role privilegiado → usuário comum, capado
        # pelos safe-roots configurados; tmp_path arbitrário fica fora.
        fake_request = SimpleNamespace(
            state=SimpleNamespace(user=SimpleNamespace(id="u1", role="member"))
        )
        with pytest.raises(HTTPException) as exc:
            await mkdir_dir(
                fake_request,  # ty: ignore[invalid-argument-type]
                MkdirRequest(path=str(tmp_path), name="nova"),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_mkdir_rejects_empty_name(self, tmp_path):
        from fastapi import HTTPException

        from backend.api.handlers.workspaces import MkdirRequest, mkdir_dir

        with pytest.raises(HTTPException) as exc:
            await mkdir_dir(
                _req(),
                MkdirRequest(path=str(tmp_path), name=""),
            )
        assert exc.value.status_code == 400
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.asyncio
    async def test_mkdir_rejects_empty_path(self, tmp_path):
        from fastapi import HTTPException

        from backend.api.handlers.workspaces import MkdirRequest, mkdir_dir

        with pytest.raises(HTTPException) as exc:
            await mkdir_dir(_req(), MkdirRequest(path="", name="nova"))
        assert exc.value.status_code == 400
        assert not (tmp_path / "nova").exists()


# ---------------------------------------------------------------------------
# owner_id + require_workspace_access (isolamento multi-usuário)
# ---------------------------------------------------------------------------


class TestWorkspaceOwnership:
    def test_create_claims_ownership_for_first_user(self, reg, tmp_path):
        ws = reg.create(str(tmp_path), user_id="alice")
        assert ws.owner_id == "alice"

    def test_create_does_not_reassign_existing_owner(self, reg, tmp_path):
        reg.create(str(tmp_path), user_id="alice")
        ws = reg.create(str(tmp_path), user_id="bob")
        assert ws.owner_id == "alice"

    def test_legacy_workspace_without_owner_stays_open(self, reg, tmp_path):
        # get_or_create() puro (sem create()) nunca seta owner_id — simula
        # um workspace legado sem dono.
        ws = reg.get_or_create(str(tmp_path))
        assert ws.owner_id is None

    def test_can_access_workspace_owner_allowed(self, reg, tmp_path):
        from backend.api.handlers.workspaces import can_access_workspace

        ws = reg.create(str(tmp_path), user_id="alice")
        req = _req(SimpleNamespace(id="alice", role="member"))
        assert can_access_workspace(ws, req) is True

    def test_can_access_workspace_other_user_denied(self, reg, tmp_path):
        from backend.api.handlers.workspaces import can_access_workspace

        ws = reg.create(str(tmp_path), user_id="alice")
        req = _req(SimpleNamespace(id="bob", role="member"))
        assert can_access_workspace(ws, req) is False

    def test_can_access_workspace_root_bypasses_ownership(self, reg, tmp_path):
        from backend.api.handlers.workspaces import can_access_workspace

        ws = reg.create(str(tmp_path), user_id="alice")
        req = _req(SimpleNamespace(id="bob", role="root"))
        assert can_access_workspace(ws, req) is True

    def test_can_access_workspace_no_owner_open_to_anyone(self, reg, tmp_path):
        from backend.api.handlers.workspaces import can_access_workspace

        ws = reg.get_or_create(str(tmp_path))
        req = _req(SimpleNamespace(id="whoever", role="member"))
        assert can_access_workspace(ws, req) is True

    def test_can_access_workspace_no_authenticated_user_treated_as_local_root(
        self, reg, tmp_path
    ):
        from backend.api.handlers.workspaces import can_access_workspace

        ws = reg.create(str(tmp_path), user_id="alice")
        # request.state.user None = CLI local sem auth → _is_privileged=True
        assert can_access_workspace(ws, _req(None)) is True

    def test_require_workspace_access_raises_403_for_other_user(self, reg, tmp_path):
        from fastapi import HTTPException

        from backend.api.handlers.workspaces import require_workspace_access

        ws = reg.create(str(tmp_path), user_id="alice")
        req = _req(SimpleNamespace(id="bob", role="member"))
        with pytest.raises(HTTPException) as exc:
            require_workspace_access(ws.id, req)
        assert exc.value.status_code == 403

    def test_require_workspace_access_returns_none_for_unknown_workspace(self, reg):
        from backend.api.handlers.workspaces import require_workspace_access

        assert require_workspace_access("naoexiste", _req(None)) is None

    def test_require_workspace_access_allows_owner(self, reg, tmp_path):
        from backend.api.handlers.workspaces import require_workspace_access

        ws = reg.create(str(tmp_path), user_id="alice")
        req = _req(SimpleNamespace(id="alice", role="member"))
        assert require_workspace_access(ws.id, req).id == ws.id

    @pytest.mark.asyncio
    async def test_list_workspaces_filters_out_other_users_workspaces(
        self, reg, tmp_path
    ):
        from backend.api.handlers.workspaces import list_workspaces

        sub_a = tmp_path / "a"
        sub_a.mkdir()
        sub_b = tmp_path / "b"
        sub_b.mkdir()
        reg.create(str(sub_a), user_id="alice")
        reg.create(str(sub_b), user_id="bob")

        req = _req(SimpleNamespace(id="alice", role="member"))
        result = await list_workspaces(req)

        ids = {w.cwd for w in result.workspaces}
        assert str(sub_a.resolve()) in ids
        assert str(sub_b.resolve()) not in ids

    @pytest.mark.asyncio
    async def test_set_active_workspace_forbidden_for_other_user(self, reg, tmp_path):
        from backend.api.handlers.workspaces import (
            SetActiveRequest,
            set_active_workspace,
        )

        ws = reg.create(str(tmp_path), user_id="alice")
        req = _req(SimpleNamespace(id="bob", role="member"))
        with pytest.raises(Exception) as exc:
            await set_active_workspace(req, SetActiveRequest(workspace_id=ws.id))
        assert getattr(exc.value, "status_code", None) == 403
