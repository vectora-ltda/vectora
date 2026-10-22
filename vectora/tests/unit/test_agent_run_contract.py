"""Testes unitários rigorosos do contrato persistente de AgentRun (Issue #368).

Cobre:
- Ciclo de vida e estados de execução, espera de provider, tools e recuperação.
- Persistência de campos obrigatórios (run_id, thread, usuário, workspace, modelo, provider, plan_step, tools, aprovações, arquivos, comandos, testes, artefatos, métricas, evidências).
- Redação rigorosa de dados sensíveis antes de persistir (tokens, chaves sk-, ghp-, AKIA, senhas).
- Sobrevivência a restart / reidratação exata sem perda de dados.
- Correlação de timeline e consultas de runs por thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast

import pytest

from backend.persistence.native.agent_run import (
    AgentRun,
    AgentRunMetrics,
    AgentRunStatus,
    redact_sensitive_data,
)
from backend.persistence.native.ledger import LedgerEventType, make_tool_started_key
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore]:
    pool = AsyncConnectionPool(
        str(tmp_path / "sessions_test.db"), min_size=1, max_size=2
    )
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    try:
        yield store
    finally:
        await pool.close()


class TestRedaction:
    def test_redact_sensitive_data_strings(self) -> None:
        fake_sk = f"sk-{'1234567890abcdef12345678'}"
        fake_ghp = f"ghp_{'1234567890' * 3}123456"
        fake_akia = f"AKIA{'IOSFODNN7EXAMPLE'}"
        secret_text = (
            f"{fake_sk} e {fake_ghp} e {fake_akia} "
            'export API_KEY="super secret token with spaces" and KEY=secret_value'
        )
        redacted = redact_sensitive_data(secret_text)
        assert fake_sk not in redacted
        assert fake_ghp not in redacted
        assert fake_akia not in redacted
        assert "super secret token with spaces" not in redacted
        assert "secret_value" not in redacted
        assert "[REDACTED]" in redacted

    def test_redact_sensitive_data_dict_keys(self) -> None:
        payload = {
            "api_key": "minha-chave-secreta",
            "x-auth-token": "meu-token-secreto",
            "db-password": "senha-super-forte",
            "normal_field": "conteudo livre",
            "nested": {
                "token": "token-1234",
                "safe": "ok",
            },
        }
        res = redact_sensitive_data(payload)
        assert res["api_key"] == "[REDACTED]"
        assert res["x-auth-token"] == "[REDACTED]"
        assert res["db-password"] == "[REDACTED]"
        assert res["normal_field"] == "conteudo livre"
        assert res["nested"]["token"] == "[REDACTED]"
        assert res["nested"]["safe"] == "ok"


class TestAgentRunLifecycle:
    @pytest.mark.parametrize(
        "status", ["admitted", "waiting_provider", "tool_running", "recovery_required"]
    )
    async def test_extended_runtime_statuses_are_persisted(
        self, store: SessionStore, status: str
    ) -> None:
        run = AgentRun(
            run_id=f"run-{status}",
            thread_id="thread-1",
            status=cast("AgentRunStatus", status),
        )
        await store.create_session("thread-1", user_id="user-1")
        await store.upsert_agent_run(run)
        loaded = await store.get_agent_run(run.run_id)
        assert loaded is not None
        assert loaded.status == status

    async def test_create_and_read_agent_run(self, store: SessionStore) -> None:
        await store.create_session("thread-run-1", user_id="user-123")
        run = AgentRun(
            run_id="run-uuid-1",
            thread_id="thread-run-1",
            turn_id="turn-1",
            user_id="user-123",
            workspace_id="ws-abc",
            model="gemini-2.5-pro",
            provider="google_genai",
            status="planned",
            plan_step="Etapa 1: Iniciar scaffolding",
            tools_available=["file_read", "terminal"],
            metrics=AgentRunMetrics(
                input_tokens=100, output_tokens=20, total_tokens=120
            ),
        )
        await store.upsert_agent_run(run)

        loaded = await store.get_agent_run("run-uuid-1")
        assert loaded is not None
        assert loaded.run_id == "run-uuid-1"
        assert loaded.thread_id == "thread-run-1"
        assert loaded.user_id == "user-123"
        assert loaded.workspace_id == "ws-abc"
        assert loaded.model == "gemini-2.5-pro"
        assert loaded.provider == "google_genai"
        assert loaded.status == "planned"
        assert loaded.plan_step == "Etapa 1: Iniciar scaffolding"
        assert loaded.tools_available == ["file_read", "terminal"]
        assert loaded.metrics.total_tokens == 120

    @pytest.mark.parametrize(
        ("terminal_status", "expected_legacy_status"),
        [
            ("passed", "completed"),
            ("degraded", "completed"),
            ("failed", "failed"),
            ("cancelled", "cancelled"),
        ],
    )
    async def test_state_transitions_canonical(
        self, store: SessionStore, terminal_status: str, expected_legacy_status: str
    ) -> None:
        thread_id = f"thread-run-{terminal_status}"
        run_id = f"run-uuid-{terminal_status}"
        await store.create_session(thread_id, user_id="user-123")
        run = AgentRun(
            run_id=run_id,
            thread_id=thread_id,
            status="planned",
        )
        await store.upsert_agent_run(run)

        # planned -> running
        await store.update_agent_run_status(
            run_id, status="running", plan_step="Executando"
        )
        r1 = await store.get_agent_run(run_id)
        assert r1 is not None and r1.status == "running"
        assert r1.terminal_at is None

        # running -> waiting_approval
        await store.update_agent_run_status(
            run_id, status="waiting_approval", detail="Esperando HITL"
        )
        r2 = await store.get_agent_run(run_id)
        assert r2 is not None and r2.status == "waiting_approval"

        # waiting_approval -> verifying
        await store.update_agent_run_status(run_id, status="verifying")
        r3 = await store.get_agent_run(run_id)
        assert r3 is not None and r3.status == "verifying"

        # verifying -> terminal_status
        await store.update_agent_run_status(
            run_id,
            status=cast("AgentRunStatus", terminal_status),
            detail=f"Concluído com {terminal_status}",
        )
        r4 = await store.get_agent_run(run_id)
        assert r4 is not None and r4.status == terminal_status
        assert r4.terminal_at is not None

        # Idempotência terminal: nova chamada terminal é no-op, preservando status, terminal_at e updated_at
        orig_terminal_at = r4.terminal_at
        orig_updated_at = r4.updated_at
        await store.update_agent_run_status(
            run_id,
            status="failed",
            detail="Tentativa de sobrescrever estado terminal",
        )
        r5 = await store.get_agent_run(run_id)
        assert r5 is not None
        assert r5.status == terminal_status
        assert r5.terminal_at == orig_terminal_at
        assert r5.updated_at == orig_updated_at

        # Confirma que session_runs reflete o status legado mapeado e respeita o CHECK
        async with store._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT status, terminal_at FROM session_runs WHERE run_id = ?",
                (run_id,),
            )
            legacy_row = await cur.fetchone()
            assert legacy_row is not None
            assert legacy_row[0] == expected_legacy_status
            assert legacy_row[1] is not None

    async def test_record_evidences_and_redaction(self, store: SessionStore) -> None:
        await store.create_session("thread-run-3", user_id="user-123")
        run = AgentRun(
            run_id="run-uuid-3",
            thread_id="thread-run-3",
            status="running",
        )
        await store.upsert_agent_run(run)

        fake_sk = f"sk-{'1234567890abcdef12345678'}"
        fake_token = f"ghp_{'1234567890' * 3}123456"

        # Registra evidências com segredo para verificar redação automática
        await store.record_run_evidence(
            "run-uuid-3",
            tool_used="terminal",
            command={
                "cmd": f"curl -H 'Authorization: Bearer {fake_sk}' https://api.exemplo.com"
            },
            evidence={"log": f"Token {fake_token} detectado"},
            artifact={"path": "report.json", "api_key": "segredo-chave"},
            file_touched={"path": "src/main.py", "lines_changed": 15},
        )

        loaded = await store.get_agent_run("run-uuid-3")
        assert loaded is not None
        assert "terminal" in loaded.tools_used
        assert len(loaded.commands_run) == 1
        cmd_str = str(loaded.commands_run[0].get("cmd"))
        assert fake_sk not in cmd_str
        assert "[REDACTED]" in cmd_str

        assert len(loaded.evidence) == 1
        log_str = str(loaded.evidence[0].get("log"))
        assert fake_token not in log_str
        assert "[REDACTED]" in log_str

        assert len(loaded.artifacts) == 1
        assert loaded.artifacts[0].get("api_key") == "[REDACTED]"
        assert len(loaded.files_touched) == 1
        assert loaded.files_touched[0].get("path") == "src/main.py"

    async def test_record_run_evidence_atomic(self, store: SessionStore) -> None:
        await store.create_session("thread-atomic", user_id="user-123")
        run = AgentRun(
            run_id="run-atomic-1",
            thread_id="thread-atomic",
            status="running",
        )
        await store.upsert_agent_run(run)

        async def _record(idx: int) -> None:
            await store.record_run_evidence(
                "run-atomic-1",
                tool_used=f"tool_{idx}",
                evidence={"step": idx},
            )

        await asyncio.gather(*[_record(i) for i in range(5)])

        loaded = await store.get_agent_run("run-atomic-1")
        assert loaded is not None
        assert len(loaded.tools_used) == 5
        assert len(loaded.evidence) == 5

    async def test_rehydrate_after_restart(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "restart_test.db")
        # 1. Abre pool, cria e encerra
        pool1 = AsyncConnectionPool(db_path, min_size=1, max_size=2)
        await pool1.open()
        store1 = SessionStore(pool1)
        await store1.setup()

        await store1.create_session("thread-restart", user_id="user-bob")
        await store1.upsert_agent_run(
            AgentRun(
                run_id="run-persist-1",
                thread_id="thread-restart",
                user_id="user-bob",
                model="gpt-4o",
                status="waiting_approval",
                plan_step="Ponto exato de aprovação",
            )
        )
        await pool1.close()

        # 2. Simula restart do backend criando um novo pool sobre o mesmo arquivo de DB
        pool2 = AsyncConnectionPool(db_path, min_size=1, max_size=2)
        await pool2.open()
        store2 = SessionStore(pool2)
        await store2.setup()

        rehydrated = await store2.get_agent_run("run-persist-1")
        assert rehydrated is not None
        assert rehydrated.run_id == "run-persist-1"
        assert rehydrated.status == "waiting_approval"
        assert rehydrated.plan_step == "Ponto exato de aprovação"
        assert rehydrated.user_id == "user-bob"
        await pool2.close()

    async def test_timeline_correlation(self, store: SessionStore) -> None:
        await store.create_session("thread-run-timeline", user_id="user-123")
        run_id = "run-corr-1"
        await store.upsert_agent_run(
            AgentRun(
                run_id=run_id,
                thread_id="thread-run-timeline",
                status="running",
            )
        )

        # Adiciona eventos ao ledger correlacionados com esse run_id
        await store.append_ledger_event(
            "thread-run-timeline",
            event_key="evt-1",
            event_type=LedgerEventType.AGENT_TURN_STARTED,
            run_id=run_id,
            payload={"step": 1},
        )
        await store.append_ledger_event(
            "thread-run-timeline",
            event_key=make_tool_started_key(run_id, "tc-1"),
            event_type=LedgerEventType.TOOL_STARTED,
            run_id=run_id,
            tool_call_id="tc-1",
            payload={"tool_name": "bash"},
        )

        timeline = await store.get_agent_run_timeline(run_id)
        assert len(timeline) == 2
        assert timeline[0].type == "agent_turn_started"
        assert timeline[1].type == "tool_started"
        assert timeline[1].payload.get("tool_name") == "bash"
