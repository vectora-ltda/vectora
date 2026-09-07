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
_REDACT_USERINFO = re.compile(r"(://[^/@\s:]+):[^/@\s]+@")
_REDACT_SECRET = re.compile(
    r"(?i)(?P<key>token|secret|password)=(?P<value>[^\s&]+)"
    r"|(?P<authorization>authorization)(?P<separator>\s*[=:]\s*)"
    r"(?:(?P<scheme>Bearer)\s+)?(?P<auth_value>[^\s&]+)"
)


def redact_git_output(value: str) -> str:
    """Remove credenciais de URLs e parâmetros antes de expor saída Git."""
    value = _REDACT_USERINFO.sub(r"\1:***@", value)

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


class GitService:
    """Serializa Git por repositório e executa chamadas fora do event loop."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._operations: dict[str, GitOperation] = {}
        self._guard = asyncio.Lock()

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
        return max(operations, key=lambda operation: operation.created_at).snapshot()

    async def execute(
        self,
        workspace_id: str,
        repo: git.Repo,
        operation_name: str,
        callback: Callable[[], _T],
        *,
        timeout_seconds: float = 120.0,
    ) -> tuple[GitOperation, _T | None]:
        """Enfileira uma operação e executa o callback em uma thread."""
        operation = GitOperation(
            operation_id=f"git-{time.time_ns()}",
            workspace_id=workspace_id,
            operation=operation_name,
        )
        async with self._guard:
            self._operations = {
                operation_id: previous
                for operation_id, previous in self._operations.items()
                if previous.workspace_id != workspace_id
            }
            self._operations[operation.operation_id] = operation
        lock = await self._lock_for(repo)
        try:
            operation.phase = "waiting_for_repository"
            await asyncio.wait_for(lock.acquire(), timeout=timeout_seconds)
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
                asyncio.shield(callback_task), timeout=timeout_seconds
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
        from backend.tools.git import _git_status_impl

        return await asyncio.to_thread(_git_status_impl, repo)


git_service = GitService()
