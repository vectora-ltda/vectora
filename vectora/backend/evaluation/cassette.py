"""Cassettes de avaliação redigidas e determinísticas para o Evaluation Lab."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.persistence.native.agent_run import redact_sensitive_data


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class CassetteInteraction(BaseModel):
    """Registro individual de uma interação do modelo."""

    interaction_id: str
    messages: list[dict[str, Any]]
    response_text: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
    systemone_decision: str | None = None


class Cassette(BaseModel):
    """Cassete de replay contendo interações redigidas e auditáveis."""

    cassette_id: str
    version: int = 1
    created_at: str = Field(default_factory=_now_iso)
    model: str = ""
    description: str = ""
    interactions: list[CassetteInteraction] = Field(default_factory=list)

    def save(self, target_path: Path | str) -> None:
        """Salva a cassete no disco com redação estrita de dados sensíveis."""
        path = Path(target_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw_dict = self.model_dump()
        for inter in raw_dict.get("interactions", []):
            saved_usage = inter.get("usage")
            inter["messages"] = redact_sensitive_data(inter.get("messages", []))
            inter["response_text"] = redact_sensitive_data(
                inter.get("response_text", "")
            )
            inter["tool_calls"] = redact_sensitive_data(inter.get("tool_calls", []))
            inter["usage"] = saved_usage
        raw_dict["description"] = redact_sensitive_data(raw_dict.get("description", ""))
        path.write_text(
            json.dumps(raw_dict, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path | str) -> Cassette:
        """Carrega e valida uma cassete do disco."""
        p = Path(path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        return cls.model_validate(raw)
