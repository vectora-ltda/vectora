"""Contratos estruturais compartilhados pelo Browser Workbench e CDP tools."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

NetworkProfileName = Literal["offline", "slow-3g", "fast-3g"]


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
    status: int | None
