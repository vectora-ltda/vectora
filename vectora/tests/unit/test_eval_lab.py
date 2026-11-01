"""Testes unitários para o Evaluation Lab, ReplayChatClient e comparação de cassetes."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from backend.cli.eval import handle_eval
from backend.evaluation.cassette import Cassette, CassetteInteraction
from backend.evaluation.replay_client import ReplayChatClient
from backend.evaluation.runner import _total_tokens, compare_cassettes
from backend.vtypes import JsonObject
from backend.vtypes.message import MessageRole, text_message


def test_export_run_cli_preserves_provenance_and_rejects_invalid_recording(
    tmp_path, capsys
):
    from backend.persistence.native.agent_run import AgentRun

    interaction = CassetteInteraction(
        interaction_id="one",
        run_id="run-export",
        messages=[],
        response_text="api_key=private-export-key",
    )
    run = AgentRun(
        run_id="run-export",
        thread_id="thread-export",
        evidence=[
            {
                "type": "evaluation_interaction",
                "interaction": interaction.model_dump(mode="json"),
            }
        ],
    )
    source = tmp_path / "run.json"
    target = tmp_path / "cassette.json"
    source.write_text(run.model_dump_json(), encoding="utf-8")
    args = argparse.Namespace(
        action="export-run", run_file=str(source), target=str(target)
    )
    assert handle_eval(args) == 0
    exported = Cassette.load(target)
    assert exported.source_run_id == run.run_id
    assert "private-export-key" not in target.read_text(encoding="utf-8")
    assert exported.interactions[0].reproducible is False
    previous = target.read_bytes()
    run.evidence[0]["interaction"] = {
        **interaction.model_dump(mode="json"),
        "run_id": "other-run",
    }
    source.write_text(run.model_dump_json(), encoding="utf-8")
    assert handle_eval(args) == 1
    assert target.read_bytes() == previous
    assert "another run" in capsys.readouterr().err
    run.evidence[0].pop("interaction")
    source.write_text(run.model_dump_json(), encoding="utf-8")
    assert handle_eval(args) == 1
    assert "missing or invalid" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_cli_replay_preserves_tool_history_without_external_calls():
    from backend.cli.eval import _replay_suite

    cassette = Cassette(
        cassette_id="tool-history",
        interactions=[
            CassetteInteraction(
                interaction_id="after-tool",
                messages=[
                    {
                        "role": "assistant",
                        "content": [],
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "name": "file_read",
                                "args": {"path": "a.txt"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "content": [{"kind": "text", "text": "recorded content"}],
                        "tool_call_id": "call-1",
                        "name": "file_read",
                    },
                ],
                response_text="recorded final response",
            )
        ],
    )
    results = await _replay_suite(cassette)
    assert results[0]["response_text"] == "recorded final response"
    cassette.interactions[0].messages[0]["role"] = "invalid-role"
    with pytest.raises(ValueError):
        await _replay_suite(cassette)


@pytest.mark.parametrize("missing_side", ["baseline", "candidate"])
def test_comparison_rejects_missing_decision_evidence_on_one_side(missing_side):
    recorded = Cassette(
        cassette_id="recorded",
        interactions=[
            CassetteInteraction(
                interaction_id="decision",
                messages=[],
                response_text="ok",
                systemone_decision_evidence={
                    "decision": "allow",
                    "policy_digest": "policy",
                },
            )
        ],
    )
    missing = recorded.model_copy(deep=True)
    missing.interactions[0].systemone_decision_evidence = None
    baseline, candidate = (
        (missing, recorded) if missing_side == "baseline" else (recorded, missing)
    )
    report = compare_cassettes(baseline, candidate)
    assert report.accuracy == 0
    assert report.details[0]["decision_evidence_match"] is False


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
                systemone_decision="allow api_key=private-decision-key",
                duration_ms=120,
                usage={"input": 50, "output": 10},
            )
        ],
    )
    cassette.save(target)

    # Verifica que o arquivo persistido redigiu o segredo
    raw_content = target.read_text(encoding="utf-8")
    assert "sk-1234567890abcdef12345678" not in raw_content
    assert "private-decision-key" not in raw_content
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
                systemone_decision_evidence={
                    "decision": "allow",
                    "provider": "clef-api",
                    "surface": "hitl",
                    "policy_digest": "sha256:policy",
                },
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
                systemone_decision_evidence={
                    "decision": "allow",
                    "provider": "clef-api",
                    "surface": "hitl",
                    "policy_digest": "sha256:policy",
                },
            )
        ],
    )

    report = compare_cassettes(b_cassette, c_cassette)
    assert report.total_cases == 1
    assert report.matches == 1
    assert report.accuracy == 1.0
    assert report.avg_latency_reduction_pct == 90.0
    assert report.token_savings_pct == 80.0
    assert report.details[0]["candidate_decision_evidence"] == {
        "decision": "allow",
        "provider": "clef-api",
        "surface": "hitl",
        "policy_digest": "sha256:policy",
    }


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


def test_compare_cassettes_detects_decision_evidence_divergence() -> None:
    """A changed policy or decision must be visible in evaluation accuracy."""
    baseline = Cassette(
        cassette_id="base",
        interactions=[
            CassetteInteraction(
                interaction_id="same",
                messages=[],
                response_text="approved",
                systemone_decision_evidence={
                    "run_id": "run-old",
                    "decision_id": "decision-old",
                    "decision": "allow",
                    "policy_digest": "sha256:policy-a",
                    "surface": "hitl",
                },
            )
        ],
    )
    candidate = Cassette(
        cassette_id="candidate",
        interactions=[
            CassetteInteraction(
                interaction_id="same",
                messages=[],
                response_text="approved",
                systemone_decision_evidence={
                    "run_id": "run-new",
                    "decision_id": "decision-new",
                    "decision": "allow",
                    "policy_digest": "sha256:policy-b",
                    "surface": "hitl",
                },
            )
        ],
    )

    report = compare_cassettes(baseline, candidate)

    assert report.matches == 0
    assert report.accuracy == 0.0
    assert report.details[0]["decision_evidence_match"] is False
    baseline_evidence = report.details[0]["baseline_decision_evidence"]
    assert isinstance(baseline_evidence, dict)
    assert baseline_evidence.get("policy_digest") == "sha256:policy-a"


def test_compare_cassettes_ignores_decision_execution_identity() -> None:
    """Different run IDs do not make otherwise identical decisions diverge."""
    baseline = Cassette(
        cassette_id="base",
        interactions=[
            CassetteInteraction(
                interaction_id="same",
                messages=[],
                systemone_decision_evidence={
                    "run_id": "run-old",
                    "decision_id": "decision-old",
                    "decision": "requires_human",
                    "policy_digest": "sha256:policy",
                },
            )
        ],
    )
    candidate = Cassette(
        cassette_id="candidate",
        interactions=[
            CassetteInteraction(
                interaction_id="same",
                messages=[],
                systemone_decision_evidence={
                    "run_id": "run-new",
                    "decision_id": "decision-new",
                    "decision": "requires_human",
                    "policy_digest": "sha256:policy",
                },
            )
        ],
    )

    report = compare_cassettes(baseline, candidate)

    assert report.matches == 1
    assert report.details[0]["decision_evidence_match"] is True


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


def test_cassette_preserves_structured_decision_evidence_and_redacts_secrets(
    tmp_path: Path,
) -> None:
    """Decision provenance survives round-trip without persisting credentials."""
    path = tmp_path / "decision.json"
    evidence: JsonObject = {
        "decision": "requires_human",
        "provider": "clef-api",
        "model": "decision-v1",
        "confidence": 0.42,
        "policy_digest": "sha256:policy",
        "surface": "hitl",
        "reason": "low_confidence",
        "response_redacted": {"authorization": "Bearer secret-token"},
    }
    Cassette(
        cassette_id="decision-run",
        source_run_id="run-1",
        interactions=[
            CassetteInteraction(
                interaction_id="tool-1",
                messages=[],
                run_id="run-1",
                systemone_decision="requires_human",
                systemone_decision_evidence=evidence,
            )
        ],
    ).save(path)

    raw = path.read_text(encoding="utf-8")
    assert "secret-token" not in raw
    loaded = Cassette.load(path)
    interaction = loaded.interactions[0]
    assert interaction.run_id == loaded.source_run_id == "run-1"
    assert interaction.systemone_decision == "requires_human"
    assert interaction.systemone_decision_evidence is not None
    assert interaction.systemone_decision_evidence["decision"] == "requires_human"
    assert interaction.systemone_decision_evidence["policy_digest"] == "sha256:policy"
    assert "[REDACTED]" in str(
        interaction.systemone_decision_evidence["response_redacted"]
    )


def test_cassette_load_keeps_legacy_decision_string_compatible(
    tmp_path: Path,
) -> None:
    """Legacy cassettes without structured provenance remain loadable."""
    path = tmp_path / "legacy.json"
    path.write_text(
        '{"cassette_id":"legacy","interactions":[{"interaction_id":"i1",'
        '"messages":[],"systemone_decision":"allow"}]}',
        encoding="utf-8",
    )
    interaction = Cassette.load(path).interactions[0]
    assert interaction.systemone_decision == "allow"
    assert interaction.systemone_decision_evidence is None


def test_total_tokens_helper():
    """Verifica o comportamento de total tokens helper."""
    assert _total_tokens({}) == 0
    assert _total_tokens({"total": 120}) == 120
    assert _total_tokens({"total_tokens": 150}) == 150
    assert _total_tokens({"input": 40, "output": 60}) == 100


def test_cli_eval_compare(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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


def test_cli_eval_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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

    # `--suite` aponta para um único cassette; uma lista de cassettes é recusada.
    list_path = tmp_path / "list.json"
    list_path.write_text(
        f"[{suite_path.read_text(encoding='utf-8')}]", encoding="utf-8"
    )
    code_list = handle_eval(
        argparse.Namespace(action="run", suite=str(list_path), output="human")
    )
    assert code_list == 1
    assert "Erro ao carregar cassete" in capsys.readouterr().err


def test_cli_eval_missing_suite(capsys: pytest.CaptureFixture[str]) -> None:
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


def test_cli_eval_corrupted_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    tmp_path: Path, raw: str
) -> None:
    """Loading untrusted JSON must fail with a validation error, never AttributeError."""
    source = tmp_path / "invalid.json"
    source.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        Cassette.load(source)


@pytest.mark.parametrize("action", ["run", "compare"])
@pytest.mark.parametrize("raw", ["[]", '{"cassette_id":"x","redaction_version":2}'])
def test_cli_reports_invalid_cassette_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    action: str,
    raw: str,
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


def test_cli_compare_reports_incomparable_cassettes_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Loadable cassettes that cannot be compared fail with a clear message."""
    interaction = CassetteInteraction(
        interaction_id="1", messages=[], response_text="A", tool_calls=[]
    )
    valid = tmp_path / "valid.json"
    duplicated = tmp_path / "duplicated.json"
    valid.write_text(
        Cassette(cassette_id="valid", interactions=[interaction]).model_dump_json(),
        encoding="utf-8",
    )
    duplicated.write_text(
        Cassette(
            cassette_id="duplicated", interactions=[interaction, interaction]
        ).model_dump_json(),
        encoding="utf-8",
    )

    def _compare(candidate: Path) -> int:
        return handle_eval(
            argparse.Namespace(
                action="compare", baseline=str(valid), candidate=str(candidate)
            )
        )

    assert _compare(valid) == 0
    capsys.readouterr()

    assert _compare(duplicated) == 1
    output = capsys.readouterr()
    assert "Erro ao comparar cassetes" in output.err
    assert "interaction_id duplicado" in output.err
    assert "Traceback" not in output.err
    assert output.out == ""


@pytest.mark.asyncio
async def test_recorded_run_export_survives_store_restart_and_session_purge(tmp_path):
    from backend.evaluation.recording import cassette_from_run, record_interaction
    from backend.persistence.native.agent_run import AgentRun
    from backend.persistence.native.session_store import SessionStore
    from backend.storage.sqlite.pool import AsyncConnectionPool
    from backend.vtypes.message import MessageRole, text_message

    path = str(tmp_path / "recording.db")
    pool = AsyncConnectionPool(path, min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.create_session("recorded-thread", user_id="owner")
    await store.upsert_agent_run(
        AgentRun(run_id="recorded-run", thread_id="recorded-thread", user_id="owner")
    )
    await record_interaction(
        store,
        run_id="recorded-run",
        messages=[text_message(MessageRole.USER, "hello")],
        response="recorded",
        tool_calls=[],
        usage=None,
        model="fixture",
        duration_ms=1,
        policy_digest="snapshot",
    )
    await pool.close()
    reopened = AsyncConnectionPool(path, min_size=1, max_size=2)
    await reopened.open()
    try:
        durable = SessionStore(reopened)
        run = await durable.get_agent_run("recorded-run")
        assert run is not None
        cassette = cassette_from_run(run)
        assert cassette.interactions[0].response_text == "recorded"
        from backend.cli.eval import _replay_suite

        replay = await _replay_suite(cassette)
        assert replay[0]["response_text"] == "recorded"
        await durable.delete_session("recorded-thread")
        assert await durable.get_agent_run("recorded-run") is None
    finally:
        await reopened.close()
