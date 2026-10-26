"""Evaluation Lab com suporte a Replay e Benchmark de Decisões."""

from backend.evaluation.cassette import Cassette, CassetteInteraction
from backend.evaluation.replay_client import ReplayChatClient
from backend.evaluation.runner import EvalMetric, EvalReport, compare_cassettes

__all__ = [
    "Cassette",
    "CassetteInteraction",
    "EvalMetric",
    "EvalReport",
    "ReplayChatClient",
    "compare_cassettes",
]
