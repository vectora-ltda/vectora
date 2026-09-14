"""Git tools — operações locais em repositórios git via GitPython.

git_status, git_log, git_diff, git_branch, git_checkout, git_commit,
git_push, git_pull, git_stash.

Todas as tools:
- Resolvem o CWD via _resolve_workspace (workspace_id → ctx → cwd atual)
- Retornam JSON estruturado compatível com os render_hints declarados
- Funções-helper públicas (_git_*_impl) recebem git.Repo diretamente,
  facilitando testes unitários sem dependência do WorkspaceRegistry.

Dependência: gitpython >= 3.1
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import Callable
from typing import Any

# GitPython roda Git.refresh() em `import git` e levanta ImportError se não
# achar o executável git no PATH — precisa disso antes do import abaixo.
os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")

import git

from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _resolve_workspace(workspace_id: str | None, ctx: ToolContext) -> Any:
    """Resolve workspace → Workspace (mesmo padrão de workspace.py)."""
    from backend.workspace.workspace import workspace_registry

    wid = workspace_id or ctx.workspace_id or None
    if wid:
        ws = workspace_registry.get(wid)
        if ws is not None:
            return ws
    return workspace_registry.get_or_create()


def _open_repo(workspace_id: str | None, ctx: ToolContext) -> Any:
    """Abre git.Repo para o workspace ativo.

    Retorna (repo, None) em sucesso ou (None, error_json_str) em falha.
    """
    ws = _resolve_workspace(workspace_id, ctx)
    if ws is None:
        return None, json.dumps(
            {"status": "error", "message": "Workspace não encontrado."}
        )
    # git tools ainda só rodam contra repos locais. Workspaces
    # SSH/Codespace precisam usar `terminal` (ou tools `remote_git_*`
    # dedicadas).
    transport = str(getattr(ws, "transport", "local"))
    if transport != "local":
        return None, json.dumps(
            {
                "status": "remote_unsupported",
                "message": (
                    f"git tools ainda não suportam workspaces "
                    f"transport={transport!r}. Use a tool `terminal` "
                    "com comandos `git ...`."
                ),
            }
        )
    try:
        repo = git.Repo(ws.cwd, search_parent_directories=True)
        return repo, None
    except git.InvalidGitRepositoryError:
        return None, json.dumps(
            {
                "status": "not_git",
                "message": f"Não é um repositório git: {ws.cwd}",
            }
        )
    except Exception as exc:
        return None, json.dumps({"status": "error", "message": str(exc)})


def _safe_call(fn: Callable[[], dict]) -> dict:
    """Executa uma operação git e nunca deixa exceção escapar pra tool.

    ``_open_repo`` já blinda a abertura do repositório, mas as operações git
    de verdade (`repo.git.xxx`, `repo.active_branch`, `repo.untracked_files`)
    só falham quando de fato executadas — e a maioria dos ``_git_*_impl`` só
    trata ``git.GitCommandError``, não ``git.GitCommandNotFound`` (executável
    git ausente do sistema, caso comum numa máquina limpa).
    """
    try:
        return fn()
    except git.GitCommandNotFound:
        return {
            "status": "git_not_found",
            "message": "git não está instalado ou não foi encontrado no PATH.",
        }
    except Exception as exc:
        logger.exception("tools/git: falha inesperada")
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# Funções-helper públicas (testáveis diretamente)
# ---------------------------------------------------------------------------


def _git_status_impl(repo: git.Repo) -> dict:
    """Retorna o estado de trabalho do repositório."""
    from backend.services.git import status_snapshot

    return status_snapshot(repo)


def _git_log_impl(repo: git.Repo, n: int = 10, branch: str | None = None) -> dict:
    """Retorna histórico de commits."""
    try:
        ref = branch or repo.active_branch.name
    except TypeError:
        ref = "HEAD"

    try:
        commits = list(repo.iter_commits(ref, max_count=n))
    except git.GitCommandError:
        # repo vazio ou branch inválida
        return {"status": "ok", "commits": [], "branch": ref}

    return {
        "status": "ok",
        "branch": ref,
        "commits": [
            {
                "hash": c.hexsha[:7],
                "author": str(c.author),
                "date": c.authored_datetime.isoformat(),
                "message": c.message.strip().splitlines()[0],
            }
            for c in commits
        ],
    }


def _git_diff_impl(repo: git.Repo, ref: str | None = None) -> dict:
    """Retorna diff do working tree (ou em relação a ref)."""
    try:
        diff_text = repo.git.diff(ref) if ref else repo.git.diff()
        return {"status": "ok", "diff": diff_text}
    except git.GitCommandError as exc:
        return {"status": "error", "message": str(exc)}


def _git_branch_impl(
    repo: git.Repo,
    action: str,
    name: str | None = None,
) -> dict:
    """Operações em branches: list / create / delete."""
    if action == "list":
        branches = [b.name for b in repo.branches]
        try:
            current = repo.active_branch.name
        except TypeError:
            current = "(HEAD detached)"
        return {"status": "ok", "branches": branches, "current": current}

    if action == "create":
        if not name:
            return {"status": "error", "message": "Nome da branch é obrigatório."}
        try:
            repo.create_head(name)
            return {"status": "ok", "branch": name, "action": "created"}
        except git.GitCommandError as exc:
            return {"status": "error", "message": str(exc)}

    if action == "delete":
        if not name:
            return {"status": "error", "message": "Nome da branch é obrigatório."}
        try:
            repo.delete_head(name, force=True)
            return {"status": "ok", "branch": name, "action": "deleted"}
        except git.GitCommandError as exc:
            return {"status": "error", "message": str(exc)}

    return {"status": "error", "message": f"Ação desconhecida: {action}"}


def _git_checkout_impl(repo: git.Repo, ref: str, create: bool = False) -> dict:
    """Faz checkout para branch ou commit; `create=True` cria a branch antes
    (equivalente a `git checkout -b`) — a UI já combina os dois numa chamada
    só, as tools tinham só o checkout simples."""
    try:
        if create:
            repo.git.checkout("-b", ref)
        else:
            repo.git.checkout(ref)
        try:
            branch = repo.active_branch.name
        except TypeError:
            branch = ref
        return {"status": "ok", "branch": branch, "ref": ref, "created": create}
    except git.GitCommandError as exc:
        return {"status": "error", "message": str(exc)}


def _full_message(message: str, body: str | None) -> str:
    return f"{message}\n\n{body}" if body else message


def _git_commit_impl(
    repo: git.Repo,
    message: str,
    all: bool = False,  # noqa: A002
    body: str | None = None,
    amend: bool = False,
) -> dict:
    """Cria um commit (ou emenda o último, se `amend=True`).

    Args:
        message: Título do commit (formato conventional commits recomendado).
        all: Se True, stageia automaticamente arquivos modificados rastreados
             (equivalente a `git commit -a`).
        body: Descrição opcional (corpo do commit), concatenada como
              `title\n\nbody`.
        amend: Se True, substitui o último commit em vez de criar um novo —
               falha com mensagem clara se não houver commit anterior.
    """
    if all:
        repo.git.add("-u")

    full_message = _full_message(message, body)

    if amend:
        try:
            repo.head.commit  # noqa: B018 — dispara ValueError se repo sem commits
        except ValueError:
            return {
                "status": "error",
                "message": "Não há commit anterior para emendar (repo vazio).",
            }
        try:
            repo.git.commit("--amend", "-m", full_message)
            commit = repo.head.commit
            return {
                "status": "ok",
                "hash": commit.hexsha[:7],
                "message": full_message,
                "amended": True,
            }
        except git.GitCommandError as exc:
            return {"status": "error", "message": str(exc)}

    if not repo.is_dirty(index=True):
        return {
            "status": "error",
            "message": "Nada staged para commitar. Use git_status para ver o estado.",
        }

    try:
        commit = repo.index.commit(full_message)
        return {
            "status": "ok",
            "hash": commit.hexsha[:7],
            "message": full_message,
            "files_changed": len(commit.stats.files),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _git_squash_impl(
    repo: git.Repo,
    base_ref: str,
    message: str,
    body: str | None = None,
) -> dict:
    """Squasha todos os commits de `base_ref` até HEAD numa mensagem só —
    `reset --soft` pra `base_ref` (mantém as mudanças staged) seguido de um
    commit novo. `base_ref` inválido ou repo com working tree sujo (fora do
    index) falha com mensagem clara, nunca deixa o repo em estado parcial."""
    try:
        base_commit = repo.commit(base_ref)
    except Exception as exc:
        return {"status": "error", "message": f"Ref inválida {base_ref!r}: {exc}"}

    try:
        head_before = repo.head.commit.hexsha
    except ValueError:
        return {"status": "error", "message": "Repo sem commits."}

    if base_commit.hexsha == head_before:
        return {
            "status": "error",
            "message": "base_ref já é o HEAD — nada para squashar.",
        }

    try:
        repo.git.reset("--soft", base_ref)
        commit = repo.index.commit(_full_message(message, body))
        return {
            "status": "ok",
            "hash": commit.hexsha[:7],
            "squashed_from": base_ref,
            "previous_head": head_before[:7],
        }
    except git.GitCommandError as exc:
        return {"status": "error", "message": str(exc)}


def _git_reorder_impl(repo: git.Repo, commits: list[str]) -> dict:
    """Reordena commits locais (ainda não pushados) pra sequência exata de
    `commits` — reset --hard pro parent do mais antigo do conjunto (não
    necessariamente `commits[0]`, que reflete a ordem FINAL desejada, não a
    ordem original no histórico), depois cherry-pick um a um na ordem
    pedida. Sequência computada, não rebase interativo com editor de texto
    livre. Conflito não resolvível automaticamente aborta o cherry-pick em
    andamento e reporta — nunca deixa o repo pela metade."""
    if not commits:
        return {"status": "error", "message": "Lista de commits vazia."}

    try:
        resolved = [repo.commit(sha).hexsha for sha in commits]
    except Exception as exc:
        return {"status": "error", "message": f"Commit inválido: {exc}"}

    try:
        history = [c.hexsha for c in repo.iter_commits()]  # newest → oldest
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

    resolved_set = set(resolved)
    oldest_sha = next((sha for sha in reversed(history) if sha in resolved_set), None)
    if oldest_sha is None:
        return {
            "status": "error",
            "message": "Nenhum dos commits informados está no histórico atual.",
        }

    oldest_commit = repo.commit(oldest_sha)
    if not oldest_commit.parents:
        return {
            "status": "error",
            "message": "Não é possível reordenar o commit raiz (sem parent).",
        }
    parent_sha = oldest_commit.parents[0].hexsha
    original_head = repo.head.commit.hexsha

    try:
        repo.git.reset("--hard", parent_sha)
    except git.GitCommandError as exc:
        return {"status": "error", "message": str(exc)}

    for sha in resolved:
        try:
            repo.git.cherry_pick(sha)
        except git.GitCommandError as exc:
            with contextlib.suppress(git.GitCommandError):
                repo.git.cherry_pick("--abort")
            with contextlib.suppress(git.GitCommandError):
                repo.git.reset("--hard", original_head)
            return {
                "status": "error",
                "message": f"Conflito ao reordenar em {sha}: {exc}",
            }

    return {
        "status": "ok",
        "commits": commits,
        "new_head": repo.head.commit.hexsha[:7],
    }


def _git_cherry_pick_impl(
    repo: git.Repo,
    sha: str,
    no_commit: bool = False,
) -> dict:
    """Cherry-pick de um commit de outra branch/ref. Commit já aplicado
    (idempotente) devolve erro claro em vez de duplicar; conflito não
    resolvível aborta o cherry-pick, nunca deixa o repo em estado pendente."""
    try:
        args = ["-n", sha] if no_commit else [sha]
        repo.git.cherry_pick(*args)
        return {
            "status": "ok",
            "sha": sha,
            "no_commit": no_commit,
            "head": None if no_commit else repo.head.commit.hexsha[:7],
        }
    except git.GitCommandError as exc:
        msg = str(exc)
        if "empty" in msg.lower() or "nothing to commit" in msg.lower():
            with contextlib.suppress(git.GitCommandError):
                repo.git.cherry_pick("--skip")
            return {
                "status": "error",
                "message": "Commit já aplicado (idempotente) — nada a fazer.",
            }
        with contextlib.suppress(git.GitCommandError):
            repo.git.cherry_pick("--abort")
        return {"status": "error", "message": msg}


def _git_fetch_impl(repo: git.Repo, remote: str = "origin") -> dict:
    """Baixa refs do remote sem integrar (sem tocar no working tree)."""
    try:
        repo.git.fetch(remote)
        return {"status": "ok", "remote": remote}
    except git.GitCommandError as exc:
        return {"status": "error", "message": str(exc)}


def _git_merge_impl(repo: git.Repo, branch: str, no_ff: bool = False) -> dict:
    """Faz merge de `branch` na branch ativa. Conflito devolve erro
    estruturado com o status atual (arquivos em conflito), não deixa a
    exceção crua escapar."""
    try:
        args = [branch, "--no-ff"] if no_ff else [branch]
        repo.git.merge(*args)
        return {"status": "ok", "branch": branch, "no_ff": no_ff}
    except git.GitCommandError as exc:
        conflicted = [str(p) for p in repo.index.unmerged_blobs()]
        return {
            "status": "conflict" if conflicted else "error",
            "message": str(exc),
            "conflicted_files": conflicted,
        }


def _git_revert_impl(repo: git.Repo, sha: str, no_commit: bool = False) -> dict:
    """Reverte um commit (cria um commit inverso, ou só stageia com
    `no_commit=True` — equivalente a `git revert --no-commit`)."""
    try:
        args = [sha, "--no-commit"] if no_commit else [sha]
        repo.git.revert(*args)
        return {
            "status": "ok",
            "sha": sha,
            "no_commit": no_commit,
            "head": None if no_commit else repo.head.commit.hexsha[:7],
        }
    except git.GitCommandError as exc:
        with contextlib.suppress(git.GitCommandError):
            repo.git.revert("--abort")
        return {"status": "error", "message": str(exc)}


def _git_compare_impl(
    repo: git.Repo, base: str, head: str, file_path: str | None = None
) -> dict:
    """Compara dois refs. Sem `file_path`: diff resumido (arquivos +
    status), mesmo formato de `git diff base...head --name-status`. Com
    `file_path`: hunks do diff daquele arquivo específico entre os dois
    refs (`git diff base...head -- <file_path>`)."""
    if file_path:
        try:
            patch = repo.git.diff(f"{base}...{head}", "--", file_path)
        except git.GitCommandError as exc:
            return {"status": "error", "message": str(exc)}
        return {
            "status": "ok",
            "base": base,
            "head": head,
            "path": file_path,
            "patch": patch,
        }

    try:
        name_status = repo.git.diff(f"{base}...{head}", "--name-status")
    except git.GitCommandError as exc:
        return {"status": "error", "message": str(exc)}

    files = []
    for line in name_status.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append({"status": parts[0], "path": parts[-1]})
    return {"status": "ok", "base": base, "head": head, "files": files}


def _git_resolve_conflict_impl(repo: git.Repo, path: str, strategy: str) -> dict:
    """Resolve um conflito de merge escolhendo um dos dois lados —
    `strategy` é `"ours"` ou `"theirs"`. Estagia o arquivo resolvido depois
    do checkout, pronto pra `git_commit` fechar o merge."""
    if strategy not in ("ours", "theirs"):
        return {
            "status": "error",
            "message": f"strategy deve ser 'ours' ou 'theirs', recebeu {strategy!r}.",
        }
    if not path:
        return {"status": "error", "message": "path é obrigatório."}
    try:
        repo.git.checkout(f"--{strategy}", "--", path)
        repo.git.add("--", path)
        return {"status": "ok", "path": path, "strategy": strategy}
    except git.GitCommandError as exc:
        return {"status": "error", "message": str(exc)}


def _git_push_impl(
    repo: git.Repo,
    remote: str = "origin",
    branch: str | None = None,
    force: bool = False,
) -> dict:
    """Faz push para remote."""
    try:
        active = branch or repo.active_branch.name
        args = [remote, active]
        if force:
            args.append("--force")
        repo.git.push(*args)
        return {"status": "ok", "remote": remote, "branch": active, "forced": force}
    except git.GitCommandError as exc:
        return {"status": "error", "message": str(exc)}


def _git_pull_impl(
    repo: git.Repo,
    remote: str = "origin",
    branch: str | None = None,
) -> dict:
    """Faz pull do remote."""
    try:
        active = branch or repo.active_branch.name
        repo.git.pull(remote, active)
        return {"status": "ok", "remote": remote, "branch": active}
    except git.GitCommandError as exc:
        return {"status": "error", "message": str(exc)}


def _git_stash_impl(
    repo: git.Repo,
    action: str,
    name: str | None = None,
) -> dict:
    """Operações de stash: push / pop / list / drop."""
    if action == "push":
        try:
            args = ["push"]
            if name:
                args += ["-m", name]
            out = repo.git.stash(*args)
            if "No local changes" in out:
                return {"status": "info", "message": "Nada para salvar no stash."}
            return {"status": "ok", "action": "push", "message": out.strip()}
        except git.GitCommandError as exc:
            return {"status": "error", "message": str(exc)}

    if action == "pop":
        try:
            repo.git.stash("pop")
            return {"status": "ok", "action": "pop"}
        except git.GitCommandError as exc:
            return {"status": "error", "message": str(exc)}

    if action == "apply":
        try:
            repo.git.stash("apply")
            return {"status": "ok", "action": "apply"}
        except git.GitCommandError as exc:
            return {"status": "error", "message": str(exc)}

    if action == "list":
        try:
            out = repo.git.stash("list")
            entries = [ln.strip() for ln in out.splitlines() if ln.strip()]
            return {"status": "ok", "action": "list", "entries": entries}
        except git.GitCommandError as exc:
            return {"status": "error", "message": str(exc)}

    if action == "drop":
        try:
            repo.git.stash("drop")
            return {"status": "ok", "action": "drop"}
        except git.GitCommandError as exc:
            return {"status": "error", "message": str(exc)}

    return {"status": "error", "message": f"Ação desconhecida: {action}"}


def git_init_repo(cwd: str) -> dict:
    """Inicializa um repositório git no diretório informado.

    Idempotente: se já houver um repositório, retorna status ``already``.
    """
    from pathlib import Path

    try:
        existing = git.Repo(cwd, search_parent_directories=False)
        return {
            "status": "already",
            "path": str(Path(existing.working_dir)),
            "branch": _safe_branch(existing),
        }
    except git.InvalidGitRepositoryError:
        pass
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

    try:
        repo = git.Repo.init(cwd)
        return {
            "status": "ok",
            "path": str(Path(repo.working_dir)),
            "branch": _safe_branch(repo),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _safe_branch(repo: git.Repo) -> str | None:
    """Nome da branch ativa, ou None em repo recém-criado sem commits."""
    try:
        return repo.active_branch.name
    except (TypeError, ValueError):
        with contextlib.suppress(Exception):
            return repo.head.ref.name
        return None


def _worktrees_root(workspace_id: str) -> Any:
    """Diretório base das worktrees de um workspace."""
    from pathlib import Path

    return Path.home() / ".vectora" / "worktrees" / workspace_id


def _git_worktree_impl(
    repo: git.Repo,
    workspace_id: str,
    action: str,
    name: str | None = None,
    branch: str | None = None,
) -> dict:
    """Operações de worktree: add / list / remove."""
    from pathlib import Path

    if action == "list":
        try:
            out = repo.git.worktree("list", "--porcelain")
            entries: list[dict] = []
            current: dict = {}
            for line in out.splitlines():
                if line.startswith("worktree "):
                    if current:
                        entries.append(current)
                    current = {"path": line[len("worktree ") :]}
                elif line.startswith("branch "):
                    current["branch"] = line[len("branch ") :]
                elif line.startswith("HEAD "):
                    current["head"] = line[len("HEAD ") :][:7]
            if current:
                entries.append(current)
            return {"status": "ok", "action": "list", "worktrees": entries}
        except git.GitCommandError as exc:
            return {"status": "error", "message": str(exc)}

    if action == "add":
        if not name:
            return {"status": "error", "message": "Nome da worktree é obrigatório."}
        wt_path = _worktrees_root(workspace_id) / name
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            args = ["add"]
            if branch:
                args += ["-b", branch, str(wt_path)]
            else:
                args += [str(wt_path)]
            repo.git.worktree(*args)
            return {
                "status": "ok",
                "action": "add",
                "name": name,
                "path": str(wt_path),
                "branch": branch,
            }
        except git.GitCommandError as exc:
            return {"status": "error", "message": str(exc)}

    if action == "remove":
        if not name:
            return {"status": "error", "message": "Nome da worktree é obrigatório."}
        wt_path = _worktrees_root(workspace_id) / name
        try:
            repo.git.worktree("remove", str(wt_path), "--force")
            return {"status": "ok", "action": "remove", "name": name}
        except git.GitCommandError as exc:
            return {"status": "error", "message": str(exc)}

    return {"status": "error", "message": f"Ação desconhecida: {action}"}


# ---------------------------------------------------------------------------
# Stage / unstage / restore por caminho
# ---------------------------------------------------------------------------


def _git_stage_impl(repo: git.Repo, path: str) -> dict:
    """Stageia um arquivo específico (`git add <path>`)."""
    try:
        repo.git.add("--", path)
        return {"status": "ok", "path": path, "action": "stage"}
    except git.GitCommandError as exc:
        return {"status": "error", "message": str(exc)}


def _git_unstage_impl(repo: git.Repo, path: str) -> dict:
    """Remove um arquivo do stage (`git reset HEAD <path>`)."""
    try:
        repo.git.reset("HEAD", "--", path)
        return {"status": "ok", "path": path, "action": "unstage"}
    except git.GitCommandError as exc:
        return {"status": "error", "message": str(exc)}


def _git_restore_impl(repo: git.Repo, path: str) -> dict:
    """Descarta mudanças não staged (`git restore -- <path>`).

    Atenção: operação destrutiva — as mudanças no worktree são perdidas.
    """
    try:
        repo.git.restore("--", path)
        return {"status": "ok", "path": path, "action": "restore"}
    except git.GitCommandError as exc:
        # fallback: checkout antigo
        try:
            repo.git.checkout("--", path)
            return {"status": "ok", "path": path, "action": "restore"}
        except git.GitCommandError:
            return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# pre-commit dry-run
# ---------------------------------------------------------------------------


def _run_pre_commit_hooks(repo: git.Repo) -> dict:
    """Executa os hooks pre-commit sem criar um commit.

    Retorna ``{"passed": True}`` se todos os hooks passaram, ou
    ``{"passed": False, "output": "<stdout+stderr>"}`` em caso de falha.
    Quando não há hook configurado, considera como passado.
    """
    import os
    import subprocess  # nosec B404
    from pathlib import Path

    hook_path = Path(repo.git_dir) / "hooks" / "pre-commit"
    if not hook_path.is_file() or not os.access(hook_path, os.X_OK):
        return {"passed": True, "output": ""}

    try:
        result = subprocess.run(  # noqa: S603 # nosec B603
            [str(hook_path)],
            cwd=repo.working_dir,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            return {"passed": True, "output": output}
        return {"passed": False, "output": output}
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "pre-commit hook timed out (60s)"}
    except OSError as exc:
        return {"passed": False, "output": str(exc)}


# ---------------------------------------------------------------------------
# tools nativas
# ---------------------------------------------------------------------------


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=False,
        icon="git-branch",
    )
)
async def git_status(ctx: ToolContext, workspace_id: str | None = None) -> str:
    """Mostra o estado do repositório git: arquivos modificados, staged, untracked.

    Retorna branch ativa, contagem ahead/behind e listas de arquivos por categoria.
    Use antes de commitar ou criar PR para garantir que o estado está correto.

    Args:
        workspace_id: ID do workspace (usa o workspace ativo se omitido).
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    return json.dumps(_safe_call(lambda: _git_status_impl(repo)))


@vtool(
    extras=ToolExtras(
        render_hint="table",
        category="git",
        destructive=False,
        icon="git-commit",
    )
)
async def git_log(
    ctx: ToolContext,
    n: int = 10,
    branch: str | None = None,
    workspace_id: str | None = None,
) -> str:
    """Exibe o histórico de commits do repositório.

    Args:
        n: Número de commits a retornar (default: 10).
        branch: Branch específica (usa branch ativa se omitida).
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    return json.dumps(_safe_call(lambda: _git_log_impl(repo, n=n, branch=branch)))


@vtool(
    extras=ToolExtras(
        render_hint="diff",
        category="git",
        destructive=False,
        icon="diff",
    )
)
async def git_diff(
    ctx: ToolContext,
    ref: str | None = None,
    workspace_id: str | None = None,
) -> str:
    """Mostra o diff do working tree ou em relação a um commit/branch.

    Args:
        ref: Commit hash, tag ou branch para comparar (compara working tree se omitido).
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    return json.dumps(_safe_call(lambda: _git_diff_impl(repo, ref=ref)))


@vtool(
    extras=ToolExtras(
        render_hint="table",
        category="git",
        destructive=False,
        icon="git-branch",
    )
)
async def git_branch(
    ctx: ToolContext,
    action: str = "list",
    name: str | None = None,
    workspace_id: str | None = None,
) -> str:
    """Gerencia branches: lista, cria ou deleta.

    Args:
        action: "list" | "create" | "delete"
        name: Nome da branch (obrigatório para create/delete).
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    return json.dumps(
        _safe_call(lambda: _git_branch_impl(repo, action=action, name=name))
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=True,
        icon="git-branch",
        invalidates=["files", "diff"],
    )
)
async def git_checkout(
    ctx: ToolContext,
    ref: str = "",
    create: bool = False,
    workspace_id: str | None = None,
) -> str:
    """Troca para uma branch ou commit; `create=True` cria a branch antes.

    ⚠️ Mudanças não commitadas podem ser perdidas. Faça git_status antes.

    Args:
        ref: Branch, tag ou commit hash para checkout (ou nome da branch nova
             quando `create=True`).
        create: Se True, cria `ref` como branch nova antes do checkout
                (equivalente a `git checkout -b`).
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    if not ref:
        return json.dumps({"status": "error", "message": "ref é obrigatório."})
    return json.dumps(
        _safe_call(lambda: _git_checkout_impl(repo, ref=ref, create=create))
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=True,
        icon="git-commit",
        invalidates=["diff"],
    )
)
async def git_commit(
    ctx: ToolContext,
    message: str = "",
    all: bool = False,  # noqa: A002
    body: str | None = None,
    amend: bool = False,
    workspace_id: str | None = None,
) -> str:
    """Cria um commit com os arquivos staged (ou emenda o último, com `amend`).

    Use conventional commits: feat:, fix:, refactor:, docs:, test:, chore:.
    Sempre escreva mensagens descritivas — nunca "wip" ou "update".

    Args:
        message: Título do commit (conventional commits recomendado).
        all: Se True, stageia automaticamente modificações rastreadas (-a).
        body: Descrição opcional do commit (corpo, separado do título).
        amend: Se True, substitui o último commit em vez de criar um novo.
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    if not message:
        return json.dumps(
            {"status": "error", "message": "Mensagem de commit é obrigatória."}
        )
    return json.dumps(
        _safe_call(
            lambda: _git_commit_impl(
                repo, message=message, all=all, body=body, amend=amend
            )
        )
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=True,
        icon="git-commit",
        invalidates=["diff", "history"],
    )
)
async def git_squash(
    ctx: ToolContext,
    base_ref: str = "",
    message: str = "",
    body: str | None = None,
    workspace_id: str | None = None,
) -> str:
    """Squasha os commits de `base_ref` até HEAD numa mensagem só.

    Args:
        base_ref: Ref (branch/SHA) a partir da qual squashar até HEAD.
        message: Título do commit resultante.
        body: Descrição opcional do commit resultante.
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    if not base_ref or not message:
        return json.dumps(
            {"status": "error", "message": "base_ref e message são obrigatórios."}
        )
    return json.dumps(
        _safe_call(
            lambda: _git_squash_impl(
                repo, base_ref=base_ref, message=message, body=body
            )
        )
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=True,
        icon="git-commit",
        invalidates=["diff", "history"],
    )
)
async def git_reorder(
    ctx: ToolContext,
    commits: list[str] | None = None,
    workspace_id: str | None = None,
) -> str:
    """Reordena commits locais (ainda não pushados) pra sequência de `commits`.

    Args:
        commits: SHAs na ordem final desejada (do mais antigo pro mais novo).
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    return json.dumps(
        _safe_call(lambda: _git_reorder_impl(repo, commits=commits or []))
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=True,
        icon="git-commit",
        invalidates=["diff", "history"],
    )
)
async def git_cherry_pick(
    ctx: ToolContext,
    sha: str = "",
    no_commit: bool = False,
    workspace_id: str | None = None,
) -> str:
    """Aplica um commit de outra branch/ref na branch ativa.

    Args:
        sha: Hash do commit a aplicar.
        no_commit: Se True, só stageia as mudanças (sem criar o commit).
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    if not sha:
        return json.dumps({"status": "error", "message": "sha é obrigatório."})
    return json.dumps(
        _safe_call(lambda: _git_cherry_pick_impl(repo, sha=sha, no_commit=no_commit))
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=False,
        icon="download-cloud",
    )
)
async def git_fetch(
    ctx: ToolContext,
    remote: str = "origin",
    workspace_id: str | None = None,
) -> str:
    """Baixa refs do remote sem integrar (não toca no working tree).

    Args:
        remote: Nome do remote (default: "origin").
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    return json.dumps(_safe_call(lambda: _git_fetch_impl(repo, remote=remote)))


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=True,
        icon="git-merge",
        invalidates=["files", "diff", "history"],
    )
)
async def git_merge(
    ctx: ToolContext,
    branch: str = "",
    no_ff: bool = False,
    workspace_id: str | None = None,
) -> str:
    """Faz merge de `branch` na branch ativa.

    Args:
        branch: Branch a mesclar na branch ativa.
        no_ff: Se True, força um merge commit mesmo quando fast-forward seria
               possível.
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    if not branch:
        return json.dumps({"status": "error", "message": "branch é obrigatório."})
    return json.dumps(
        _safe_call(lambda: _git_merge_impl(repo, branch=branch, no_ff=no_ff))
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=True,
        icon="git-commit",
        invalidates=["files", "diff", "history"],
    )
)
async def git_revert(
    ctx: ToolContext,
    sha: str = "",
    no_commit: bool = False,
    workspace_id: str | None = None,
) -> str:
    """Reverte um commit — cria um commit inverso (ou só stageia, com
    `no_commit`).

    Args:
        sha: Hash do commit a reverter.
        no_commit: Se True, só stageia as mudanças (sem criar o commit).
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    if not sha:
        return json.dumps({"status": "error", "message": "sha é obrigatório."})
    return json.dumps(
        _safe_call(lambda: _git_revert_impl(repo, sha=sha, no_commit=no_commit))
    )


@vtool(
    extras=ToolExtras(
        render_hint="table",
        category="git",
        destructive=False,
        icon="git-compare",
    )
)
async def git_compare(
    ctx: ToolContext,
    base: str = "",
    head: str = "",
    file_path: str | None = None,
    workspace_id: str | None = None,
) -> str:
    """Compara dois refs — lista arquivos alterados entre `base` e `head`,
    ou (com `file_path`) o diff (hunks) de um arquivo específico.

    Args:
        base: Ref base da comparação.
        head: Ref alvo da comparação.
        file_path: Opcional — devolve os hunks do diff só desse arquivo em
            vez da lista de arquivos alterados.
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    if not base or not head:
        return json.dumps(
            {"status": "error", "message": "base e head são obrigatórios."}
        )
    return json.dumps(
        _safe_call(
            lambda: _git_compare_impl(repo, base=base, head=head, file_path=file_path)
        )
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=True,
        icon="git-merge",
        invalidates=["files", "diff"],
    )
)
async def git_resolve_conflict(
    ctx: ToolContext,
    path: str = "",
    strategy: str = "",
    workspace_id: str | None = None,
) -> str:
    """Resolve um conflito de merge escolhendo um dos dois lados.

    Args:
        path: Caminho do arquivo em conflito.
        strategy: "ours" (mantém o lado atual) ou "theirs" (usa o lado
                  incoming).
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    return json.dumps(
        _safe_call(
            lambda: _git_resolve_conflict_impl(repo, path=path, strategy=strategy)
        )
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=False,
        icon="check-circle",
    )
)
async def git_check_hooks(ctx: ToolContext, workspace_id: str | None = None) -> str:
    """Roda os hooks de pre-commit configurados sem criar um commit
    (dry-run) — útil pra checar se o working tree passaria antes de
    commitar de verdade.

    Args:
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    return json.dumps(_safe_call(lambda: _run_pre_commit_hooks(repo)))


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=True,
        icon="upload-cloud",
    )
)
async def git_push(
    ctx: ToolContext,
    remote: str = "origin",
    branch: str | None = None,
    force: bool = False,
    workspace_id: str | None = None,
) -> str:
    """Envia commits locais para o remote.

    ⚠️ Force push em branches compartilhadas pode sobrescrever histórico remoto.

    Args:
        remote: Nome do remote (default: "origin").
        branch: Branch a enviar (usa branch ativa se omitida).
        force: Se True, usa --force (cuidado em branches compartilhadas).
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    return json.dumps(
        _safe_call(
            lambda: _git_push_impl(repo, remote=remote, branch=branch, force=force)
        )
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=True,
        icon="download-cloud",
        invalidates=["files", "diff"],
    )
)
async def git_pull(
    ctx: ToolContext,
    remote: str = "origin",
    branch: str | None = None,
    workspace_id: str | None = None,
) -> str:
    """Baixa e integra commits do remote.

    Args:
        remote: Nome do remote (default: "origin").
        branch: Branch a baixar (usa branch ativa se omitida).
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    return json.dumps(
        _safe_call(lambda: _git_pull_impl(repo, remote=remote, branch=branch))
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=False,
        icon="layers",
        invalidates=["diff"],
    )
)
async def git_stash(
    ctx: ToolContext,
    action: str = "push",
    name: str | None = None,
    workspace_id: str | None = None,
) -> str:
    """Gerencia o stash: salva, aplica ou lista mudanças temporárias.

    Args:
        action: "push" | "pop" | "apply" | "list" | "drop" — "pop" aplica
            e remove do stash; "apply" aplica sem remover.
        name: Nome descritivo do stash (opcional, apenas para push).
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    return json.dumps(
        _safe_call(lambda: _git_stash_impl(repo, action=action, name=name))
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=False,
        icon="git-branch",
    )
)
async def git_init(ctx: ToolContext, workspace_id: str | None = None) -> str:
    """Inicializa um repositório git no workspace ativo.

    Útil quando a pasta selecionada ainda não é um repositório. Idempotente —
    se já houver um repositório, apenas reporta o estado atual.

    Args:
        workspace_id: ID do workspace (usa o workspace ativo se omitido).
    """
    ws = _resolve_workspace(workspace_id, ctx)
    if ws is None:
        return json.dumps({"status": "error", "message": "Workspace não encontrado."})
    return json.dumps(_safe_call(lambda: git_init_repo(ws.cwd)))


@vtool(
    extras=ToolExtras(
        render_hint="table",
        category="git",
        destructive=True,
        icon="git-branch",
    )
)
async def git_worktree(
    ctx: ToolContext,
    action: str = "list",
    name: str | None = None,
    branch: str | None = None,
    workspace_id: str | None = None,
) -> str:
    """Gerencia worktrees do repositório: lista, adiciona ou remove.

    Worktrees permitem trabalhar em múltiplas branches em paralelo sem trocar
    o checkout principal. Ficam em ~/.vectora/worktrees/<workspace>/<name>.

    Args:
        action: "list" | "add" | "remove"
        name: Nome da worktree (obrigatório para add/remove).
        branch: Branch a criar na worktree (apenas para add).
        workspace_id: ID do workspace.
    """
    ws = _resolve_workspace(workspace_id, ctx)
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    return json.dumps(
        _safe_call(
            lambda: _git_worktree_impl(
                repo, ws.id, action=action, name=name, branch=branch
            )
        )
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=False,
        icon="plus-circle",
        invalidates=["diff"],
    )
)
async def git_stage(
    ctx: ToolContext,
    path: str = "",
    workspace_id: str | None = None,
) -> str:
    """Stageia um arquivo específico (`git add <path>`).

    Use antes de git_commit para preparar os arquivos desejados.

    Args:
        path: Caminho do arquivo a stagear (relativo à raiz do repositório).
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    if not path:
        return json.dumps({"status": "error", "message": "path é obrigatório."})
    return json.dumps(_safe_call(lambda: _git_stage_impl(repo, path=path)))


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=False,
        icon="minus-circle",
        invalidates=["diff"],
    )
)
async def git_unstage(
    ctx: ToolContext,
    path: str = "",
    workspace_id: str | None = None,
) -> str:
    """Remove um arquivo do stage (`git reset HEAD <path>`).

    Args:
        path: Caminho do arquivo a remover do stage.
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    if not path:
        return json.dumps({"status": "error", "message": "path é obrigatório."})
    return json.dumps(_safe_call(lambda: _git_unstage_impl(repo, path=path)))


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="git",
        destructive=True,
        icon="undo-2",
        invalidates=["diff", "files"],
    )
)
async def git_discard(
    ctx: ToolContext,
    path: str = "",
    workspace_id: str | None = None,
) -> str:
    """Descarta mudanças não staged de um arquivo (`git restore -- <path>`,
    mesma paridade do botão "Descartar" da aba Git — reaproveita
    `_git_restore_impl`, já usado internamente).

    Atenção: operação destrutiva — as mudanças no worktree são perdidas
    permanentemente, sem confirmação adicional além da aprovação HITL.

    Args:
        path: Caminho do arquivo cujas mudanças serão descartadas.
        workspace_id: ID do workspace.
    """
    repo, err = _open_repo(workspace_id, ctx)
    if err:
        return err
    if not path:
        return json.dumps({"status": "error", "message": "path é obrigatório."})
    return json.dumps(_safe_call(lambda: _git_restore_impl(repo, path=path)))


# ---------------------------------------------------------------------------
# Helpers para detecção de git no workspace
# ---------------------------------------------------------------------------


def detect_git_info(cwd: str) -> dict:
    """Detecta se o diretório é um repo git e retorna informações básicas.

    Retorna dict com is_git_repo, branch, remote e default_branch.
    Usado por WorkspaceRegistry.get_or_create() para preencher campos git.
    """
    try:
        repo = git.Repo(cwd, search_parent_directories=True)
        try:
            branch = repo.active_branch.name
        except TypeError:
            branch = None
        remotes = [r.name for r in repo.remotes]
        remote_url = None
        if repo.remotes:
            with contextlib.suppress(Exception):
                remote_url = repo.remotes[0].url
        return {
            "is_git_repo": True,
            "git_current_branch": branch,
            "git_remote": remote_url,
            "git_remotes": remotes,
        }
    except git.InvalidGitRepositoryError:
        return {"is_git_repo": False}
    except Exception:
        return {"is_git_repo": False}
