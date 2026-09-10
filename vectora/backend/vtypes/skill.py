"""Skill — capacidade reutilizável carregada pelo Deep Agent.

Cada skill é uma pasta com ``SKILL.md`` no root (frontmatter YAML com ``name``
e ``description`` + corpo markdown). O agente lê o frontmatter sob demanda
(progressive disclosure) e usa a skill quando relevante.

Persistência: ``~/.vectora/skills/<user_id>/`` (uma pasta por skill instalada)
+ ``index.json`` listando o que está instalado. Gerenciado por
``src/services/skills.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.services.extension_trust import TrustRecord


class Skill(BaseModel):
    """Skill instalada para um usuário."""

    id: str = Field(description="ID determinístico (slug do nome).")
    name: str = Field(description="Nome declarado no frontmatter do SKILL.md.")
    description: str = Field(description="Descrição declarada no frontmatter.")
    source: str = Field(description="URL git ou path original de onde a skill veio.")
    path: str = Field(description="Path absoluto onde a skill está extraída.")
    installed_at: str = Field(description="Timestamp ISO 8601 da instalação.")
    installed_by: str = Field(description="user_id que instalou.")
    trust: TrustRecord = Field(default_factory=TrustRecord)
    trust_confirmed: bool = False
    revision: str | None = None
