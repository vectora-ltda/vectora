"""Contratos Pydantic compartilhados pelo Git Workbench."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GitContract(BaseModel):
    """Modelo imutável que também oferece leitura compatível com mappings."""

    model_config = ConfigDict(frozen=True)

    def __getitem__(self, key: str) -> Any:
        """Compatibilidade de leitura para callers JSON legados.

        ``Any`` is intentional here: the accessor mirrors Pydantic's dynamic
        field lookup for legacy mapping callers, while typed model attributes
        remain the preferred API for new code.
        """
        return getattr(self, key)

    def get(self, key: str, default: object = None) -> Any:
        """Lê um campo opcional para callers JSON legados.

        ``Any`` is intentional for the same dynamic compatibility boundary as
        :meth:`__getitem__`; typed attributes remain available to new code.
        """
        return getattr(self, key, default)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in type(self).model_fields


class GitCommitSnapshot(GitContract):
    """Resumo de um commit exibido no histórico do workspace."""

    hash: str
    author: str
    date: str
    message: str


class GitStatusSnapshot(GitContract):
    """Estado atual do repositório e de uma operação Git em andamento."""

    status: Literal["ok", "error"]
    branch: str = ""
    clean: bool = True
    untracked: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)
    staged: list[str] = Field(default_factory=list)
    ahead: int = 0
    behind: int = 0
    operation_in_progress: GitOperationSnapshot | None = None


class GitLogSnapshot(GitContract):
    """Página de commits retornada pelo histórico do repositório."""

    status: Literal["ok", "error"]
    branch: str = ""
    commits: list[GitCommitSnapshot] = Field(default_factory=list)
    message: str | None = None


class GitDiffSnapshot(GitContract):
    """Diff textual de uma referência do repositório."""

    status: Literal["ok", "error"]
    diff: str = ""
    message: str | None = None


class GitOperationSnapshot(GitContract):
    """Estado persistido de uma operação Git assíncrona."""

    operation_id: str
    workspace_id: str
    operation: str
    state: Literal["queued", "running", "succeeded", "failed"]
    phase: str
    progress: int = Field(ge=0, le=100)
    output: str = ""
    error_code: str | None = None
    error: str | None = None
    created_at: float
    finished_at: float | None = None
