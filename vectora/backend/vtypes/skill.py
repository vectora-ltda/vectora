"""Skill — capacidade reutilizável carregada pelo Deep Agent.

Cada skill é uma pasta com ``SKILL.md`` no root (frontmatter YAML com ``name``
e ``description`` + corpo markdown). O agente lê o frontmatter sob demanda
(progressive disclosure) e usa a skill quando relevante.

Persistência: ``~/.vectora/skills/<user_id>/`` (uma pasta por skill instalada)
+ ``index.json`` listando o que está instalado. Gerenciado por
``src/services/skills.py``.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


type SkillCatalogSource = Literal["remote", "enterprise", "local"]


class SkillCatalogEntry(BaseModel):
    """Validated skill metadata crossing catalog aggregation boundaries."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    source: str = Field(min_length=1)
    package_name: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    catalog_source: SkillCatalogSource
    vectora_verified: bool | None = None
    verified: bool | None = None

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        source = value.strip()
        if not source:
            raise ValueError("skill catalog source must not be empty")
        return source

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return parsed if isinstance(parsed, list) else [value]


class SkillCatalogResponse(BaseModel):
    """Typed response returned by the aggregated skill catalog endpoint."""

    entries: list[SkillCatalogEntry]
    total: int = Field(ge=0)
