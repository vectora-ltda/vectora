"""Interface abstrata / protocolo para provedores de Decision Models.

Decisão de escopo arquitetural:
- Esta camada em Python consolida a entrega aprovada para o runtime 1.x (#318),
  mantendo contratos, fixtures, registry de patterns e critérios de avaliação
  desacoplados e reutilizáveis.
- O runtime nativo em Rust (#303) permanece condicionado à migração da #266 (2.0),
  compartilhando os mesmos contratos JSON e especificações de patterns.
"""

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
