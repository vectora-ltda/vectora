"""Contrato validado para jobs de revisão recebidos pelo gateway."""

from __future__ import annotations

from pydantic import BaseModel, Field


# Leave room for JSON field names and UTF-8 encoding around the diff.
MAX_REVIEW_JOB_BYTES = 6_000_000


class ReviewJobRequest(BaseModel):
    """Payload mínimo e estável entre Action, gateway e worker self-hosted."""

    job_id: str = Field(min_length=1, max_length=160)
    diff: str = Field(min_length=1, max_length=5_000_000)
    metadata: dict[str, str] = Field(default_factory=dict)
    callback_secret: str = Field(min_length=1, max_length=512)
    delivery_id: str | None = Field(default=None, max_length=200)
    head_sha: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{40}$")
    base_sha: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{40}$")
