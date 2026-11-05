"""Capture provider inputs and join durable decisions and tool outcomes for replay."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING

from backend.evaluation.cassette import Cassette, CassetteInteraction
from backend.persistence.native.agent_run import AgentRun, redact_sensitive_data

if TYPE_CHECKING:
    from backend.persistence.native.session_store import SessionStore
    from backend.vtypes.message import ToolCall, VMessage


async def record_interaction(
    store: SessionStore,
    *,
    run_id: str,
    messages: list[VMessage],
    response: str,
    tool_calls: list[ToolCall],
    usage: dict[str, int] | None,
    model: str,
    duration_ms: int,
    policy_digest: str,
) -> None:
    """Persist an opt-in model trace before any corresponding tool effect."""
    history = [
        {
            "role": message.role.value,
            "content": [asdict(block) for block in message.content],
            "tool_calls": [
                {"id": call.id, "name": call.name, "args": call.args}
                for call in message.tool_calls
            ],
            "tool_call_id": message.tool_call_id,
            "name": message.name,
        }
        for message in messages
    ]
    identity = hashlib.sha256(json.dumps(history, sort_keys=True).encode()).hexdigest()
    interaction = CassetteInteraction(
        interaction_id=identity,
        messages=history,
        response_text=response,
        tool_calls=[
            {"id": call.id, "name": call.name, "args": call.args} for call in tool_calls
        ],
        usage=usage or {},
        run_id=run_id,
        duration_ms=duration_ms,
        policy_digest=policy_digest,
    )
    await store.record_run_evidence(
        run_id,
        evidence={
            "type": "evaluation_interaction",
            "evidence_id": f"evaluation:{identity}",
            "model": model,
            "interaction": redact_sensitive_data(interaction.model_dump(mode="json")),
        },
    )


def cassette_from_run(run: AgentRun) -> Cassette:
    """Export recorded inputs together with decisions and observed result digests."""
    interactions = []
    model = ""
    for entry in run.evidence:
        if entry.get("type") != "evaluation_interaction":
            continue
        raw = entry.get("interaction")
        if not isinstance(raw, dict):
            raise ValueError("recorded interaction is missing or invalid")
        interaction = CassetteInteraction.model_validate(raw)
        if interaction.run_id != run.run_id:
            raise ValueError("interaction belongs to another run")
        call_ids = {call.get("id") for call in interaction.tool_calls}
        decisions = [
            item
            for item in run.evidence
            if item.get("type") == "decision_model_decision"
            and item.get("tool_call_id") in call_ids
        ]
        results = [
            item
            for item in run.evidence
            if item.get("type") == "tool_result"
            and item.get("tool_call_id") in call_ids
        ]
        snapshot = next(
            (
                item
                for item in run.evidence
                if item.get("kind") == "decision_model_snapshot"
            ),
            None,
        )
        if call_ids or snapshot is not None:
            interaction.systemone_decision_evidence = redact_sensitive_data(
                {
                    "snapshot": snapshot,
                    "decisions": decisions,
                    "tool_results": results,
                    "outcomes_complete": call_ids
                    == {item.get("tool_call_id") for item in results},
                }
            )
        model = str(entry.get("model") or model)
        interactions.append(interaction)
    if not interactions:
        raise ValueError("run has no recorded evaluation interactions")
    return Cassette(
        cassette_id=run.run_id,
        source_run_id=run.run_id,
        model=model,
        interactions=interactions,
    )
