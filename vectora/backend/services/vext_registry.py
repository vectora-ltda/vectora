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

    def __init__(self, path: str | Path | None = None) -> None:
        self._keys: dict[tuple[str, str], PublisherKey] = {}
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            self._load()

    def _load(self) -> None:
        """Load trust records without silently accepting malformed state."""
        if self.path is None or not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            records = raw.get("publishers", [])
            if not isinstance(records, list):
                raise ValueError("trust store inválido")
            for item in records:
                if not isinstance(item, dict):
                    raise ValueError("trust store inválido")
                publisher = item["publisher"]
                encoded = item["public_key"]
                revoked = item.get("revoked", False)
                if (
                    not isinstance(publisher, str)
                    or not isinstance(encoded, str)
                    or not isinstance(revoked, bool)
                ):
                    raise ValueError("trust store inválido")
                key = VerifyKey(base64.b64decode(encoded, validate=True))
                record = PublisherKey(publisher, self.fingerprint(key), key, revoked)
                self._keys[(publisher, record.fingerprint)] = record
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("não foi possível carregar o trust store VEXT") from exc

    def _save(self) -> None:
        """Atomically persist trust records while retaining revocation history."""
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "publishers": [
                {
                    "publisher": record.publisher,
                    "fingerprint": record.fingerprint,
                    "public_key": base64.b64encode(bytes(record.key)).decode("ascii"),
                    "revoked": record.revoked,
                }
                for record in sorted(
                    self._keys.values(),
                    key=lambda value: (value.publisher, value.fingerprint),
                )
            ],
        }
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path)

    @staticmethod
    def fingerprint(key: VerifyKey) -> str:
        """Return the stable SHA-256 fingerprint for a public key."""
        return hashlib.sha256(bytes(key)).hexdigest()

    def add(self, publisher: str, key: VerifyKey) -> PublisherKey:
        """Trust a publisher key until it is explicitly revoked."""
        record = PublisherKey(publisher, self.fingerprint(key), key)
        self._keys[(publisher, record.fingerprint)] = record
        self._save()
        return record

    def revoke(self, publisher: str, fingerprint: str) -> None:
        """Revoke a key without deleting its audit record."""
        current = self._keys.get((publisher, fingerprint))
        if current is not None:
            self._keys[(publisher, fingerprint)] = PublisherKey(
                current.publisher, current.fingerprint, current.key, True
            )
            self._save()

    def records(self, publisher: str | None = None) -> tuple[PublisherKey, ...]:
        """Return immutable records for audit and key-rotation tooling."""
        values = (
            self._keys.values()
            if publisher is None
            else (
                record
                for record in self._keys.values()
                if record.publisher == publisher
            )
        )
        return tuple(
            sorted(values, key=lambda value: (value.publisher, value.fingerprint))
        )

    def rotate(self, publisher: str, key: VerifyKey) -> PublisherKey:
        """Add a replacement key while retaining older keys for rollback verification."""
        return self.add(publisher, key)

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
