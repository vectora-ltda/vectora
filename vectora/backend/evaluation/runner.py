"""Executor de suites do Evaluation Lab e comparação de performance e decisões."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.evaluation.cassette import Cassette


class EvalMetric(BaseModel):
    """Métrica individual comparada entre baseline e candidato."""

    name: str
    baseline_value: float
    candidate_value: float
    delta: float
    improved: bool


class EvalReport(BaseModel):
    """Relatório estruturado de avaliação."""

    suite_name: str
    total_cases: int
    matches: int
    accuracy: float
    avg_latency_reduction_pct: float = 0.0
    token_savings_pct: float = 0.0
    metrics: list[EvalMetric] = Field(default_factory=list)
    details: list[dict[str, Any]] = Field(default_factory=list)


def compare_cassettes(
    baseline_cassette: Cassette, candidate_cassette: Cassette
) -> EvalReport:
    """Compara duas cassetes (ex: baseline SystemTwo vs candidato SystemOne)."""
    total = min(
        len(baseline_cassette.interactions),
        len(candidate_cassette.interactions),
    )
    if total == 0:
        return EvalReport(
            suite_name="comparison",
            total_cases=0,
            matches=0,
            accuracy=0.0,
            metrics=[],
            details=[],
        )

    matches = 0
    total_baseline_latency = 0
    total_candidate_latency = 0
    total_baseline_tokens = 0
    total_candidate_tokens = 0
    details: list[dict[str, Any]] = []

    for i in range(total):
        b_item = baseline_cassette.interactions[i]
        c_item = candidate_cassette.interactions[i]

        b_tools = [t.get("name") for t in b_item.tool_calls]
        c_tools = [t.get("name") for t in c_item.tool_calls]
        is_match = b_tools == c_tools
        if is_match:
            matches += 1

        b_lat = b_item.duration_ms
        c_lat = c_item.duration_ms
        total_baseline_latency += b_lat
        total_candidate_latency += c_lat

        b_tok = sum(b_item.usage.values())
        c_tok = sum(c_item.usage.values())
        total_baseline_tokens += b_tok
        total_candidate_tokens += c_tok

        details.append(
            {
                "index": i,
                "matched": is_match,
                "baseline_tools": b_tools,
                "candidate_tools": c_tools,
                "latency_delta_ms": c_lat - b_lat,
                "tokens_delta": c_tok - b_tok,
                "candidate_decision": c_item.systemone_decision,
            }
        )

    accuracy = matches / total if total > 0 else 1.0

    # Redução de latência
    latency_reduction = (
        ((total_baseline_latency - total_candidate_latency) / total_baseline_latency)
        * 100.0
        if total_baseline_latency > 0
        else 0.0
    )

    # Economia de tokens
    token_savings = (
        ((total_baseline_tokens - total_candidate_tokens) / total_baseline_tokens)
        * 100.0
        if total_baseline_tokens > 0
        else 0.0
    )

    metrics = [
        EvalMetric(
            name="accuracy",
            baseline_value=1.0,
            candidate_value=accuracy,
            delta=accuracy - 1.0,
            improved=accuracy >= 0.95,
        ),
        EvalMetric(
            name="latency_ms",
            baseline_value=float(total_baseline_latency),
            candidate_value=float(total_candidate_latency),
            delta=float(total_candidate_latency - total_baseline_latency),
            improved=total_candidate_latency <= total_baseline_latency,
        ),
        EvalMetric(
            name="tokens",
            baseline_value=float(total_baseline_tokens),
            candidate_value=float(total_candidate_tokens),
            delta=float(total_candidate_tokens - total_baseline_tokens),
            improved=total_candidate_tokens <= total_baseline_tokens,
        ),
    ]

    return EvalReport(
        suite_name=f"{baseline_cassette.cassette_id}_vs_{candidate_cassette.cassette_id}",
        total_cases=total,
        matches=matches,
        accuracy=accuracy,
        avg_latency_reduction_pct=latency_reduction,
        token_savings_pct=token_savings,
        metrics=metrics,
        details=details,
    )
