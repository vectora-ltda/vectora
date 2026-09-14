"""Publisher trust and signature policy for VEXT artifacts."""

from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from nacl.signing import VerifyKey

from backend.services.vext_artifact import verify_vext


@dataclass(frozen=True, slots=True)
class PublisherKey:
    """Trusted public key for one publisher identity."""

    publisher: str
    fingerprint: str
    key: VerifyKey
    revoked: bool = False


class VextTrustStore:
    """Keep an explicit allowlist of publisher keys and revocations."""

    def __init__(self) -> None:
        self._keys: dict[tuple[str, str], PublisherKey] = {}

    @staticmethod
    def fingerprint(key: VerifyKey) -> str:
        """Return the stable SHA-256 fingerprint for a public key."""
        return hashlib.sha256(bytes(key)).hexdigest()

    def add(self, publisher: str, key: VerifyKey) -> PublisherKey:
        """Trust a publisher key until it is explicitly revoked."""
        record = PublisherKey(publisher, self.fingerprint(key), key)
        self._keys[(publisher, record.fingerprint)] = record
        return record

    def revoke(self, publisher: str, fingerprint: str) -> None:
        """Revoke a key without deleting its audit record."""
        current = self._keys.get((publisher, fingerprint))
        if current is not None:
            self._keys[(publisher, fingerprint)] = PublisherKey(
                current.publisher, current.fingerprint, current.key, True
            )

    def verify(self, artifact: str | Path) -> PublisherKey:
        """Verify a signed artifact against the trusted publisher key."""
        result = verify_vext(artifact)
        try:
            with zipfile.ZipFile(artifact) as archive:
                signature = json.loads(archive.read("signature/manifest.json"))
                public_key = VerifyKey(
                    base64.b64decode(signature["public_key"], validate=True)
                )
        except (
            KeyError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            zipfile.BadZipFile,
        ) as exc:
            raise PermissionError("artefato VEXT sem assinatura válida") from exc
        fingerprint = self.fingerprint(public_key)
        record = self._keys.get((result.manifest.publisher, fingerprint))
        if record is None or record.revoked:
            raise PermissionError("publisher VEXT não confiável ou revogado")
        verify_vext(artifact, verify_key=record.key)
        return record


__all__ = ["PublisherKey", "VextTrustStore"]
