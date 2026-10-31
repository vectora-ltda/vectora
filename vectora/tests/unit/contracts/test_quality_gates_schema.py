"""Testes para conformidade do schema de quality gates e perfis de verificação pós-execução."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.contracts.models import (
    QualityGatePolicy,
    QualityGateRunResult,
    QualityGateSpec,
    WorkspaceVerificationProfile,
)


def test_quality_gate_spec_and_profile_instantiation() -> None:
    """Verifica o comportamento de quality gate spec and profile instantiation."""
    gate_pytest = QualityGateSpec(
        id="pytest",
        name="Unit Tests",
        command=["python", "-m", "pytest", "tests/unit/"],
        policy="block",
        file_patterns=["**/*.py"],
        timeout_seconds=120,
    )
    assert gate_pytest.id == "pytest"
    assert gate_pytest.policy == "block"

    profile = WorkspaceVerificationProfile(
        workspace_id="ws-dev-1",
        enabled=True,
        gates=[gate_pytest],
        require_clean_git=True,
        inject_stack_trace_on_failure=True,
    )
    assert profile.workspace_id == "ws-dev-1"
    assert len(profile.gates) == 1
    assert profile.require_clean_git is True


def test_quality_gates_schema_file_matches_models() -> None:
    """Verifica o comportamento de quality gates schema file matches models."""
    schema_file = (
        Path(__file__).resolve().parents[3]
        / "backend"
        / "contracts"
        / "quality_gates"
        / "schema.json"
    )
    assert schema_file.is_file(), "Arquivo schema.json de quality gates não encontrado"

    schema_json = json.loads(schema_file.read_text(encoding="utf-8"))
    assert "definitions" in schema_json
    assert "QualityGateSpec" in schema_json["definitions"]
    assert "WorkspaceVerificationProfile" in schema_json["definitions"]
    assert "QualityGateRunResult" in schema_json["definitions"]


def test_quality_gate_run_result_serialization() -> None:
    """Verifica o comportamento de quality gate run result serialization."""
    result = QualityGateRunResult(
        gate_id="ruff",
        passed=False,
        exit_code=1,
        output="Found 2 errors.",
        duration_ms=450,
        artifacts_collected=["reports/ruff.txt"],
    )
    data = result.model_dump()
    assert data["gate_id"] == "ruff"
    assert data["passed"] is False
    assert data["exit_code"] == 1
    assert data["duration_ms"] == 450
    assert data["artifacts_collected"] == ["reports/ruff.txt"]

    recovered = QualityGateRunResult.model_validate(data)
    assert recovered.gate_id == "ruff"
    assert recovered.passed is False


def test_quality_gate_policy_literals() -> None:
    """Verifica o comportamento de quality gate policy literals."""
    valid_policies: list[QualityGatePolicy] = ["block", "degrade", "inform"]
    for pol in valid_policies:
        gate = QualityGateSpec(
            id=f"gate-{pol}",
            name=f"Gate {pol}",
            command=["echo", pol],
            policy=pol,
        )
        assert gate.policy == pol

    with pytest.raises(ValidationError):
        QualityGateSpec.model_validate(
            {
                "id": "bad-gate",
                "name": "Bad Gate",
                "command": ["echo", "bad"],
                "policy": "invalid_policy",
            }
        )


def test_quality_gates_bundle_all_refs_resolve() -> None:
    """Valida que todas as referências $ref no bundle draft-07 resolvem a partir da raiz."""
    schema_file = (
        Path(__file__).resolve().parents[3]
        / "backend"
        / "contracts"
        / "quality_gates"
        / "schema.json"
    )
    schema_json = json.loads(schema_file.read_text(encoding="utf-8"))

    def _resolve_pointer(root: dict, pointer: str) -> dict:
        """Executa a operação resolve pointer."""
        assert pointer.startswith("#/"), f"Referência externa não suportada: {pointer}"
        parts = pointer[2:].split("/")
        curr = root
        for part in parts:
            assert part in curr, f"Referência {pointer} falhou em resolver '{part}'"
            curr = curr[part]
        return curr

    refs_found: list[str] = []

    def _find_refs(obj: object) -> None:
        """Executa a operação find refs."""
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref = str(obj["$ref"])
                refs_found.append(ref)
                resolved = _resolve_pointer(schema_json, ref)
                assert isinstance(resolved, dict)
            for v in obj.values():
                _find_refs(v)
        elif isinstance(obj, list):
            for item in obj:
                _find_refs(item)

    _find_refs(schema_json)
    assert "#/definitions/QualityGateSpec" in refs_found
