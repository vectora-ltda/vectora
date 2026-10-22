from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Document(BaseModel):
    """Estrutura de documento recuperado do RAG (Pydantic)."""

    page_content: str = Field(description="Conteúdo do documento.")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Metadados associados."
    )
    relevance_score: float | None = Field(
        default=None, description="Score de relevância do RAG."
    )

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


VALID_ARTIFACT_TYPES = frozenset(
    {
        "plan",
        "spec",
        "task_list",
        "overview",
        "guide",
        "architecture",
        "implementation",
        # Remember (learning loop) — skill/fato aprovado vira artefato
        # consultável na aba Plan em vez de sumir depois do diff de aprovação.
        # "remember_proposal" é a proposta automática (ainda não aprovada)
        # do gatilho a cada N turnos (backend/services/remember_trigger.py).
        "skill_learned",
        "fact_learned",
        "remember_proposal",
        # Imagem/áudio gerados por `backend/tools/media.py`. Não passam por
        # `create_artifact` (que grava markdown) — são binários em
        # `artifacts/{session}/media/` e a listagem os inclui à parte.
        "media",
    }
)

# Tipo default pra artifacts salvos antes do campo existir (sidecar
# `.artifact_type` ausente) — nunca quebra a listagem, só perde o ícone/cor
# específico do tipo.
DEFAULT_ARTIFACT_TYPE = "other"


class ArtifactMetadata(BaseModel):
    """Metadado de um artifact persistido em disco (Pydantic)."""

    title: str = Field(description="Título do artifact.")
    path: str = Field(description="Caminho absoluto do arquivo no disco.")
    session_id: str = Field(description="ID da sessão que gerou o artifact.")
    created_at: str = Field(description="Timestamp ISO 8601 de criação.")
    artifact_type: str = Field(
        default=DEFAULT_ARTIFACT_TYPE,
        description="Tipo do artifact (plan/spec/task_list/...); "
        "'other' para artifacts legados sem o sidecar de tipo.",
    )
    content_preview: str | None = Field(
        default=None, description="Preview dos primeiros 200 caracteres."
    )
    byte_size: int = Field(default=0, ge=0, description="Tamanho do payload em bytes.")
    sha256: str = Field(default="", description="Checksum SHA-256 do payload.")
    retention_class: str = Field(
        default="run", description="Política de retenção aplicada ao artifact."
    )

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)
