"""Testes unitários para o Evaluation Lab, ReplayChatClient e comparação de cassetes."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from backend.cli.eval import handle_eval
from backend.evaluation.cassette import Cassette, CassetteInteraction
from backend.evaluation.replay_client import ReplayChatClient
from backend.evaluation.runner import _total_tokens, compare_cassettes
from backend.vtypes.message import MessageRole, text_message


def test_cassette_save_and_load_with_redaction(tmp_path: Path):
    """Verifica o comportamento de cassette save and load with redaction."""
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
    """Verifica o comportamento de replay chat client."""
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
    chunks = [chunk async for chunk in client.astream([user_msg])]
    assert len(chunks) >= 1
    assert any(c.delta_text == "Vou listar os arquivos" for c in chunks)


@pytest.mark.asyncio
async def test_replay_chat_client_rejects_different_history() -> None:
    """Verifica o comportamento de replay chat client rejects different history."""
    cassette = Cassette(
        cassette_id="cas-history",
        interactions=[
            CassetteInteraction(
                interaction_id="int-1",
                messages=[{"role": "user", "content": "comando autorizado"}],
                response_text="ok",
            )
        ],
    )
    client = ReplayChatClient(cassette)
    with pytest.raises(RuntimeError, match="histórico não corresponde"):
        await client.agenerate([text_message(MessageRole.USER, "comando diferente")])


@pytest.mark.asyncio
async def test_replay_chat_client_accepts_native_usage_keys() -> None:
    """Verifica o comportamento de replay chat client accepts native usage keys."""
    cassette = Cassette(
        cassette_id="cas-usage",
        interactions=[
            CassetteInteraction(
                interaction_id="int-1",
                messages=[],
                response_text="ok",
                usage={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
            )
        ],
    )
    client = ReplayChatClient(cassette)
    chunks = [chunk async for chunk in client.astream([])]
    usage = next(chunk.usage for chunk in chunks if chunk.usage is not None)
    assert usage["input_tokens"] == 12
    assert usage["output_tokens"] == 3
    assert usage["total_tokens"] == 15


def test_compare_cassettes():
    """Verifica o comportamento de compare cassettes."""
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


def test_compare_cassettes_unequal_lengths():
    """Verifica o comportamento de compare cassettes unequal lengths."""
    b_cassette = Cassette(
        cassette_id="base",
        interactions=[
            CassetteInteraction(
                interaction_id="1",
                messages=[],
                response_text="A",
                tool_calls=[{"id": "c1", "name": "t1", "args": {}}],
                duration_ms=100,
                usage={"total": 10},
            ),
            CassetteInteraction(
                interaction_id="2",
                messages=[],
                response_text="B",
                tool_calls=[{"id": "c2", "name": "t2", "args": {}}],
                duration_ms=100,
                usage={"total": 10},
            ),
        ],
    )
    c_cassette = Cassette(
        cassette_id="cand",
        interactions=[
            CassetteInteraction(
                interaction_id="1",
                messages=[],
                response_text="A",
                tool_calls=[{"id": "c1", "name": "t1", "args": {}}],
                duration_ms=50,
                usage={"total": 5},
            )
        ],
    )

    report = compare_cassettes(b_cassette, c_cassette)
    assert report.total_cases == 2
    assert report.matches == 1
    assert report.accuracy == 0.5
    assert len(report.details) == 2
    assert report.details[1].get("missing") == "candidate"


def test_compare_cassettes_rejects_different_tool_arguments() -> None:
    """Nomes iguais com argumentos diferentes não são a mesma execução."""
    baseline = Cassette(
        cassette_id="base",
        interactions=[
            CassetteInteraction(
                interaction_id="1",
                messages=[],
                tool_calls=[{"id": "a", "name": "read_file", "args": {"path": "a"}}],
            )
        ],
    )
    candidate = Cassette(
        cassette_id="candidate",
        interactions=[
            CassetteInteraction(
                interaction_id="1",
                messages=[],
                tool_calls=[
                    {"id": "different", "name": "read_file", "args": {"path": "b"}}
                ],
            )
        ],
    )

    report = compare_cassettes(baseline, candidate)

    assert report.matches == 0
    assert report.details[0]["tool_presence_match"] is True
    assert report.details[0]["tool_arguments_match"] is False


def test_compare_cassettes_rejects_divergent_response_without_tools() -> None:
    """A resposta divergente não pode produzir 100% de equivalência."""
    baseline = Cassette(
        cassette_id="base",
        interactions=[
            CassetteInteraction(interaction_id="1", messages=[], response_text="A")
        ],
    )
    candidate = Cassette(
        cassette_id="candidate",
        interactions=[
            CassetteInteraction(interaction_id="1", messages=[], response_text="B")
        ],
    )

    report = compare_cassettes(baseline, candidate)

    assert report.matches == 0
    assert report.accuracy == 0.0
    assert report.details[0]["response_match"] is False


def test_compare_cassettes_rejects_duplicate_interaction_ids() -> None:
    """Duplicate IDs must not be silently aligned as separate cases."""
    cassette = Cassette(
        cassette_id="dup",
        interactions=[
            CassetteInteraction(interaction_id="same", messages=[]),
            CassetteInteraction(interaction_id="same", messages=[]),
        ],
    )
    with pytest.raises(ValueError, match="duplicado"):
        compare_cassettes(cassette, Cassette(cassette_id="other"))


def test_total_tokens_helper():
    """Verifica o comportamento de total tokens helper."""
    assert _total_tokens({}) == 0
    assert _total_tokens({"total": 120}) == 120
    assert _total_tokens({"total_tokens": 150}) == 150
    assert _total_tokens({"input": 40, "output": 60}) == 100


def test_cli_eval_compare(tmp_path: Path, capsys):
    """Verifica o comportamento de cli eval compare."""
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


def test_cli_eval_run(tmp_path: Path, capsys):
    """Verifica o comportamento de cli eval run."""
    suite_path = tmp_path / "suite.json"
    cassette = Cassette(
        cassette_id="my-suite",
        interactions=[
            CassetteInteraction(
                interaction_id="int-1",
                messages=[{"role": "user", "content": "olá"}],
                response_text="olá mundo",
                duration_ms=10,
                usage={"total": 5},
            )
        ],
    )
    cassette.save(suite_path)

    # Execução em modo human
    args = argparse.Namespace(
        action="run",
        suite=str(suite_path),
        output="human",
    )
    code = handle_eval(args)
    assert code == 0
    captured = capsys.readouterr()
    assert "Suite 'my-suite' executada com sucesso" in captured.out

    # Execução em modo json
    args_json = argparse.Namespace(
        action="run",
        suite=str(suite_path),
        output="json",
    )
    code_json = handle_eval(args_json)
    assert code_json == 0
    captured_json = capsys.readouterr()
    assert '"suite_id": "my-suite"' in captured_json.out


def test_cli_eval_missing_suite(capsys):
    """Verifica o comportamento de cli eval missing suite."""
    args = argparse.Namespace(
        action="run",
        suite=None,
        output="human",
    )
    code = handle_eval(args)
    assert code == 1
    captured = capsys.readouterr()
    assert "obrigatório" in captured.err


def test_cli_eval_corrupted_file(tmp_path: Path, capsys):
    """Verifica o comportamento de cli eval corrupted file."""
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{bad json", encoding="utf-8")

    args = argparse.Namespace(
        action="run",
        suite=str(corrupt),
        output="human",
    )
    code = handle_eval(args)
    assert code == 1
    captured = capsys.readouterr()
    assert "Erro ao carregar cassete" in captured.err


@pytest.mark.parametrize(
    "raw", ["[]", "null", "42", '{"cassette_id":"x","redaction_version":2}']
)
def test_cassette_load_rejects_invalid_shape_and_redaction_version(
    tmp_path, raw
) -> None:
    """Loading untrusted JSON must fail with a validation error, never AttributeError."""
    source = tmp_path / "invalid.json"
    source.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        Cassette.load(source)


@pytest.mark.parametrize("action", ["run", "compare"])
@pytest.mark.parametrize("raw", ["[]", '{"cassette_id":"x","redaction_version":2}'])
def test_cli_reports_invalid_cassette_without_traceback(
    tmp_path, capsys, action, raw
) -> None:
    """Both CLI entry points explain invalid cassettes and return failure."""
    source = tmp_path / "invalid.json"
    source.write_text(raw, encoding="utf-8")
    args = argparse.Namespace(
        action=action,
        suite=str(source),
        baseline=str(source),
        candidate=str(source),
    )
    assert handle_eval(args) == 1
    output = capsys.readouterr()
    assert "Erro ao carregar" in output.err
    assert "Traceback" not in output.err
    assert (
        "objeto JSON" if raw == "[]" else "redaction_version não suportada"
    ) in output.err
