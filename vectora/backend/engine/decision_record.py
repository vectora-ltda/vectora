"""Validated outcome shared by decision evaluation, authorization and audit."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.vtypes import JsonObject


class DecisionRecord(BaseModel):
    """A model recommendation never replaces the executor's policy check."""

    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, hide_input_in_errors=True
    )

    decision: Literal["allow", "deny", "requires_human", "unavailable"]
    reason: str
    run_id: str | None = None
    thread_id: str | None = None
    provider: str | None = None
    model: str | None = None
    risk_score: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    policy_version: str | None = None
    decision_id: str | None = None
    response_redacted: JsonObject | None = None
