"""Public Python contracts for Vectora VEXT extensions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class RpcTransport(Protocol):
    """Transport implemented by the Vectora extension host."""

    def request(self, method: str, params: dict[str, object]) -> object:
        """Send one capability-mediated request."""


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    """Minimal manifest view exposed to Python extension code."""

    id: str
    publisher: str
    version: str
    api_version: int
    protocol_version: int = 1


@dataclass(frozen=True, slots=True)
class ExtensionContext:
    """Context passed to extension handlers without host internals."""

    manifest: ExtensionManifest
    transport: RpcTransport

    def request(self, method: str, params: dict[str, object] | None = None) -> object:
        """Request a capability through the host transport."""
        return self.transport.request(method, params or {})


__all__ = ["ExtensionContext", "ExtensionManifest", "RpcTransport"]
