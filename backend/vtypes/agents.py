from __future__ import annotations

from pydantic import BaseModel, Field

from backend.vtypes.metrics import UIMetrics

__all__ = ["MemoryEntry", "UIMetrics"]


class MemoryEntry(BaseModel):
    """Par key/content para persistência automática de informações do usuário."""

    key: str = Field(
        description=(
            "Chave única e descritiva da memória. Use snake_case curto. "
            "Exemplos: 'nome', 'idade', 'projeto_principal', 'linguagem_preferida', "
            "'cargo', 'empresa', 'cidade', 'objetivo_atual'."
        )
    )
    content: str = Field(
        description="Conteúdo da memória em linguagem natural, como uma frase completa."
    )
