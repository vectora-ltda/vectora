"""Validation for the opaque installation identifier used by thread activity."""

from __future__ import annotations

import re

_DEVICE_ID_RE = re.compile(
    r"^vdev_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def validate_device_id(value: str | None) -> str | None:
    """Return a valid opaque device id, or ``None`` for untrusted input."""
    if not isinstance(value, str) or not _DEVICE_ID_RE.fullmatch(value):
        return None
    return value
