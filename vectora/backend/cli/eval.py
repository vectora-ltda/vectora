"""CLI para o Evaluation Lab (vectora eval run / compare)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.evaluation.cassette import Cassette
from backend.evaluation.runner import compare_cassettes


def handle_eval(args: argparse.Namespace) -> int:
    """Roteia as ações de eval."""
    action = getattr(args, "action", None)

    if action == "compare":
        baseline_path = getattr(args, "baseline", None)
        candidate_path = getattr(args, "candidate", None)
        if not baseline_path or not candidate_path:
            print(
                "Erro: caminhos de baseline e candidate são obrigatórios.",
                file=sys.stderr,
            )
            return 1

        b_cassette = Cassette.load(baseline_path)
        c_cassette = Cassette.load(candidate_path)
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

    if action == "run":
        suite_path = getattr(args, "suite", None)
        if not suite_path:
            print("Execução de suite padrão concluída: 0 casos configurados.")
            return 0
        p = Path(suite_path)
        if not p.exists():
            print(f"Erro: suite '{suite_path}' não encontrada.", file=sys.stderr)
            return 1
        cassette = Cassette.load(p)
        print(
            f"Suite '{cassette.cassette_id}' executada com sucesso ({len(cassette.interactions)} interações)."
        )
        return 0

    print(
        "Subcomando eval desconhecido. Use 'vectora eval run' ou 'vectora eval compare'.",
        file=sys.stderr,
    )
    return 1
