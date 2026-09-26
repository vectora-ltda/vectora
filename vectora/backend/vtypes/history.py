"""Validated contracts exchanged by the native history service and API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HistoryDiffHunk(BaseModel):
    """Serializable diff hunk stored with an edited file."""

    header: str = ""
    lines: list[str] = Field(default_factory=list)


class HistoryEditedFile(BaseModel):
    """Validated file-change payload attached to one assistant message."""

    path: str
    status: str = "M"
    additions: int = 0
    deletions: int = 0
    hunks: list[HistoryDiffHunk] = Field(default_factory=list)


class ThreadHistoryEntry(BaseModel):
    """Named, validated history entry returned by the native history service."""

    role: Literal["human", "assistant"]
    text: str
    checkpoint_id: str
    attachments: list[dict[str, object]] = Field(default_factory=list)
    edited_files: list[HistoryEditedFile] = Field(default_factory=list)
