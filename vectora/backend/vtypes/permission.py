"""Shared permission-mode contract for API and native agent contexts."""

from __future__ import annotations

from typing import Literal

type PermissionMode = Literal["ask", "accept_edits", "plan", "auto", "bypass"]

PERMISSION_MODES: tuple[PermissionMode, ...] = (
    "ask",
    "accept_edits",
    "plan",
    "auto",
    "bypass",
)
