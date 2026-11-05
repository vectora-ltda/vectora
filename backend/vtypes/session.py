from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SessionMetadata(BaseModel):
    """Metadados da sessão para rastreamento de contexto (Pydantic)."""

    thread_id: str = Field(
        description="Identificador único da sessão (string de 6 dígitos com padding)."
    )
    user_type: str = Field(description="Classificação do usuário (default ou custom).")
    created_at: str = Field(description="Timestamp ISO 8601 de criação.")
    llm_provider: str = Field(
        description="Provedor do LLM ativo (google-genai, openai, etc.)."
    )
    llm_model: str = Field(description="Nome do modelo de LLM ativo.")
    workspace_id: str = Field(description="ID do workspace ativo.")
    manifest_version: int = Field(
        description="Versão do manifest carregada no contexto desta sessão."
    )

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)
