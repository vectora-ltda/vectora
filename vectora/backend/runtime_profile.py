"""Resolve the isolated runtime profile and home directory."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

_PROFILE_RE = re.compile(r"[^A-Za-z0-9_-]")


def sanitize_runtime_profile(profile: str | None, default: str = "stable") -> str:
    """Return the filesystem-safe profile name shared by every bootstrap."""
    candidate = _PROFILE_RE.sub("-", (profile or "").strip())
    if candidate:
        return candidate
    fallback = _PROFILE_RE.sub("-", default.strip())
    return fallback or "stable"


def normalize_runtime_home(value: str, home_directory: Path | None = None) -> Path:
    """Expand a user-relative override and return an absolute runtime home."""
    base = home_directory or Path.home()
    expanded = value
    if value == "~":
        expanded = str(base)
    elif value.startswith(("~/", "~\\")):
        expanded = str(base / value[2:])
    path = Path(expanded)
    return path if path.is_absolute() else (base / path).resolve()


def runtime_home_for_profile(
    profile: str | None,
    home_directory: Path | None = None,
) -> Path:
    """Return the default home for a sanitized runtime profile."""
    base = home_directory or Path.home()
    safe_profile = sanitize_runtime_profile(profile)
    directory_name = {
        "stable": ".vectora",
        "dev": ".vectora-dev",
    }.get(safe_profile, f".vectora-{safe_profile}")
    return base / directory_name


def resolve_runtime_home(
    env: Mapping[str, str] | None = None,
    *,
    default_profile: str = "stable",
    home_directory: Path | None = None,
) -> Path:
    """Resolve ``VECTORA_HOME`` or derive it from ``VECTORA_RUNTIME_PROFILE``."""
    environ = os.environ if env is None else env
    configured = environ.get("VECTORA_HOME")
    if configured:
        return normalize_runtime_home(configured, home_directory)
    return runtime_home_for_profile(
        environ.get("VECTORA_RUNTIME_PROFILE", default_profile), home_directory
    )
