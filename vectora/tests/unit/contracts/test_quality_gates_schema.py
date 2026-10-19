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
