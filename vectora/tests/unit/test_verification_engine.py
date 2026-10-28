"""Testes unitários para o motor de verificação pós-execução e quality gates."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.contracts.models import (
    QualityGateSpec,
    WorkspaceVerificationProfile,
)
from backend.services.verification import snapshot
from backend.services.verification.engine import _matches_any_pattern, run_verification
from backend.services.verification.profile_store import (
    _profile_path,
    get_verification_profile,
    save_verification_profile,
)
from backend.services.verification.snapshot import compute_files_snapshot


@pytest.fixture
def test_workspace(tmp_path: Path) -> Path:
    """Verifica o comportamento de workspace."""
    ws = tmp_path / "ws_test"
    ws.mkdir()
    (ws / "main.py").write_text("print('hello world')\n", encoding="utf-8")
    (ws / "utils.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    return ws


def test_compute_files_snapshot(test_workspace: Path) -> None:
    """Verifica o comportamento de compute files snapshot."""
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


def test_compute_files_snapshot_unreadable_and_traversal(
    test_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifica o comportamento de compute files snapshot unreadable and traversal."""
    (test_workspace / "unreadable.py").write_text("secret", encoding="utf-8")
    original_hash = snapshot.hash_file_sha256

    def mock_hash(p: Path) -> str:
        """Executa a operação mock hash."""
        if p.name == "unreadable.py":
            return ""
        return original_hash(p)

    monkeypatch.setattr(
        "backend.services.verification.snapshot.hash_file_sha256", mock_hash
    )

    sha, file_hashes = compute_files_snapshot(
        test_workspace,
        files=["main.py", "unreadable.py", "non_existent.py", "../outside.py"],
    )
    assert len(sha) == 64
    assert file_hashes["main.py"] != "unreadable"
    assert file_hashes["unreadable.py"] == "unreadable"
    assert "non_existent.py" not in file_hashes
    assert "../outside.py" not in file_hashes


def test_pattern_matching_recursive() -> None:
    """Verifica o comportamento de pattern matching recursive."""
    assert _matches_any_pattern("main.py", ["**/*.py"]) is True
    assert _matches_any_pattern("src/app.py", ["**/*.py"]) is True
    assert _matches_any_pattern("src/components/button.tsx", ["src/**/*.tsx"]) is True
    assert _matches_any_pattern("src/button.tsx", ["src/**/*.tsx"]) is True
    assert _matches_any_pattern("readme.md", ["**/*.py"]) is False


def test_quality_gate_spec_empty_command_validation() -> None:
    """Verifica o comportamento de quality gate spec empty command validation."""
    with pytest.raises(ValidationError):
        QualityGateSpec(
            id="empty_gate",
            name="Empty Gate",
            command=[],
        )


@pytest.mark.asyncio
async def test_run_verification_profile_empty(test_workspace: Path) -> None:
    """Verifica o comportamento de run verification profile empty."""
    profile = WorkspaceVerificationProfile(
        workspace_id="test_ws",
        enabled=True,
        gates=[],
    )
    res = await run_verification("test_ws", test_workspace, profile=profile)
    assert res.passed is True
    assert res.status == "passed"
    assert res.outcome == "not_run"
    assert len(res.snapshot_sha256) == 64
    assert res.gate_results == []


@pytest.mark.asyncio
async def test_run_verification_dirty_git(
    test_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifica o comportamento de run verification dirty git."""
    profile = WorkspaceVerificationProfile(
        workspace_id="test_ws",
        enabled=True,
        require_clean_git=True,
        gates=[],
    )

    async def mock_clean(_cwd: Path) -> tuple[bool, str]:
        """Executa a operação mock clean."""
        return False, " M modified_file.py"

    monkeypatch.setattr(
        "backend.services.verification.engine._is_git_working_tree_clean",
        mock_clean,
    )
    res = await run_verification("test_ws", test_workspace, profile=profile)
    assert res.passed is False
    assert res.status == "failed"
    assert res.outcome == "blocked"
    assert "Git working tree" in (res.failure_feedback or "")


@pytest.mark.asyncio
async def test_run_verification_empty_command_fails(test_workspace: Path) -> None:
    """Verifica o comportamento de run verification empty command fails."""
    gate = QualityGateSpec.model_construct(
        id="empty_cmd_gate",
        name="Empty Command Gate",
        command=[],
        policy="block",
        timeout_seconds=30,
        file_patterns=[],
    )
    profile = WorkspaceVerificationProfile(
        workspace_id="test_ws",
        enabled=True,
        gates=[gate],
    )
    res = await run_verification("test_ws", test_workspace, profile=profile)
    assert res.passed is False
    assert res.status == "failed"
    assert res.outcome == "failed"
    assert len(res.gate_results) == 1
    assert res.gate_results[0].passed is False


@pytest.mark.asyncio
async def test_run_verification_dispatch_error_is_not_approved(
    test_workspace: Path,
) -> None:
    """Falha de infraestrutura não pode ser reportada como execução aprovada."""
    gate = QualityGateSpec.model_construct(
        id="missing_binary",
        name="Missing binary",
        command=[str(test_workspace / "does-not-exist")],
        policy="degrade",
        timeout_seconds=5,
        file_patterns=[],
    )
    result = await run_verification(
        "infra_ws",
        test_workspace,
        profile=WorkspaceVerificationProfile(
            workspace_id="infra_ws", enabled=True, gates=[gate]
        ),
    )
    assert result.outcome == "infra_error"
    assert result.status == "failed"
    assert result.passed is False


@pytest.mark.asyncio
async def test_run_verification_passing_gate(test_workspace: Path) -> None:
    """Verifica o comportamento de run verification passing gate."""
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
    assert res.outcome == "passed"
    assert len(res.gate_results) == 1
    assert res.gate_results[0].passed is True
    assert res.gate_results[0].exit_code == 0
    assert res.gate_results[0].reason == "passed"

    evidence = res.to_evidence_entry()
    assert evidence["kind"] == "quality_gate_verification"
    assert evidence["run_id"] is None
    assert evidence["policy_snapshot_id"] is None
    assert evidence["passed"] is True
    assert evidence["snapshot_sha256"] == res.snapshot_sha256
    assert evidence["file_hashes"] == dict(sorted(res.file_hashes.items()))
    assert evidence["artifact_refs"] == []
    assert evidence["commit_sha"] == res.commit_sha


@pytest.mark.asyncio
async def test_run_verification_preserves_run_and_policy_provenance(
    test_workspace: Path,
) -> None:
    """A gate result cannot be detached from the run and policy that authorized it."""
    profile = WorkspaceVerificationProfile(
        workspace_id="provenance_ws", enabled=True, gates=[]
    )
    result = await run_verification(
        "provenance_ws",
        test_workspace,
        profile=profile,
        run_id="run-123",
        policy_snapshot_id="policy-v7",
    )
    evidence = result.to_evidence_entry()
    assert evidence["run_id"] == "run-123"
    assert evidence["policy_snapshot_id"] == "policy-v7"
    assert "commit_sha" in evidence


@pytest.mark.asyncio
async def test_run_verification_runs_patterned_gates_when_paths_are_unknown(
    test_workspace: Path,
) -> None:
    """Unknown tool paths must fail closed into evaluating every configured gate."""
    gate = QualityGateSpec(
        id="python_gate",
        name="Python gate",
        command=[sys.executable, "-c", "print('executed')"],
        file_patterns=["**/*.py"],
        policy="block",
    )
    profile = WorkspaceVerificationProfile(
        workspace_id="unknown_paths",
        enabled=True,
        gates=[gate],
    )

    # ``None`` means a tool did not report paths; it must not silently skip a gate.
    result = await run_verification(
        "unknown_paths", test_workspace, files_touched=None, profile=profile
    )

    assert [item.gate_id for item in result.gate_results] == ["python_gate"]
    assert result.gate_results[0].passed is True


@pytest.mark.asyncio
async def test_run_verification_bounds_gate_output(test_workspace: Path) -> None:
    """Unbounded gate output must not be retained in verification evidence."""
    gate = QualityGateSpec(
        id="noisy_gate",
        name="Noisy gate",
        command=[sys.executable, "-c", "print('x' * 50000)"],
        policy="block",
    )
    profile = WorkspaceVerificationProfile(
        workspace_id="noisy_output",
        enabled=True,
        gates=[gate],
    )

    result = await run_verification("noisy_output", test_workspace, profile=profile)

    assert result.gate_results[0].passed is True
    assert len(result.gate_results[0].output) <= 10_000


@pytest.mark.asyncio
async def test_run_verification_blocking_failure(test_workspace: Path) -> None:
    """Verifica o comportamento de run verification blocking failure."""
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
    assert res.outcome == "failed"
    assert len(res.gate_results) == 1
    assert res.gate_results[0].passed is False
    assert res.gate_results[0].reason == "assertion_failed"
    assert "gate error" in (res.failure_feedback or "")


@pytest.mark.asyncio
async def test_run_verification_degrading_failure(test_workspace: Path) -> None:
    """Verifica o comportamento de run verification degrading failure."""
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


@pytest.mark.asyncio
async def test_corrupted_profile_fail_closed(
    test_workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verifica o comportamento de corrupted profile fail closed."""
    monkeypatch.setattr(
        "backend.services.verification.profile_store._profiles_dir",
        lambda: tmp_path / "profiles",
    )
    p_path = _profile_path("corrupted_ws")
    p_path.parent.mkdir(parents=True, exist_ok=True)
    p_path.write_text("{bad json content", encoding="utf-8")

    profile = get_verification_profile("corrupted_ws")
    assert profile.enabled is True
    assert len(profile.gates) == 1
    assert profile.gates[0].id == "corrupted_profile_guard"

    res = await run_verification("corrupted_ws", test_workspace, profile=profile)
    assert res.passed is False
    assert res.status == "failed"


def test_save_verification_profile_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifica o comportamento de save verification profile atomic."""
    monkeypatch.setattr(
        "backend.services.verification.profile_store._profiles_dir",
        lambda: tmp_path / "profiles",
    )
    profile = WorkspaceVerificationProfile(
        workspace_id="atomic_ws",
        enabled=True,
        gates=[],
    )
    save_verification_profile(profile)
    loaded = get_verification_profile("atomic_ws")
    assert loaded.workspace_id == "atomic_ws"
    assert loaded.enabled is True


@pytest.mark.asyncio
async def test_run_verification_target_gate_missing_fails(
    test_workspace: Path,
) -> None:
    """Verifica o comportamento de run verification target gate missing fails."""
    profile = WorkspaceVerificationProfile(
        workspace_id="test_ws",
        enabled=True,
        gates=[],
    )
    res = await run_verification(
        "test_ws", test_workspace, profile=profile, target_gate_ids=["nonexistent_gate"]
    )
    assert res.passed is False
    assert res.status == "failed"
    assert res.outcome == "failed"
    assert "Quality gates solicitados não existem" in (res.failure_feedback or "")
