"""Contrato comum de trust para MCP e skills.

Curadoria do catálogo é separada de assinatura criptográfica. Entradas sem
metadados são legadas/unsigned e não podem ser executadas sem confirmação e
um backend de sandbox disponível.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

TrustState = Literal[
    "vectora_verified",
    "publisher_signed",
    "community_listed",
    "unsigned",
    "invalid",
    "verification_unavailable",
]


class TrustRecord(BaseModel):
    schema_version: int = 1
    source: str = ""
    publisher: str = ""
    key_fingerprint: str = ""
    algorithm: str = ""
    digest: str = ""
    signature_status: Literal["valid", "invalid", "missing", "unavailable"] = "missing"
    state: TrustState = "unsigned"
    verified_at: str | None = None
    reason: str = "legacy_entry"


def unsigned_record(source: str = "") -> TrustRecord:
    return TrustRecord(source=source, state="unsigned", reason="confirmation_required")


def curated_record(source: str, material: str) -> TrustRecord:
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return TrustRecord(
        source=source,
        digest=digest,
        signature_status="missing",
        state="vectora_verified",
        verified_at=datetime.now(UTC).isoformat(),
        reason="catalog_curated",
    )


def content_record(source: str, material: str) -> TrustRecord:
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return TrustRecord(source=source, digest=digest)


def requires_confirmation(record: TrustRecord) -> bool:
    return record.state in {"unsigned", "verification_unavailable"}


def validate_record(record: TrustRecord, *, confirmed: bool) -> None:
    if record.state == "invalid":
        raise PermissionError("conteúdo de extensão inválido")
    if requires_confirmation(record) and not confirmed:
        raise PermissionError(
            "confirmação explícita necessária para conteúdo não verificado"
        )


__all__ = [
    "TrustRecord",
    "TrustState",
    "content_record",
    "curated_record",
    "requires_confirmation",
    "unsigned_record",
    "validate_record",
]
