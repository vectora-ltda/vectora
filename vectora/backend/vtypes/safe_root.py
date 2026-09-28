"""SafeRoot — pasta confiável global definida pelo admin.

Diferente do `Workspace` (que é um projeto isolado de um usuário), um
SafeRoot é uma raiz **compartilhada** que limita onde usuários comuns
podem navegar ao criar workspaces. Admin gerencia a lista; usuários
herdam os limites.

Persistência: ``~/.vectora/safe_roots.json`` via ``SafeRootRegistry``
(``src/services/safe_roots.py``).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SafeRoot(BaseModel):
    """Raiz confiável onde usuários podem criar/navegar workspaces."""

    id: str = Field(description="ID determinístico (sha256[:8] do path absoluto).")
    path: str = Field(description="Caminho absoluto da raiz.")
    label: str = Field(description="Nome amigável exibido na UI.")
    created_at: str = Field(description="Timestamp ISO 8601 de criação.")
    created_by: str = Field(
        description="ID do usuário admin que criou a entrada.",
    )
    builtin: bool = Field(
        default=False,
        description="True para entradas que o Vectora cria por padrão "
        "(ex.: ~/Documents/vectora). Builtins não podem ser removidos.",
    )
    archived_at: str | None = Field(
        default=None,
        description="Timestamp ISO 8601 em que a raiz foi arquivada.",
    )
