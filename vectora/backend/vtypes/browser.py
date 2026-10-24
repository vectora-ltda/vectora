"""Contratos estruturais compartilhados pelo Browser Workbench e CDP tools."""

from __future__ import annotations

from typing import Literal, NotRequired

from pydantic import TypeAdapter
from typing_extensions import TypedDict

NetworkProfileName = Literal["offline", "slow-3g", "fast-3g"]
BrowserProfileKind = Literal["research", "authenticated"]


class BrowserTarget(TypedDict):
    """Identidade imutável do alvo Browser compartilhado por uma execução.

    ``view_id`` identifica a WebContentsView criada pelo Electron e
    ``navigation_generation`` invalida referências DOM obtidas antes de uma
    navegação. O perfil autenticado só deve ser usado após aprovação explícita.
    """

    workspace_id: str
    profile_id: str
    profile_kind: BrowserProfileKind
    view_id: str
    navigation_generation: int


class NetworkProfile(TypedDict):
    """Parâmetros aceitos por ``Network.emulateNetworkConditions``."""

    offline: bool
    latency: int
    downloadThroughput: int
    uploadThroughput: int


class BrowserTab(TypedDict):
    """Resumo de uma aba que pode ser serializado para a UI."""

    tab_id: str
    url: str
    active: bool
    title: NotRequired[str]
    navigation_generation: NotRequired[int]


class BrowserConsoleEntry(TypedDict):
    """Linha normalizada do console de uma aba."""

    type: str
    text: str


class BrowserNetworkEntry(TypedDict):
    """Requisição/resposta resumida no buffer de observabilidade."""

    request_id: str
    url: str
    method: str
    resource_type: str
    status: int | Literal["failed"] | None
    error: NotRequired[str | None]


_BROWSER_TAB_ADAPTER = TypeAdapter(BrowserTab)
_BROWSER_TARGET_ADAPTER = TypeAdapter(BrowserTarget)
_BROWSER_CONSOLE_ENTRY_ADAPTER = TypeAdapter(BrowserConsoleEntry)
_BROWSER_NETWORK_ENTRY_ADAPTER = TypeAdapter(BrowserNetworkEntry)


def validate_browser_tab(value: BrowserTab) -> None:
    """Validate a tab payload before exposing it to a caller."""
    _BROWSER_TAB_ADAPTER.validate_python(value)


def validate_browser_target(value: BrowserTarget) -> None:
    """Validate the target identity before it crosses a tool boundary."""
    _BROWSER_TARGET_ADAPTER.validate_python(value)


def validate_browser_console_entry(value: BrowserConsoleEntry) -> None:
    """Validate a console event before storing it in the ring buffer."""
    _BROWSER_CONSOLE_ENTRY_ADAPTER.validate_python(value)


def validate_browser_network_entry(value: BrowserNetworkEntry) -> None:
    """Validate a network event before storing it in the ring buffer."""
    _BROWSER_NETWORK_ENTRY_ADAPTER.validate_python(value)
