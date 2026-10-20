"""Testes unitários para o motor de verificação pós-execução e quality gates."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from backend.contracts.models import (
    QualityGateSpec,
    WorkspaceVerificationProfile,
)
from backend.services.verification.engine import run_verification
from backend.services.verification.profile_store import (
    get_verification_profile,
    save_verification_profile,
)
from backend.services.verification.snapshot import compute_files_snapshot


@pytest.fixture
def test_workspace(tmp_path: Path):
    ws = tmp_path / "ws_test"
    ws.mkdir()
    (ws / "main.py").write_text("print('hello world')\n", encoding="utf-8")
    (ws / "utils.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    return ws


def test_compute_files_snapshot(test_workspace: Path):
    sha, file_hashes = compute_files_snapshot(test_workspace)
    assert len(sha) == 64  # SHA-256 hex digest
    assert "main.py" in file_hashes
    assert "utils.py" in file_hashes

    # Alterar um arquivo deve alterar o hash
    (test_workspace / "main.py").write_text("print('modified')\n", encoding="utf-8")
    sha2, file_hashes2 = compute_files_snapshot(test_workspace)
    assert sha2 != sha
    assert file_hashes2["main.py"] != file_hashes["main.py"]
    assert file_hashes2["utils.py"] == file_hashes["utils.py"]


@pytest.mark.asyncio
async def test_run_verification_profile_empty(test_workspace: Path):
    profile = WorkspaceVerificationProfile(
        workspace_id="test_ws",
        enabled=True,
        gates=[],
    )
    res = await run_verification("test_ws", test_workspace, profile=profile)
    assert res.passed is True
    assert res.status == "passed"
    assert len(res.snapshot_sha256) == 64
    assert res.gate_results == []


@pytest.mark.asyncio
async def test_run_verification_passing_gate(test_workspace: Path):
    gate = QualityGateSpec(
        id="syntax_check",
        name="Syntax Check",
        command=[sys.executable, "-c", "import sys; sys.exit(0)"],
        policy="block",
    )
    profile = WorkspaceVerificationProfile(
        workspace_id="test_ws",
        enabled=True,
        gates=[gate],
    )
    res = await run_verification("test_ws", test_workspace, profile=profile)
    assert res.passed is True
    assert res.status == "passed"
    assert len(res.gate_results) == 1
    assert res.gate_results[0].passed is True
    assert res.gate_results[0].exit_code == 0

    evidence = res.to_evidence_entry()
    assert evidence["kind"] == "quality_gate_verification"
    assert evidence["passed"] is True
    assert evidence["snapshot_sha256"] == res.snapshot_sha256


@pytest.mark.asyncio
async def test_run_verification_blocking_failure(test_workspace: Path):
    gate = QualityGateSpec(
        id="failing_gate",
        name="Failing Gate",
        command=[
            sys.executable,
            "-c",
            "import sys; print('gate error', file=sys.stderr); sys.exit(1)",
        ],
        policy="block",
    )
    profile = WorkspaceVerificationProfile(
        workspace_id="test_ws",
        enabled=True,
        gates=[gate],
        inject_stack_trace_on_failure=True,
    )
    res = await run_verification("test_ws", test_workspace, profile=profile)
    assert res.passed is False
    assert res.status == "failed"
    assert len(res.gate_results) == 1
    assert res.gate_results[0].passed is False
    assert "gate error" in (res.failure_feedback or "")


@pytest.mark.asyncio
async def test_run_verification_degrading_failure(test_workspace: Path):
    gate = QualityGateSpec(
        id="advisory_gate",
        name="Advisory Gate",
        command=[sys.executable, "-c", "import sys; sys.exit(1)"],
        policy="degrade",
    )
    profile = WorkspaceVerificationProfile(
        workspace_id="test_ws",
        enabled=True,
        gates=[gate],
    )
    res = await run_verification("test_ws", test_workspace, profile=profile)
    assert res.passed is True  # Degraded ainda permite avanço
    assert res.status == "degraded"
