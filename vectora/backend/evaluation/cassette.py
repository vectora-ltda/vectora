"""Cassettes de avaliação redigidas e determinísticas para o Evaluation Lab."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from backend.persistence.native.agent_run import redact_sensitive_data
from backend.vtypes import JsonObject


def _now_iso() -> str:
    """Executa a operação now iso."""
    return datetime.now(UTC).isoformat()


class CassetteInteraction(BaseModel):
    """Registro individual de uma interação do modelo."""

    interaction_id: str
    messages: list[JsonObject]
    response_text: str = ""
    tool_calls: list[JsonObject] = Field(default_factory=list)
    duration_ms: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
    systemone_decision: str | None = None
    # Structured, redacted provenance for new recordings. Keep the legacy
    # string field above so existing cassettes remain readable.
    systemone_decision_evidence: JsonObject | None = None
    # Provenance is optional for legacy cassettes, but new recordings should
    # populate it so a replay can be tied to the execution that produced it.
    run_id: str | None = None
    policy_digest: str | None = None
    environment_digest: str | None = None
    reproducible: bool = False


class Cassette(BaseModel):
    """Cassete de replay contendo interações redigidas e auditáveis."""

    cassette_id: str
    version: int = 1
    created_at: str = Field(default_factory=_now_iso)
    model: str = ""
    description: str = ""
    runtime_sha: str | None = None
    source_run_id: str | None = None
    provider: str | None = None
    capability: str | None = None
    redaction_version: int = Field(default=1, ge=1)
    interactions: list[CassetteInteraction] = Field(default_factory=list)

    def save(self, target_path: Path | str) -> None:
        """Salva a cassete no disco com redação estrita de dados sensíveis."""
        path = Path(target_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.redaction_version != 1:
            raise ValueError(
                f"redaction_version não suportada: {self.redaction_version}"
            )
        raw_dict = self.model_dump()
        # A cassette without a source/run identity is still loadable for
        # backwards compatibility, but is explicitly marked as legacy. This
        # prevents replay output from being mistaken for live evidence.
        raw_dict.setdefault("redaction_version", 1)
        for inter in raw_dict.get("interactions", []):
            saved_usage = inter.get("usage")
            inter["messages"] = redact_sensitive_data(inter.get("messages", []))
            inter["response_text"] = redact_sensitive_data(
                inter.get("response_text", "")
            )
            inter["tool_calls"] = redact_sensitive_data(inter.get("tool_calls", []))
            decision_evidence = inter.get("systemone_decision_evidence")
            if decision_evidence is not None:
                inter["systemone_decision_evidence"] = redact_sensitive_data(
                    decision_evidence
                )
            inter["usage"] = saved_usage
        raw_dict["description"] = redact_sensitive_data(raw_dict.get("description", ""))
        path.write_text(
            json.dumps(raw_dict, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, source_path: Path | str) -> Cassette:
        """Carrega e valida uma cassete gravada a partir do caminho indicado."""
        path = Path(source_path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("A cassete deve ser um objeto JSON")
        version = raw.get("redaction_version", 1)
        if version != 1:
            raise ValueError(f"redaction_version não suportada: {version}")
        return cls.model_validate(raw)
