"""Motor de verificação pós-execução e enforcement de quality gates."""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from backend.contracts.models import (
    QualityGatePolicy,
    QualityGateRunResult,
    QualityGateSpec,
    WorkspaceVerificationProfile,
)
from backend.services.verification.profile_store import get_verification_profile
from backend.services.verification.snapshot import compute_files_snapshot

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Resultado consolidado do ciclo de verificação pós-execução."""

    passed: bool
    status: Literal["passed", "degraded", "failed"]
    snapshot_sha256: str
    file_hashes: dict[str, str] = field(default_factory=dict)
    gate_results: list[QualityGateRunResult] = field(default_factory=list)
    failure_feedback: str | None = None

    def to_evidence_entry(self) -> dict[str, Any]:
        """Formata o resultado da verificação como evidência persistível no AgentRun."""
        return {
            "kind": "quality_gate_verification",
            "passed": self.passed,
            "status": self.status,
            "snapshot_sha256": self.snapshot_sha256,
            "gates_evaluated": len(self.gate_results),
            "gate_results": [g.model_dump() for g in self.gate_results],
        }


def _matches_any_pattern(file_path: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    norm_path = file_path.replace("\\", "/")
    pure_p = PurePosixPath(norm_path)
    basename = pure_p.name
    for pat in patterns:
        if fnmatch.fnmatch(norm_path, pat) or fnmatch.fnmatch(basename, pat):
            return True
        if pure_p.match(pat):
            return True
        if pat.startswith("**/"):
            stripped = pat[3:]
            if (
                fnmatch.fnmatch(norm_path, stripped)
                or fnmatch.fnmatch(basename, stripped)
                or pure_p.match(stripped)
            ):
                return True
        if "/**/" in pat:
            collapsed = pat.replace("/**/", "/")
            if fnmatch.fnmatch(norm_path, collapsed) or pure_p.match(collapsed):
                return True
    return False


async def _execute_single_gate(
    gate: QualityGateSpec, cwd: Path
) -> QualityGateRunResult:
    """Executa um comando de quality gate de forma isolada e mede sua saída."""
    t0 = time.perf_counter()
    cmd = list(gate.command)
    if not cmd:
        return QualityGateRunResult(
            gate_id=gate.id,
            passed=True,
            exit_code=0,
            output="No command specified",
            duration_ms=0,
        )

    try:
        kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True

        proc = await asyncio.create_subprocess_exec(
            cmd[0],
            *cmd[1:],
            **kwargs,
        )
        try:
            stdout_data, _ = await asyncio.wait_for(
                proc.communicate(), timeout=float(gate.timeout_seconds)
            )
            exit_code = proc.returncode if proc.returncode is not None else 1
            output = (stdout_data or b"").decode("utf-8", errors="replace")
        except TimeoutError:
            if os.name != "nt" and hasattr(os, "killpg"):
                with contextlib.suppress(ProcessLookupError, Exception):
                    os.killpg(proc.pid, 9)
            else:
                with contextlib.suppress(ProcessLookupError, Exception):
                    proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            exit_code = -1
            output = f"Tempo limite de execução ({gate.timeout_seconds}s) excedido para o gate '{gate.id}'"
    except Exception as exc:
        exit_code = 1
        output = f"Falha ao despachar comando do gate '{gate.id}': {exc}"

    duration_ms = int((time.perf_counter() - t0) * 1000)
    passed = exit_code == 0
    return QualityGateRunResult(
        gate_id=gate.id,
        passed=passed,
        exit_code=exit_code,
        output=output[:10000],  # Limite seguro para armazenamento
        duration_ms=duration_ms,
    )


async def run_verification(
    workspace_id: str | None,
    cwd: Path | str | None,
    *,
    files_touched: list[str] | None = None,
    profile: WorkspaceVerificationProfile | None = None,
) -> VerificationResult:
    """Executa o ciclo completo de verificação pós-execução.

    Calcula o snapshot SHA-256 dos arquivos, seleciona os quality gates aplicáveis,
    executa os comandos e determina se o status final é passed, degraded ou failed.
    """
    root_dir = Path(cwd) if cwd else Path.cwd()
    prof = profile or get_verification_profile(workspace_id)

    # 1. Cálculo do snapshot SHA-256 criptográfico
    snapshot_sha256, file_hashes = await asyncio.to_thread(
        compute_files_snapshot, root_dir, files=files_touched
    )

    if not prof.enabled or not prof.gates:
        return VerificationResult(
            passed=True,
            status="passed",
            snapshot_sha256=snapshot_sha256,
            file_hashes=file_hashes,
            gate_results=[],
        )

    # 2. Filtragem dos gates relevantes aos arquivos alterados
    applicable_gates: list[QualityGateSpec] = []
    for g in prof.gates:
        if not g.file_patterns or (
            files_touched
            and any(_matches_any_pattern(f, g.file_patterns) for f in files_touched)
        ):
            applicable_gates.append(g)
        elif not files_touched:
            # Se não houver lista específica de alterados, roda todos os gates configurados
            applicable_gates.append(g)

    gate_results: list[QualityGateRunResult] = []
    has_blocking_failure = False
    has_degrading_failure = False
    failure_feedback_parts: list[str] = []

    for gate in applicable_gates:
        res = await _execute_single_gate(gate, root_dir)
        gate_results.append(res)
        if not res.passed:
            if gate.policy == "block":
                has_blocking_failure = True
            elif gate.policy == "degrade":
                has_degrading_failure = True

            if prof.inject_stack_trace_on_failure:
                failure_feedback_parts.append(
                    f"[QualityGate '{gate.id}' ({gate.name}) FALHOU com código {res.exit_code}]:\n{res.output}"
                )

    if has_blocking_failure:
        status: Literal["passed", "degraded", "failed"] = "failed"
        overall_passed = False
    elif has_degrading_failure:
        status = "degraded"
        overall_passed = True  # Degraded ainda permite avanço, mas com sinalização
    else:
        status = "passed"
        overall_passed = True

    feedback = "\n\n".join(failure_feedback_parts) if failure_feedback_parts else None

    return VerificationResult(
        passed=overall_passed,
        status=status,
        snapshot_sha256=snapshot_sha256,
        file_hashes=file_hashes,
        gate_results=gate_results,
        failure_feedback=feedback,
    )
