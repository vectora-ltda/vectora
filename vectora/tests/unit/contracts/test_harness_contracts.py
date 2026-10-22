"""Contratos canônicos compartilhados pelo runtime de execução."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.contracts.models import (
    ArtifactRef,
    CheckpointRef,
    ContextProjection,
    MemoryRecord,
    ProviderCapability,
)

_HASH = "a" * 64


def test_harness_contracts_validate_refs_and_provenance() -> None:
    artifact = ArtifactRef(
        artifact_id="artifact-1",
        media_type="text/plain",
        byte_size=3,
        sha256=_HASH,
        storage_uri="file:///tmp/artifact-1",
    )
    checkpoint = CheckpointRef(
        checkpoint_id="checkpoint-1",
        run_id="run-1",
        thread_id="thread-1",
        last_sequence=12,
        source_hash=_HASH,
        schema_version="1",
        artifact_refs=[artifact],
    )
    projection = ContextProjection(
        projection_id="projection-1",
        thread_id="thread-1",
        run_id="run-1",
        source_hash=_HASH,
        covered_from_sequence=1,
        covered_to_sequence=12,
        tokens_before=100,
        tokens_after=40,
        preserved_decisions=["decision-1"],
        preserved_approvals=["approval-1"],
    )
    memory = MemoryRecord(
        memory_id="memory-1",
        scope="project",
        subject="runtime",
        content="O transcript é canônico.",
        source_run_id="run-1",
        provenance_hash=_HASH,
    )
    capability = ProviderCapability(
        provider="llama.cpp",
        model="qwen",
        discovered=True,
        ready=True,
        text_generation=True,
        streaming=True,
    )

    assert checkpoint.artifact_refs[0].sha256 == _HASH
    assert projection.covered_to_sequence == 12
    assert memory.scope == "project"
    assert capability.ready is True


def test_harness_contracts_reject_invalid_digest_and_scope() -> None:
    with pytest.raises(ValidationError):
        ArtifactRef(
            artifact_id="artifact-1",
            sha256="invalid",
            storage_uri="file:///tmp/artifact-1",
        )

    with pytest.raises(ValidationError):
        MemoryRecord.model_validate(
            {
                "memory_id": "memory-1",
                "scope": "global",
                "subject": "runtime",
                "content": "invalid",
                "provenance_hash": _HASH,
            }
        )


def test_harness_schema_is_generated_and_contains_all_domains() -> None:
    schema_file = (
        Path(__file__).resolve().parents[3]
        / "backend"
        / "contracts"
        / "harness"
        / "schema.json"
    )
    assert schema_file.is_file()
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    assert {
        "ArtifactRef",
        "CheckpointRef",
        "ContextProjection",
        "MemoryRecord",
        "ProviderCapability",
    }.issubset(schema["definitions"])
