"""Interface abstrata / protocolo para provedores de Decision Models."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.llm.systemone.types import (
    ProviderCapabilities,
    SystemOneRequest,
    SystemOneResponse,
)


@runtime_checkable
class SystemOneProvider(Protocol):
    """Protocolo que todo adaptador de Decision Model (JEV / Clef / Llama.cpp) deve implementar."""

    @property
    def provider_id(self) -> str:
        """Identificador canônico do provedor (ex: 'clef-local', 'clef-api', 'jev-api')."""
        ...

    async def decide(self, request: SystemOneRequest) -> SystemOneResponse:
        """Envia uma requisição de decisão estruturada e retorna respostas tipadas."""
        ...

    async def capabilities(self) -> ProviderCapabilities:
        """Retorna as capacidades suportadas por este provedor/modelo."""
        ...

    async def health(self) -> bool:
        """Verifica se o endpoint do provedor está acessível e operacional."""
        ...
