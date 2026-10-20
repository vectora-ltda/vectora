"""Contratos e modelos de Manifesto de Contexto e Projeções Versionadas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

ContextDomain = Literal[
    "system",
    "short_term",
    "session",
    "project",
    "vector_rag",
    "structural_graph",
]


@dataclass
class ContextBlock:
    """Bloco individual carregado no contexto do modelo com proveniência explícita."""

    domain: ContextDomain
    title: str
    token_count: int
    reason: str
    provenance: str | None = None
    untrusted_content: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "title": self.title,
            "token_count": self.token_count,
            "reason": self.reason,
            "provenance": self.provenance,
            "untrusted_content": self.untrusted_content,
        }


@dataclass
class ContextProjection:
    """Projeção compactada e versionada do transcript operacional."""

    start_seq: int
    end_seq: int
    source_transcript_hash: str
    tokens_before: int
    tokens_after: int
    model: str = ""
    policy_version: str = "v1"
    preserved_decisions: list[str] = field(default_factory=list)
    preserved_errors: list[str] = field(default_factory=list)
    preserved_approvals: list[str] = field(default_factory=list)
    excluded_items_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def is_valid_for(self, current_transcript_hash: str) -> bool:
        """Verifica se a projeção é compatível com o hash atual do transcript."""
        return self.source_transcript_hash == current_transcript_hash

    def to_evidence_entry(self) -> dict[str, Any]:
        return {
            "kind": "context_projection",
            "start_seq": self.start_seq,
            "end_seq": self.end_seq,
            "source_transcript_hash": self.source_transcript_hash,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "model": self.model,
            "policy_version": self.policy_version,
            "preserved_decisions_count": len(self.preserved_decisions),
            "preserved_errors_count": len(self.preserved_errors),
            "preserved_approvals_count": len(self.preserved_approvals),
            "excluded_items_count": self.excluded_items_count,
            "created_at": self.created_at,
        }


@dataclass
class ContextManifest:
    """Manifesto completo de contexto carregado para a invocação do modelo."""

    blocks: list[ContextBlock] = field(default_factory=list)
    total_tokens: int = 0
    untrusted_content: bool = False
    projection: ContextProjection | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_evidence_entry(self) -> dict[str, Any]:
        return {
            "kind": "context_manifest",
            "total_tokens": self.total_tokens,
            "untrusted_content": self.untrusted_content,
            "blocks_count": len(self.blocks),
            "blocks": [b.to_dict() for b in self.blocks],
            "has_projection": self.projection is not None,
            "created_at": self.created_at,
        }
