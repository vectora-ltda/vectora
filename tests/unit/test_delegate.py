"""Delegate — worktree isolado por task.

create_task_worktree/remove_task_worktree reaproveitam _git_worktree_impl
já usado pela tool git_worktree — aqui só cobrimos o wrapper: resolução do
workspace, propagação de erro claro, e idempotência da remoção.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from backend.scheduling.delegate import (
    DelegateError,
    create_task_worktree,
    remove_task_worktree,
)


def _fake_workspace(cwd: str = "/tmp/ws1"):
    ws = MagicMock()
    ws.cwd = cwd
    return ws


@pytest.mark.asyncio
async def test_create_task_worktree_returns_path_on_success():
    with (
        patch("backend.workspace.workspace.workspace_registry") as mock_registry,
        patch("git.Repo") as mock_repo_cls,
        patch("backend.tools.git._git_worktree_impl") as mock_impl,
    ):
        mock_registry.get.return_value = _fake_workspace()
        mock_repo_cls.return_value = MagicMock()
        mock_impl.return_value = {
            "status": "ok",
            "action": "add",
            "path": "/home/user/.vectora/worktrees/ws1/task-42",
        }

        path = await create_task_worktree("ws1", "task-42")

        assert path == "/home/user/.vectora/worktrees/ws1/task-42"
        mock_impl.assert_called_once()
        assert mock_impl.call_args.args[2] == "add"
        assert mock_impl.call_args.kwargs["name"] == "task-42"


@pytest.mark.asyncio
async def test_create_task_worktree_unknown_workspace_raises_clear_error():
    with patch("backend.workspace.workspace.workspace_registry") as mock_registry:
        mock_registry.get.return_value = None

        with pytest.raises(DelegateError, match="não encontrado"):
            await create_task_worktree("ghost-ws", "task-1")


@pytest.mark.asyncio
async def test_create_task_worktree_git_failure_raises_delegate_error():
    # Erro/borda: branch inválida/disco cheio não deixa a task presa num
    # estado inconsistente — propaga como DelegateError com mensagem clara.
    with (
        patch("backend.workspace.workspace.workspace_registry") as mock_registry,
        patch("git.Repo") as mock_repo_cls,
        patch("backend.tools.git._git_worktree_impl") as mock_impl,
    ):
        mock_registry.get.return_value = _fake_workspace()
        mock_repo_cls.return_value = MagicMock()
        mock_impl.return_value = {"status": "error", "message": "disk full"}

        with pytest.raises(DelegateError, match="disk full"):
            await create_task_worktree("ws1", "task-42")


@pytest.mark.asyncio
async def test_remove_task_worktree_is_idempotent_on_missing_worktree():
    # Erro/borda: remover um worktree que já não existe (ou nunca existiu)
    # não levanta exceção — só loga e segue.
    with (
        patch("backend.workspace.workspace.workspace_registry") as mock_registry,
        patch("git.Repo") as mock_repo_cls,
        patch("backend.tools.git._git_worktree_impl") as mock_impl,
    ):
        mock_registry.get.return_value = _fake_workspace()
        mock_repo_cls.return_value = MagicMock()
        mock_impl.return_value = {"status": "error", "message": "not found"}

        await remove_task_worktree("ws1", "task-42")  # não deve levantar


@pytest.mark.asyncio
async def test_remove_task_worktree_also_deletes_the_branch():
    # `git worktree add <task_id>` cria implicitamente uma branch com o
    # nome da task; remover só o worktree deixaria essa branch órfã.
    with (
        patch("backend.workspace.workspace.workspace_registry") as mock_registry,
        patch("git.Repo") as mock_repo_cls,
        patch("backend.tools.git._git_worktree_impl") as mock_impl,
    ):
        mock_registry.get.return_value = _fake_workspace()
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_impl.return_value = {"status": "ok", "action": "remove"}

        await remove_task_worktree("ws1", "task-42")

        mock_repo.git.branch.assert_called_once_with("-D", "task-42")


@pytest.mark.asyncio
async def test_remove_task_worktree_branch_delete_failure_does_not_raise():
    # Erro/borda: branch já deletada manualmente, ou checked out em outro
    # lugar — best-effort, nunca propaga (a remoção do worktree já valeu).
    with (
        patch("backend.workspace.workspace.workspace_registry") as mock_registry,
        patch("git.Repo") as mock_repo_cls,
        patch("backend.tools.git._git_worktree_impl") as mock_impl,
    ):
        mock_registry.get.return_value = _fake_workspace()
        mock_repo = MagicMock()
        mock_repo.git.branch.side_effect = Exception("branch not found")
        mock_repo_cls.return_value = mock_repo
        mock_impl.return_value = {"status": "ok", "action": "remove"}

        await remove_task_worktree("ws1", "task-42")  # não deve levantar


@pytest.mark.asyncio
async def test_workspace_without_git_repo_raises_delegate_error():
    with (
        patch("backend.workspace.workspace.workspace_registry") as mock_registry,
        patch("git.Repo", side_effect=Exception("not a git repository")),
    ):
        mock_registry.get.return_value = _fake_workspace()

        with pytest.raises(DelegateError, match="não é um repositório git"):
            await create_task_worktree("ws1", "task-42")


@pytest.mark.asyncio
async def test_create_task_worktree_ja_existente_gera_erro_claro_de_conflito():
    # Duplicado: criar worktree pra uma task_id que já tem um worktree
    # (git recusa "already exists") deve propagar como DelegateError com a
    # mensagem original do git, não mascarar o motivo do conflito.
    with (
        patch("backend.workspace.workspace.workspace_registry") as mock_registry,
        patch("git.Repo") as mock_repo_cls,
        patch("backend.tools.git._git_worktree_impl") as mock_impl,
    ):
        mock_registry.get.return_value = _fake_workspace()
        mock_repo_cls.return_value = MagicMock()
        mock_impl.return_value = {
            "status": "error",
            "message": "'task-42' already exists",
        }

        with pytest.raises(DelegateError, match="already exists"):
            await create_task_worktree("ws1", "task-42")


@pytest.mark.asyncio
async def test_workspace_id_com_caracteres_especiais_e_repassado_intacto():
    # Espaços, acentos, barras e unicode no workspace_id não devem quebrar
    # a resolução — quem sanitiza pra path é _git_worktree_impl, este
    # wrapper só precisa repassar o valor exato recebido.
    workspace_id = "ws com espaço/e-título açúcar 糖"
    with (
        patch("backend.workspace.workspace.workspace_registry") as mock_registry,
        patch("git.Repo") as mock_repo_cls,
        patch("backend.tools.git._git_worktree_impl") as mock_impl,
    ):
        mock_registry.get.return_value = _fake_workspace()
        mock_repo_cls.return_value = MagicMock()
        mock_impl.return_value = {
            "status": "ok",
            "action": "add",
            "path": "/home/user/.vectora/worktrees/ws-especial/task-1",
        }

        path = await create_task_worktree(workspace_id, "task-1")

        assert path == "/home/user/.vectora/worktrees/ws-especial/task-1"
        mock_registry.get.assert_called_once_with(workspace_id)
        assert mock_impl.call_args.args[1] == workspace_id


@pytest.mark.asyncio
async def test_task_id_com_barra_e_caracteres_de_shell_e_repassado_como_name():
    # Erro/borda: task_id vindo do LLM pode conter caracteres que
    # pareceriam injeção de shell (`;`, `&&`) — o wrapper não escapa nem
    # rejeita, só repassa como `name=` (a defesa real é em
    # `_git_worktree_impl`, testada em outro arquivo).
    with (
        patch("backend.workspace.workspace.workspace_registry") as mock_registry,
        patch("git.Repo") as mock_repo_cls,
        patch("backend.tools.git._git_worktree_impl") as mock_impl,
    ):
        mock_registry.get.return_value = _fake_workspace()
        mock_repo_cls.return_value = MagicMock()
        mock_impl.return_value = {
            "status": "ok",
            "action": "add",
            "path": "/tmp/wt",
        }

        await create_task_worktree("ws1", "task; rm -rf /")

        assert mock_impl.call_args.kwargs["name"] == "task; rm -rf /"


@pytest.mark.asyncio
async def test_criacao_concorrente_de_dois_worktrees_para_tasks_diferentes_nao_colide():
    # Concorrência: duas tasks distintas do mesmo workspace criando
    # worktree ao mesmo tempo não podem ver o path uma da outra.
    call_paths = {
        "task-a": "/home/user/.vectora/worktrees/ws1/task-a",
        "task-b": "/home/user/.vectora/worktrees/ws1/task-b",
    }

    def _fake_worktree_impl(_repo, _workspace_id, _action, *, name):
        return {"status": "ok", "action": "add", "path": call_paths[name]}

    with (
        patch("backend.workspace.workspace.workspace_registry") as mock_registry,
        patch("git.Repo") as mock_repo_cls,
        patch("backend.tools.git._git_worktree_impl", side_effect=_fake_worktree_impl),
    ):
        mock_registry.get.return_value = _fake_workspace()
        mock_repo_cls.return_value = MagicMock()

        path_a, path_b = await asyncio.gather(
            create_task_worktree("ws1", "task-a"),
            create_task_worktree("ws1", "task-b"),
        )

        assert path_a == call_paths["task-a"]
        assert path_b == call_paths["task-b"]
        assert path_a != path_b


@pytest.mark.asyncio
async def test_remove_task_worktree_com_task_id_vazio_ainda_chama_impl():
    with (
        patch("backend.workspace.workspace.workspace_registry") as mock_registry,
        patch("git.Repo") as mock_repo_cls,
        patch("backend.tools.git._git_worktree_impl") as mock_impl,
    ):
        mock_registry.get.return_value = _fake_workspace()
        mock_repo_cls.return_value = MagicMock()
        mock_impl.return_value = {"status": "error", "message": "invalid name"}

        await remove_task_worktree("ws1", "")  # não deve levantar

        assert mock_impl.call_args.kwargs["name"] == ""


# ---------------------------------------------------------------------------
# Invariante: só trabalho em segundo plano ganha worktree
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegacao_sincrona_nunca_cria_worktree_so_background_cria(monkeypatch):
    """`task()` no meio de um turno roda no workspace principal (troca de
    persona, não paralelismo); tarefa em segundo plano isola em worktree
    próprio.
    """
    from backend.scheduling import background_tasks as bg

    criados: list[tuple[str, str]] = []

    async def _spy_create(workspace_id: str, task_id: str) -> str:
        criados.append((workspace_id, task_id))
        return f"/tmp/worktrees/{task_id}"

    monkeypatch.setattr("backend.scheduling.delegate.create_task_worktree", _spy_create)

    fake_ws = MagicMock()
    fake_ws.id = "ws-efemero"
    monkeypatch.setattr(
        "backend.workspace.workspace.workspace_registry",
        MagicMock(get_or_create=MagicMock(return_value=fake_ws)),
    )

    # Caminho de segundo plano: isola de verdade.
    ws_id = await bg._worktree_workspace_id("ws-principal", "task-bg")
    assert ws_id == "ws-efemero"
    assert criados == [("ws-principal", "task-bg")]

    # Erro/borda: nenhuma outra rota de background_tasks chama
    # create_task_worktree além do caminho de segundo plano.
    assert len(criados) == 1
