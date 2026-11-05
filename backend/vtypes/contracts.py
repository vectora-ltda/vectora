"""Shared structural types used at native engine/API boundaries.

These aliases keep JSON payloads, tool approval options and usage counters
consistent without coupling the native engine to Pydantic serialization.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class ToolOption(TypedDict):
    """A single user-selectable option in a structured tool approval."""

    label: str
    value: str


class UsageSnapshot(TypedDict, total=False):
    """Provider usage counters emitted during one streamed turn."""

    input_tokens: int
    output_tokens: int
    total_tokens: int


class ChangedFile(TypedDict, total=False):
    """Minimal file change envelope used by the review workbench."""

    path: str
    additions: int
    deletions: int
    patch: str


class ApprovalMetadata(TypedDict, total=False):
    """Redacted metadata persisted for a pending approval."""

    operation: str
    provider: str
    model: str
    estimate_version: str
    billable_unit: str
    currency: str
    estimated_units: int
    idempotency_key: str
    balance_after_reservation: int
    source: NotRequired[str]
