"""Executor de suites do Evaluation Lab e comparação de performance e decisões."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.evaluation.cassette import Cassette
from backend.vtypes import JsonObject


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
    details: list[JsonObject] = Field(default_factory=list)


def _total_tokens(usage: dict[str, int]) -> int:
    """Calcula o total de tokens preferindo chaves consolidadas ou somando input/output."""
    if not usage:
        return 0
    if "total" in usage:
        return int(usage["total"])
    if "total_tokens" in usage:
        return int(usage["total_tokens"])
    inp = int(usage.get("input", usage.get("prompt", usage.get("input_tokens", 0))))
    out = int(
        usage.get("output", usage.get("completion", usage.get("output_tokens", 0)))
    )
    return inp + out


def compare_cassettes(
    baseline_cassette: Cassette, candidate_cassette: Cassette
) -> EvalReport:
    """Compara duas cassetes (ex: baseline SystemTwo vs candidato SystemOne)."""
    baseline_ids = [item.interaction_id for item in baseline_cassette.interactions]
    candidate_ids = [item.interaction_id for item in candidate_cassette.interactions]
    if len(set(baseline_ids)) != len(baseline_ids) or len(set(candidate_ids)) != len(
        candidate_ids
    ):
        raise ValueError("cassetes não podem conter interaction_id duplicado")
    baseline_by_id = {
        item.interaction_id: item for item in baseline_cassette.interactions
    }
    candidate_by_id = {
        item.interaction_id: item for item in candidate_cassette.interactions
    }
    # Keep baseline order, then append candidate-only interactions. This makes
    # missing/extra cases explicit instead of hiding an early termination with
    # positional min(len(baseline), len(candidate)) alignment.
    interaction_ids = baseline_ids + [
        item_id for item_id in candidate_ids if item_id not in baseline_by_id
    ]
    total = len(interaction_ids)
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

    for i, interaction_id in enumerate(interaction_ids):
        b_item = baseline_by_id.get(interaction_id)
        c_item = candidate_by_id.get(interaction_id)

        if b_item is not None and c_item is not None:
            b_tools = [t.get("name") for t in b_item.tool_calls]
            c_tools = [t.get("name") for t in c_item.tool_calls]
            is_match = b_tools == c_tools
            if is_match:
                matches += 1

            b_lat = b_item.duration_ms
            c_lat = c_item.duration_ms
            b_tok = _total_tokens(b_item.usage)
            c_tok = _total_tokens(c_item.usage)

            details.append(
                {
                    "index": i,
                    "interaction_id": interaction_id,
                    "matched": is_match,
                    "baseline_tools": b_tools,
                    "candidate_tools": c_tools,
                    "latency_delta_ms": c_lat - b_lat,
                    "tokens_delta": c_tok - b_tok,
                    "candidate_decision": c_item.systemone_decision,
                }
            )
        elif b_item is not None:
            b_tools = [t.get("name") for t in b_item.tool_calls]
            b_lat = b_item.duration_ms
            c_lat = 0
            b_tok = _total_tokens(b_item.usage)
            c_tok = 0
            details.append(
                {
                    "index": i,
                    "interaction_id": interaction_id,
                    "matched": False,
                    "missing": "candidate",
                    "baseline_tools": b_tools,
                    "candidate_tools": None,
                    "latency_delta_ms": -b_lat,
                    "tokens_delta": -b_tok,
                    "candidate_decision": None,
                }
            )
        elif c_item is not None:
            c_tools = [t.get("name") for t in c_item.tool_calls]
            b_lat = 0
            c_lat = c_item.duration_ms
            b_tok = 0
            c_tok = _total_tokens(c_item.usage)
            details.append(
                {
                    "index": i,
                    "interaction_id": interaction_id,
                    "matched": False,
                    "missing": "baseline",
                    "baseline_tools": None,
                    "candidate_tools": c_tools,
                    "latency_delta_ms": c_lat,
                    "tokens_delta": c_tok,
                    "candidate_decision": c_item.systemone_decision,
                }
            )

        total_baseline_latency += b_lat
        total_candidate_latency += c_lat
        total_baseline_tokens += b_tok
        total_candidate_tokens += c_tok

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
