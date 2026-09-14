"""Serviço assíncrono compartilhado para operações Git do Workbench."""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

import git

_T = TypeVar("_T")
_REDACT_USERINFO = re.compile(r"(://)[^/@\s]+@")
_REDACT_SECRET = re.compile(
    r"(?i)(?P<key>token|secret|password)=(?P<value>[^\s&]+)"
    r"|(?P<authorization>authorization)(?P<separator>\s*[=:]\s*)"
    r"(?P<auth_value>[^\s&]+(?:\s+[^\s&]+)?)"
)


def redact_git_output(value: str) -> str:
    """Remove credenciais de URLs e parâmetros antes de expor saída Git."""
    value = _REDACT_USERINFO.sub(r"\1***@", value)

    def replace(match: re.Match[str]) -> str:
        if match.group("authorization"):
            return f"{match.group('authorization')}{match.group('separator')}***"
        return f"{match.group('key')}=***"

    return _REDACT_SECRET.sub(replace, value)


@dataclass(slots=True)
class GitOperation:
    """Snapshot serializável do ciclo de vida de uma operação Git."""

    operation_id: str
    workspace_id: str
    operation: str
    state: str = "queued"
    phase: str = "queued"
    progress: int = 0
    output: str = ""
    error_code: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    sequence: int = field(default=0, repr=False)

    def snapshot(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "workspace_id": self.workspace_id,
            "operation": self.operation,
            "state": self.state,
            "phase": self.phase,
            "progress": self.progress,
            "output": redact_git_output(self.output),
            "error_code": self.error_code,
            "error": redact_git_output(self.error or "") or None,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class GitOperationError(RuntimeError):
    """Erro tipado retornado quando uma operação Git não pode ser concluída."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def status_snapshot(repo: git.Repo) -> dict[str, object]:
    """Retorna o estado de trabalho do repositório sem tocar no event loop."""
    try:
        branch = repo.active_branch.name
    except TypeError:
        branch = (
            str(repo.head.commit.hexsha[:7])
            if not repo.head.is_detached
            else "HEAD detached"
        )

    untracked = repo.untracked_files
    modified = [item.a_path for item in repo.index.diff(None)]
    if repo.head.is_valid():
        staged = [item.a_path for item in repo.index.diff("HEAD")]
    else:
        staged = [path for path, _stage in repo.index.entries]

    ahead = behind = 0
    try:
        tracking = repo.active_branch.tracking_branch()
        if tracking:
            ahead = len(list(repo.iter_commits(f"{tracking.name}..HEAD")))
            behind = len(list(repo.iter_commits(f"HEAD..{tracking.name}")))
    except Exception:
        pass

    return {
        "status": "ok",
        "branch": branch,
        "clean": not untracked and not modified and not staged,
        "untracked": list(untracked),
        "modified": modified,
        "staged": staged,
        "ahead": ahead,
        "behind": behind,
    }


def log_snapshot(
    repo: git.Repo, n: int = 10, branch: str | None = None
) -> dict[str, object]:
    """Retorna histórico de commits para consumo REST e pelas tools."""
    try:
        ref = branch or repo.active_branch.name
    except TypeError:
        ref = "HEAD"
    try:
        commits = list(repo.iter_commits(ref, max_count=n))
    except git.GitCommandError:
        return {"status": "ok", "commits": [], "branch": ref}
    return {
        "status": "ok",
        "branch": ref,
        "commits": [
            {
                "hash": commit.hexsha[:7],
                "author": str(commit.author),
                "date": commit.authored_datetime.isoformat(),
                "message": commit.message.strip().splitlines()[0],
            }
            for commit in commits
        ],
    }


def diff_snapshot(repo: git.Repo, ref: str | None = None) -> dict[str, object]:
    """Retorna o diff atual, opcionalmente comparado a uma referência."""
    try:
        diff_text = repo.git.diff(ref) if ref else repo.git.diff()
        return {"status": "ok", "diff": diff_text}
    except git.GitCommandError as exc:
        return {"status": "error", "message": redact_git_output(str(exc))}


class GitService:
    """Serializa Git por repositório e executa chamadas fora do event loop."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._operations: dict[str, GitOperation] = {}
        self._guard = asyncio.Lock()
        self._sequence = 0

    @staticmethod
    def repository_key(repo: git.Repo) -> str:
        """Retorna uma chave canônica compartilhada por todas as worktrees."""
        common_dir = Path(repo.common_dir).resolve()
        value = os.path.normpath(str(common_dir))
        return value.casefold() if os.name == "nt" else value

    async def _lock_for(self, repo: git.Repo) -> asyncio.Lock:
        key = self.repository_key(repo)
        async with self._guard:
            return self._locks.setdefault(key, asyncio.Lock())

    async def latest(self, workspace_id: str) -> dict[str, object] | None:
        async with self._guard:
            operations = [
                operation
                for operation in self._operations.values()
                if operation.workspace_id == workspace_id
            ]
        if not operations:
            return None
        return max(operations, key=lambda operation: operation.sequence).snapshot()

    async def history(
        self, workspace_id: str, *, limit: int = 50
    ) -> list[dict[str, object]]:
        """Retorna operações recentes para reconexão e diagnóstico."""
        bounded_limit = max(1, min(limit, 200))
        async with self._guard:
            operations = [
                operation
                for operation in self._operations.values()
                if operation.workspace_id == workspace_id
            ]
        return [
            operation.snapshot()
            for operation in sorted(
                operations, key=lambda item: item.sequence, reverse=True
            )[:bounded_limit]
        ]

    async def execute(
        self,
        workspace_id: str,
        repo: git.Repo,
        operation_name: str,
        callback: Callable[[], _T],
        *,
        timeout_seconds: float | None = None,
        lock_timeout_seconds: float | None = None,
        command_timeout_seconds: float | None = None,
    ) -> tuple[GitOperation, _T | None]:
        """Enfileira uma operação e executa o callback em uma thread."""
        if timeout_seconds is not None:
            lock_timeout = command_timeout = timeout_seconds
        else:
            from backend.settings import get_settings

            settings = get_settings()
            lock_timeout = (
                lock_timeout_seconds
                if lock_timeout_seconds is not None
                else settings.git_lock_timeout
            )
            command_timeout = (
                command_timeout_seconds
                if command_timeout_seconds is not None
                else settings.git_command_timeout
            )
        async with self._guard:
            self._sequence += 1
            operation = GitOperation(
                operation_id=f"git-{time.time_ns()}",
                workspace_id=workspace_id,
                operation=operation_name,
                sequence=self._sequence,
            )
            self._operations[operation.operation_id] = operation
            workspace_operations = sorted(
                (
                    previous
                    for previous in self._operations.values()
                    if previous.workspace_id == workspace_id
                ),
                key=lambda item: item.sequence,
                reverse=True,
            )
            for stale in workspace_operations[50:]:
                self._operations.pop(stale.operation_id, None)
        lock = await self._lock_for(repo)
        try:
            operation.phase = "waiting_for_repository"
            await asyncio.wait_for(lock.acquire(), timeout=lock_timeout)
        except TimeoutError as exc:
            operation.state = "failed"
            operation.phase = "terminal"
            operation.error_code = "git_lock_timeout"
            operation.error = "Tempo limite aguardando outra operação Git"
            operation.finished_at = time.time()
            raise GitOperationError(operation.error_code, operation.error) from exc

        try:
            callback_task = asyncio.create_task(asyncio.to_thread(callback))
            operation.state = "running"
            operation.phase = operation_name
            operation.progress = 10
            result = await asyncio.wait_for(
                asyncio.shield(callback_task), timeout=command_timeout
            )
            operation.state = "succeeded"
            operation.phase = "terminal"
            operation.progress = 100
            operation.finished_at = time.time()
            return operation, result
        except TimeoutError as exc:
            try:
                await callback_task
            except Exception as callback_error:
                operation.output = redact_git_output(str(callback_error))
            operation.state = "failed"
            operation.phase = "terminal"
            operation.error_code = "git_command_timeout"
            operation.error = "Tempo limite executando a operação Git"
            operation.finished_at = time.time()
            raise GitOperationError(operation.error_code, operation.error) from exc
        except asyncio.CancelledError:
            while not callback_task.done():
                try:
                    await asyncio.shield(callback_task)
                except asyncio.CancelledError:
                    continue
            try:
                callback_task.result()
            except Exception as callback_error:
                operation.output = redact_git_output(str(callback_error))
            operation.state = "failed"
            operation.phase = "terminal"
            operation.error_code = "git_operation_cancelled"
            operation.error = "Operação Git cancelada"
            operation.finished_at = time.time()
            raise
        except git.GitCommandError as exc:
            operation.state = "failed"
            operation.phase = "terminal"
            operation.error_code = "git_command_failed"
            operation.error = redact_git_output(str(exc.stderr or exc.stdout or exc))
            operation.output = operation.error
            operation.finished_at = time.time()
            raise GitOperationError(operation.error_code, operation.error) from exc
        except Exception as exc:
            operation.state = "failed"
            operation.phase = "terminal"
            operation.error_code = "git_operation_failed"
            operation.error = redact_git_output(str(exc)) or "Falha na operação Git"
            operation.output = operation.error
            operation.finished_at = time.time()
            raise
        finally:
            lock.release()

    async def status(self, workspace_id: str, repo: git.Repo) -> dict[str, object]:
        snapshot = await asyncio.to_thread(status_snapshot, repo)
        latest = await self.latest(workspace_id)
        if latest is not None and latest["state"] in {"queued", "running"}:
            snapshot["operation_in_progress"] = latest
        else:
            snapshot["operation_in_progress"] = None
        return snapshot


git_service = GitService()
