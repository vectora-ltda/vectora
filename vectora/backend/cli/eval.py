"""CLI para o Evaluation Lab (vectora eval run / compare)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from backend.evaluation.cassette import Cassette
from backend.evaluation.replay_client import ReplayChatClient
from backend.evaluation.runner import compare_cassettes
from backend.vtypes import JsonObject
from backend.vtypes.message import ContentBlock, MessageRole, VMessage


async def _replay_suite(cassette: Cassette) -> list[JsonObject]:
    client = ReplayChatClient(cassette)
    results: list[JsonObject] = []
    for idx, interaction in enumerate(cassette.interactions):
        msgs: list[VMessage] = []
        for m in interaction.messages:
            role_val = m.get("role", "user")
            try:
                role = MessageRole(role_val)
            except ValueError:
                role = MessageRole.USER
            msgs.append(
                VMessage(
                    role=role,
                    content=[ContentBlock(kind="text", text=str(m.get("content", "")))],
                )
            )
        res = await client.agenerate(msgs)
        results.append(
            {
                "index": idx,
                "interaction_id": interaction.interaction_id,
                "role": res.role.value,
                "response_text": res.text(),
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "args": tc.args}
                    for tc in res.tool_calls
                ],
            }
        )
    return results


def _handle_compare(args: argparse.Namespace) -> int:
    baseline_path = getattr(args, "baseline", None)
    candidate_path = getattr(args, "candidate", None)
    if not baseline_path or not candidate_path:
        print(
            "Erro: caminhos de baseline e candidate são obrigatórios.",
            file=sys.stderr,
        )
        return 1

    try:
        b_cassette = Cassette.load(baseline_path)
        c_cassette = Cassette.load(candidate_path)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(
            f"Erro ao carregar cassetes para comparação: {exc}",
            file=sys.stderr,
        )
        return 1

    report = compare_cassettes(b_cassette, c_cassette)

    if getattr(args, "output", "human") == "json":
        print(json.dumps(report.model_dump(), indent=2, ensure_ascii=False))
    else:
        print(f"=== Relatório de Avaliação: {report.suite_name} ===")
        print(f"Casos comparados: {report.total_cases}")
        print(f"Acurácia / Concordância: {report.accuracy * 100:.1f}%")
        print(f"Redução de latência: {report.avg_latency_reduction_pct:+.1f}%")
        print(f"Economia de tokens: {report.token_savings_pct:+.1f}%")
    return 0


def _handle_run(args: argparse.Namespace) -> int:
    suite_path = getattr(args, "suite", None)
    if not suite_path:
        print(
            "Erro: o parâmetro --suite é obrigatório para execução de avaliação.",
            file=sys.stderr,
        )
        return 1
    p = Path(suite_path)
    if not p.exists():
        print(f"Erro: suite '{suite_path}' não encontrada.", file=sys.stderr)
        return 1

    try:
        cassette = Cassette.load(p)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(
            f"Erro ao carregar cassete '{suite_path}': {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        replayed = asyncio.run(_replay_suite(cassette))
    except Exception as exc:
        print(
            f"Erro durante o replay da suite '{cassette.cassette_id}': {exc}",
            file=sys.stderr,
        )
        return 1

    if getattr(args, "output", "human") == "json":
        output_data = {
            "suite_id": cassette.cassette_id,
            "total_interactions": len(cassette.interactions),
            "replayed_interactions": len(replayed),
            "results": replayed,
        }
        print(json.dumps(output_data, indent=2, ensure_ascii=False))
    else:
        print(
            f"Suite '{cassette.cassette_id}' executada com sucesso ({len(replayed)} interações reproduzidas)."
        )
    return 0


def handle_eval(args: argparse.Namespace) -> int:
    """Roteia as ações de eval."""
    action = getattr(args, "action", None)

    if action == "compare":
        return _handle_compare(args)

    if action == "run":
        return _handle_run(args)

    print(
        "Subcomando eval desconhecido. Use 'vectora eval run' ou 'vectora eval compare'.",
        file=sys.stderr,
    )
    return 1
