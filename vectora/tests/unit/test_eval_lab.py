"""Testes unitários para o Evaluation Lab, ReplayChatClient e comparação de cassetes."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from backend.cli.eval import handle_eval
from backend.evaluation.cassette import Cassette, CassetteInteraction
from backend.evaluation.replay_client import ReplayChatClient
from backend.evaluation.runner import compare_cassettes
from backend.vtypes.message import MessageRole, text_message


def test_cassette_save_and_load_with_redaction(tmp_path: Path):
    target = tmp_path / "test_cassette.json"
    cassette = Cassette(
        cassette_id="cas-001",
        model="systemone:decision",
        interactions=[
            CassetteInteraction(
                interaction_id="int-1",
                messages=[
                    {
                        "role": "user",
                        "content": "minha chave e sk-1234567890abcdef12345678",
                    }
                ],
                response_text="Resposta segura",
                duration_ms=120,
                usage={"input": 50, "output": 10},
            )
        ],
    )
    cassette.save(target)

    # Verifica que o arquivo persistido redigiu o segredo
    raw_content = target.read_text(encoding="utf-8")
    assert "sk-1234567890abcdef12345678" not in raw_content
    assert "[REDACTED]" in raw_content

    loaded = Cassette.load(target)
    assert loaded.cassette_id == "cas-001"
    assert len(loaded.interactions) == 1
    assert loaded.interactions[0].duration_ms == 120
    assert loaded.interactions[0].usage == {"input": 50, "output": 10}


@pytest.mark.asyncio
async def test_replay_chat_client():
    cassette = Cassette(
        cassette_id="cas-replay",
        interactions=[
            CassetteInteraction(
                interaction_id="int-1",
                messages=[{"role": "user", "content": "listar arquivos"}],
                response_text="Vou listar os arquivos",
                tool_calls=[
                    {"id": "call_1", "name": "file_read", "args": {"path": "."}}
                ],
                duration_ms=15,
                usage={"input": 20, "output": 5},
            )
        ],
    )

    client = ReplayChatClient(cassette)

    # Teste de agenerate
    user_msg = text_message(MessageRole.USER, "listar arquivos")
    res = await client.agenerate([user_msg])
    assert res.role == MessageRole.ASSISTANT
    assert res.text() == "Vou listar os arquivos"
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0].name == "file_read"

    # Reset do cursor e teste de astream
    client.reset()
    chunks = []
    async for chunk in client.astream([user_msg]):
        chunks.append(chunk)
    assert len(chunks) >= 1
    assert any(c.delta_text == "Vou listar os arquivos" for c in chunks)


def test_compare_cassettes():
    b_cassette = Cassette(
        cassette_id="baseline",
        interactions=[
            CassetteInteraction(
                interaction_id="1",
                messages=[],
                response_text="A",
                tool_calls=[{"id": "c1", "name": "tool_a", "args": {}}],
                duration_ms=500,
                usage={"total": 200},
            )
        ],
    )
    c_cassette = Cassette(
        cassette_id="candidate",
        interactions=[
            CassetteInteraction(
                interaction_id="1",
                messages=[],
                response_text="A",
                tool_calls=[{"id": "c1", "name": "tool_a", "args": {}}],
                duration_ms=50,
                usage={"total": 40},
                systemone_decision="fast_path",
            )
        ],
    )

    report = compare_cassettes(b_cassette, c_cassette)
    assert report.total_cases == 1
    assert report.matches == 1
    assert report.accuracy == 1.0
    assert report.avg_latency_reduction_pct == 90.0
    assert report.token_savings_pct == 80.0


def test_cli_eval_compare(tmp_path: Path, capsys):
    b_path = tmp_path / "base.json"
    c_path = tmp_path / "cand.json"
    Cassette(cassette_id="base").save(b_path)
    Cassette(cassette_id="cand").save(c_path)

    args = argparse.Namespace(
        action="compare",
        baseline=str(b_path),
        candidate=str(c_path),
        output="human",
    )
    code = handle_eval(args)
    assert code == 0
    captured = capsys.readouterr()
    assert "Relatório de Avaliação" in captured.out
