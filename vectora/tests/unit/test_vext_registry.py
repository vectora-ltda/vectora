import json
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from backend.services.vext_artifact import build_vext
from backend.services.vext_registry import VextTrustStore


def test_trust_store_verifies_and_revokes_publisher(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text(
        "def handle(method, params): return None\n", encoding="utf-8"
    )
    (source / "vectora-extension.json").write_text(
        json.dumps(
            {
                "id": "signed.test",
                "publisher": "publisher",
                "name": "Signed",
                "version": "1.0.0",
                "api_version": 1,
                "runtime": "python",
                "entrypoint": "main.py",
            }
        ),
        encoding="utf-8",
    )
    key = SigningKey.generate()
    artifact = tmp_path / "signed.vext"
    build_vext(source, artifact, signing_key=key)

    trust = VextTrustStore()
    record = trust.add("publisher", key.verify_key)
    assert trust.verify(artifact) == record
    trust.revoke("publisher", record.fingerprint)
    with pytest.raises(PermissionError, match="revogado"):
        trust.verify(artifact)
