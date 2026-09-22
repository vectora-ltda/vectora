"""Tests for backend/scheduling/background_tasks.py.

Cobre o ciclo de vida das tarefas em segundo plano session-scoped: CRUD, execução
real do agente via o motor nativo (run_task/resume_background_run rodam sobre
`backend/engine/conversation_loop.py`), scheduler de interval e a ponte
webhook→IA. Cada caminho feliz tem o par de erro/borda no mesmo teste.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import backend
from backend.engine.hitl import ApprovalGate
from backend.persistence.native.session_store import SessionStore
from backend.scheduling import background_tasks as bg
from backend.services import agent_factory
from backend.services.agent_factory import NativeAgent
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.context import ToolContext
from backend.tools.registry import ToolRegistry
from backend.vtypes.message import ToolCallChunk, VMessageChunk

_SCHEMA = (
    Path(backend.__file__).parent / "storage" / "migrations" / "sqlite" / "schema.sql"
)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """Banco SQLite temporário com o schema único aplicado.

    `_get_db` abre uma conexão nova por chamada (igual à produção); todas apontam
    para o mesmo arquivo, então o estado persiste entre operações.
    """
    db_path = str(tmp_path / "bg.db")
    up_sql = _SCHEMA.read_text(encoding="utf-8")

    import aiosqlite

    async def _connect() -> Any:
        conn: Any = await aiosqlite.connect(db_path)
        conn.row_factory = lambda c, r: dict(
            zip([col[0] for col in c.description], r, strict=False)
        )
        return conn

    setup = await _connect()
    await setup.executescript(up_sql)
    await setup.commit()
    await setup.close()

    monkeypatch.setattr(bg, "_get_db", _connect)
    # threads.py (upsert/increment/list de sessions) compartilha o mesmo banco.
    monkeypatch.setattr("backend.api.handlers.threads._get_db", _connect)
    return db_path


@pytest.fixture(autouse=True)
def _pro_tier_by_default(monkeypatch):
    """Tarefas `webhook` exigem tier pro (`create_task`); os testes deste
    arquivo cobrem o resto do ciclo de vida, não o gating em si — default
    pro aqui, `TestWebhookTaskRequiresPro` abaixo testa o gating de verdade.
    """
    monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")


@pytest.fixture
async def native_session_store(tmp_path):
    """``SessionStore`` real (sqlite à parte do banco de tasks/runs) — a
    mesma infraestrutura que ``agent_factory.get_session_store()`` devolve
    em produção, isolada por teste."""
    pool = AsyncConnectionPool(
        str(tmp_path / "native-sessions.db"), min_size=1, max_size=2
    )
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    try:
        yield store
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Fakes do motor nativo, usados pra simular run_conversation nos testes.
# ---------------------------------------------------------------------------


def _require_spec(name: str) -> Any:
    """`TOOL_REGISTRY.get(name)` sem o `| None` — os testes deste arquivo só
    chamam isto depois de garantir que a tool foi registrada (`@vtool`),
    então um `None` aqui é bug do próprio teste, não caso de borda real."""
    from backend.tools.registry import TOOL_REGISTRY

    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        msg = f"tool '{name}' não registrada — setup do teste está errado"
        raise AssertionError(msg)
    return spec


def _texto_chunk(texto: str) -> VMessageChunk:
    return VMessageChunk(delta_text=texto)


def _tool_call_chunk(*, index: int, id: str, name: str, args: str) -> VMessageChunk:  # noqa: A002
    return VMessageChunk(
        tool_call_chunks=[
            ToolCallChunk(index=index, id=id, name=name, args_fragment=args)
        ]
    )


class _ScriptedChatClient:
    """Cliente de chat fake — cada `astream` consome o próximo turno
    pré-roteirizado. Com `exc`, toda chamada levanta a exceção dada em vez
    de produzir chunks (simula o provider fora do ar)."""

    def __init__(
        self,
        turnos: list[list[VMessageChunk]] | None = None,
        *,
        exc: Exception | None = None,
    ) -> None:
        self._turnos = turnos or []
        self.chamadas = 0
        self._exc = exc

    async def astream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        self.chamadas += 1
        if self._exc is not None:
            raise self._exc
        turno = self._turnos[self.chamadas - 1]
        for chunk in turno:
            yield chunk

    async def agenerate(
        self, messages, *, tools=None, temperature=None, max_tokens=None
    ):
        msg = "não usado (astream-only)"
        raise NotImplementedError(msg)


class _RecordingChatClientFactory:
    """Substitui ``FallbackChatClient`` em ``background_tasks.py`` —
    registra o ``primary_model_id`` de cada chamada e devolve o próximo
    cliente roteirizado da lista (o último se a lista for mais curta que o
    número de chamadas)."""

    def __init__(self, clients: Any) -> None:
        self._clients = clients if isinstance(clients, list) else [clients]
        self.calls: list[str] = []

    def __call__(self, primary_model_id: str = "") -> Any:
        self.calls.append(primary_model_id)
        idx = min(len(self.calls) - 1, len(self._clients) - 1)
        return self._clients[idx]


def _patch_native_engine(
    monkeypatch,
    *,
    session_store: SessionStore,
    tool_registry: ToolRegistry | None = None,
    subagent_catalog: dict[str, Any] | None = None,
    system_prompt: str = "system prompt de teste",
    chat_client: Any = None,
) -> _RecordingChatClientFactory:
    """Substitui as dependências que `run_task`/`resume_background_run`
    resolvem via `agent_factory` (motor nativo) + o construtor de
    `FallbackChatClient`."""
    native_agent = NativeAgent(
        tool_registry=tool_registry or ToolRegistry(),
        subagent_catalog=subagent_catalog or {},
        system_prompt=system_prompt,
    )

    async def _fake_get_native_agent(
        user_id: str | None = None,
        chat_mode: bool = False,
        workspace_id: str | None = None,
    ) -> NativeAgent:
        return native_agent

    async def _fake_get_session_store() -> SessionStore:
        return session_store

    approval_gate = ApprovalGate(session_store)

    async def _fake_get_approval_gate() -> ApprovalGate:
        return approval_gate

    async def _fake_get_store() -> None:
        return None

    monkeypatch.setattr(agent_factory, "get_native_agent", _fake_get_native_agent)
    monkeypatch.setattr(agent_factory, "get_session_store", _fake_get_session_store)
    monkeypatch.setattr(agent_factory, "get_approval_gate", _fake_get_approval_gate)
    monkeypatch.setattr(agent_factory, "get_store", _fake_get_store)

    factory = _RecordingChatClientFactory(
        chat_client
        if chat_client is not None
        else _ScriptedChatClient([[_texto_chunk("ok")]])
    )
    monkeypatch.setattr(bg, "FallbackChatClient", factory)
    return factory


# ---------------------------------------------------------------------------
# CRUD + validação
# ---------------------------------------------------------------------------


async def test_create_task_persists_then_validation_rejects_bad_input(db):
    task = await bg.create_task(
        session_id="sess-1",
        user_id="uuid-aaa",
        kind="routine",
        name="Resumo diário",
        instruction="Resuma as mudanças do dia",
        trigger_type="interval",
        trigger_config={"cron_expr": "0 9 * * *"},
        workspace_id="ws1",
    )
    assert task.id
    assert task.next_run_at is not None  # interval agenda o próximo horário
    listed = await bg.list_tasks("sess-1")
    assert [t.id for t in listed] == [task.id]
    got = await bg.get_task(task.id)
    assert got is not None
    assert got.name == "Resumo diário"

    # Erro/borda no mesmo teste: kind/trigger/cron inválidos devem falhar.
    with pytest.raises(ValueError):
        await bg.create_task(
            session_id="sess-1",
            user_id="uuid-aaa",
            kind="invalido",
            name="x",
            instruction="x",
            trigger_type="interval",
            trigger_config={"cron_expr": "0 9 * * *"},
        )
    with pytest.raises(ValueError):
        await bg.create_task(
            session_id="sess-1",
            user_id="uuid-aaa",
            kind="routine",
            name="x",
            instruction="x",
            trigger_type="cosmico",
            trigger_config={},
        )
    with pytest.raises(ValueError):
        await bg.create_task(
            session_id="sess-1",
            user_id="uuid-aaa",
            kind="routine",
            name="x",
            instruction="x",
            trigger_type="interval",
            trigger_config={"cron_expr": "isso não é cron"},
        )


async def test_create_task_priority_default_e_persistida(db):
    """Prioridade é sinal visual do card do Kanban, propagada de ponta a
    ponta (default 'normal' se omitida)."""
    default_task = await bg.create_task(
        session_id="sess-1",
        user_id="uuid-aaa",
        kind="routine",
        name="sem prioridade explícita",
        instruction="x",
        trigger_type="manual",
    )
    assert default_task.priority == "normal"

    urgent_task = await bg.create_task(
        session_id="sess-1",
        user_id="uuid-aaa",
        kind="routine",
        name="com prioridade",
        instruction="x",
        trigger_type="manual",
        priority="urgent",
    )
    assert urgent_task.priority == "urgent"
    got = await bg.get_task(urgent_task.id)
    assert got is not None
    assert got.priority == "urgent"

    # Erro/borda: prioridade fora da taxonomia é rejeitada antes de gravar.
    with pytest.raises(ValueError):
        await bg.create_task(
            session_id="sess-1",
            user_id="uuid-aaa",
            kind="routine",
            name="x",
            instruction="x",
            trigger_type="manual",
            priority="critica-demais",
        )


async def test_update_task_priority(db):
    task = await bg.create_task(
        session_id="s",
        user_id="u",
        kind="routine",
        name="x",
        instruction="i",
        trigger_type="manual",
    )
    assert task.priority == "normal"

    updated = await bg.update_task(task.id, priority="high")
    assert updated is not None
    assert updated.priority == "high"

    # Erro/borda: prioridade inválida no update também é rejeitada.
    with pytest.raises(ValueError):
        await bg.update_task(task.id, priority="nao-existe")


async def test_update_task_agent_profile_id_e_atribuivel_e_desatribuivel(db):
    """O drawer precisa trocar o assignee depois da
    criação, não só no formulário de nova tarefa. `None` explícito
    precisa funcionar (desatribuir), então `update_task` distingue "campo
    omitido" de "campo passado como None" pela PRESENÇA da chave em
    `updates`, não por `is not None`."""
    task = await bg.create_task(
        session_id="s",
        user_id="u",
        kind="routine",
        name="x",
        instruction="i",
        trigger_type="manual",
    )
    assert task.agent_profile_id is None

    atribuida = await bg.update_task(task.id, agent_profile_id="perfil-1")
    assert atribuida is not None
    assert atribuida.agent_profile_id == "perfil-1"

    # Erro/borda: None explícito desatribui — se `update_task` filtrasse
    # por `is not None` como os outros campos, isto silenciosamente NÃO
    # faria nada, e o assignee ficaria preso pra sempre.
    desatribuida = await bg.update_task(task.id, agent_profile_id=None)
    assert desatribuida is not None
    assert desatribuida.agent_profile_id is None


async def test_update_and_delete_roundtrip(db):
    task = await bg.create_task(
        session_id="s",
        user_id="u",
        kind="heartbreak",
        name="orig",
        instruction="i",
        trigger_type="webhook",
        trigger_config={"provider": "github", "events": ["push"]},
    )
    updated = await bg.update_task(task.id, name="novo", enabled=False)
    assert updated is not None
    assert updated.name == "novo"
    assert updated.enabled is False

    # Erro/borda: atualizar task inexistente devolve None; delete idem False.
    assert await bg.update_task("nope") is None
    assert await bg.delete_task(task.id) is True
    assert await bg.delete_task(task.id) is False
    assert await bg.get_task(task.id) is None


# ---------------------------------------------------------------------------
# Gating: task com trigger_type="webhook" exige tier pro
# ---------------------------------------------------------------------------


class TestWebhookTaskRequiresPro:
    async def test_free_tier_e_rejeitada_com_402(self, db, monkeypatch, tmp_path):
        monkeypatch.delenv("VECTORA_LICENSE_BYPASS", raising=False)
        from backend.services import license as lic

        monkeypatch.setattr(lic, "CACHE_PATH", tmp_path / "license_cache.json")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await bg.create_task(
                session_id="s",
                user_id="u",
                kind="routine",
                name="sync",
                instruction="i",
                trigger_type="webhook",
                trigger_config={"provider": "github", "events": ["push"]},
            )
        assert exc.value.status_code == 402

    async def test_pro_tier_cria_normalmente(self, db, monkeypatch):
        monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
        task = await bg.create_task(
            session_id="s",
            user_id="u",
            kind="routine",
            name="sync",
            instruction="i",
            trigger_type="webhook",
            trigger_config={"provider": "github", "events": ["push"]},
        )
        assert task.trigger_type == "webhook"

    async def test_outros_triggers_nao_exigem_pro(self, db, monkeypatch):
        """manual/interval/once/subagent seguem livres — só webhook é pago."""
        monkeypatch.delenv("VECTORA_LICENSE_BYPASS", raising=False)
        task = await bg.create_task(
            session_id="s",
            user_id="u",
            kind="routine",
            name="manual-task",
            instruction="i",
            trigger_type="manual",
        )
        assert task.trigger_type == "manual"


# ---------------------------------------------------------------------------
# Quota por workspace
# ---------------------------------------------------------------------------


async def test_quota_por_workspace_bloqueia_apos_o_limite_mas_nao_bloqueia_manual(
    db, monkeypatch
):
    monkeypatch.setattr(bg, "MAX_SCHEDULED_TASKS_PER_WORKSPACE", 2)

    await bg.create_task(
        session_id="s",
        user_id="u",
        kind="routine",
        name="t1",
        instruction="i",
        trigger_type="interval",
        trigger_config={"cron_expr": "0 9 * * *"},
        workspace_id="ws-quota",
    )
    await bg.create_task(
        session_id="s",
        user_id="u",
        kind="routine",
        name="t2",
        instruction="i",
        trigger_type="interval",
        trigger_config={"cron_expr": "0 10 * * *"},
        workspace_id="ws-quota",
    )

    with pytest.raises(ValueError, match="limite"):
        await bg.create_task(
            session_id="s",
            user_id="u",
            kind="routine",
            name="t3",
            instruction="i",
            trigger_type="interval",
            trigger_config={"cron_expr": "0 11 * * *"},
            workspace_id="ws-quota",
        )

    # Erro/borda: 'manual' nunca conta pra quota, mesmo workspace já saturado.
    manual = await bg.create_task(
        session_id="s",
        user_id="u",
        kind="routine",
        name="t-manual",
        instruction="i",
        trigger_type="manual",
        workspace_id="ws-quota",
    )
    assert manual.id

    # Outro workspace não compete pela mesma quota.
    other = await bg.create_task(
        session_id="s",
        user_id="u",
        kind="routine",
        name="t-outro-ws",
        instruction="i",
        trigger_type="interval",
        trigger_config={"cron_expr": "0 12 * * *"},
        workspace_id="ws-outro",
    )
    assert other.id


# ---------------------------------------------------------------------------
# Timezone do usuário
# ---------------------------------------------------------------------------


def test_next_run_usa_timezone_do_usuario_nao_utc(monkeypatch):
    """Cron é interpretado no fuso do usuário: "todo dia às 9h" pra alguém em
    GMT-3 dispara às 12h UTC, não às 9h UTC (que seria 6h da manhã pra ele)."""
    from datetime import datetime

    class _FakeSettings:
        user_timezone = "America/Sao_Paulo"

    monkeypatch.setattr(
        "backend.workspace.runtime_settings.runtime_settings", _FakeSettings()
    )

    result = bg._next_run("0 9 * * *")

    assert result is not None
    parsed = datetime.fromisoformat(result)
    # Armazenado em UTC, mas representando 9h em São Paulo (UTC-3).
    assert parsed.hour == 12

    # Erro/borda: timezone inexistente não derruba o agendamento — cai no
    # fuso local com aviso, em vez de propagar ZoneInfoNotFoundError.
    class _BrokenSettings:
        user_timezone = "Marte/Olympus_Mons"

    monkeypatch.setattr(
        "backend.workspace.runtime_settings.runtime_settings", _BrokenSettings()
    )
    fallback = bg._next_run("0 9 * * *")
    assert fallback is not None


def test_next_run_sem_timezone_configurado_usa_local_do_so(monkeypatch):
    class _NoTz:
        user_timezone = ""

    monkeypatch.setattr("backend.workspace.runtime_settings.runtime_settings", _NoTz())
    assert bg._next_run("0 9 * * *") is not None

    # Erro/borda: cron inválido continua retornando None — o timezone não
    # transforma um erro de parse em exceção.
    assert bg._next_run("isso não é cron") is None


# ---------------------------------------------------------------------------
# Catch-up throttle (disparo atrasado)
# ---------------------------------------------------------------------------


def test_is_stale_so_marca_atraso_alem_da_janela_de_tolerancia():
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)

    # Atrasado muito além da janela (processo ficou horas parado) — pula.
    antigo = (now - timedelta(hours=5)).isoformat()
    assert bg._is_stale(antigo, now) is True

    # Happy path: atraso pequeno (dentro da tolerância) ainda dispara.
    recente = (now - timedelta(minutes=2)).isoformat()
    assert bg._is_stale(recente, now) is False

    # Erro/borda: timestamp ausente ou ilegível nunca é tratado como
    # atrasado — na dúvida executa, não engole a tarefa em silêncio.
    assert bg._is_stale(None, now) is False
    assert bg._is_stale("não é timestamp", now) is False


async def test_tick_pula_interval_atrasada_mas_executa_once_atrasada(db, monkeypatch):
    from datetime import UTC, datetime, timedelta

    executed: list[str] = []

    async def _fake_run_task(task, trigger, payload=None):
        executed.append(task.id)

    monkeypatch.setattr(bg, "run_task", _fake_run_task)

    muito_atrasado = (datetime.now(UTC) - timedelta(hours=6)).isoformat()

    recorrente = await bg.create_task(
        session_id="s",
        user_id="u",
        kind="routine",
        name="diária atrasada",
        instruction="i",
        trigger_type="interval",
        trigger_config={"cron_expr": "0 9 * * *"},
        next_run_at=muito_atrasado,
    )
    unica = await bg.create_task(
        session_id="s",
        user_id="u",
        kind="routine",
        name="única atrasada",
        instruction="i",
        trigger_type="once",
        next_run_at=muito_atrasado,
    )

    await bg.BackgroundScheduler().tick()

    # A recorrente atrasada é pulada e reagendada pro futuro...
    assert recorrente.id not in executed
    reagendada = await bg.get_task(recorrente.id)
    assert reagendada is not None
    assert reagendada.next_run_at is not None
    assert datetime.fromisoformat(reagendada.next_run_at) > datetime.now(UTC)

    # ...mas a execução única atrasada ainda roda (erro/borda: pular aqui
    # perderia uma tarefa que o usuário pediu explicitamente uma vez).
    assert unica.id in executed


# ---------------------------------------------------------------------------
# run_task — execução real do agente (motor nativo)
# ---------------------------------------------------------------------------


async def test_run_task_invokes_agent_registers_session_and_records_run(
    db, native_session_store, monkeypatch
):
    client = _ScriptedChatClient([[_texto_chunk("Tudo certo hoje.")]])
    _patch_native_engine(
        monkeypatch, session_store=native_session_store, chat_client=client
    )

    upserts: list[dict[str, Any]] = []

    async def _fake_upsert(thread_id, title=None, workspace_id=None):
        upserts.append(
            {"thread_id": thread_id, "title": title, "workspace_id": workspace_id}
        )

    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _fake_upsert)

    task = await bg.create_task(
        session_id="sess-run",
        user_id="uuid-bbb",
        kind="routine",
        name="Check",
        instruction="Verifique o repo",
        trigger_type="manual",
        trigger_config={},
        workspace_id="ws9",
    )

    run_thread_id = await bg.run_task(task, "manual")

    assert run_thread_id is not None
    assert run_thread_id.startswith(f"bg-{task.id}-")
    assert client.chamadas == 1
    # A run criou uma thread visível na sidebar com título e workspace certos.
    assert upserts == [
        {"thread_id": run_thread_id, "title": "Rotina: Check", "workspace_id": "ws9"}
    ]
    runs = await bg.list_runs("sess-run")
    assert len(runs) == 1
    assert runs[0]["status"] == "done"
    assert runs[0]["summary"] == "Tudo certo hoje."
    after = await bg.get_task(task.id)
    assert after is not None
    assert after.last_run_at is not None

    # A run também gravou sua conversa em SessionStore, sob o run_thread_id.
    historico = await native_session_store.get_history(run_thread_id)
    assert [m.role.value for m in historico] == ["system", "user", "assistant"]
    assert historico[-1].text() == "Tudo certo hoje."


async def test_run_task_watchdog_cancela_ao_final_sem_deixar_task_pendente(
    db, native_session_store, monkeypatch
):
    """O watchdog de heartbeat roda no `finally` de
    `run_task`, cobrindo todo caminho de saída (aqui, sucesso). Sem o
    cancelamento, uma task asyncio ficaria viva pra sempre depois de cada
    run — vazamento silencioso a cada tarefa executada."""
    client = _ScriptedChatClient([[_texto_chunk("ok")]])
    _patch_native_engine(
        monkeypatch, session_store=native_session_store, chat_client=client
    )
    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", AsyncMock())

    task = await bg.create_task(
        session_id="sess-watchdog",
        user_id="uuid-bbb",
        kind="routine",
        name="Watchdog",
        instruction="i",
        trigger_type="manual",
    )

    antes = {t for t in asyncio.all_tasks() if not t.done()}
    await bg.run_task(task, "manual")
    depois = {t for t in asyncio.all_tasks() if not t.done()}

    novas = depois - antes
    assert not any("_heartbeat_watchdog" in repr(t) for t in novas), (
        "watchdog não foi cancelado — vazou uma task asyncio viva"
    )


async def test_run_task_heartbeat_e_chamado_periodicamente_durante_execucao_longa(
    db, native_session_store, monkeypatch
):
    """Reproduz o bug real: sem o watchdog, uma run que passa do TTL do
    claim (900s) enquanto ainda genuinamente executando seria devolvida
    pra `ready` por `release_stale_claims()` no tick seguinte do
    scheduler — permitindo reclaim/execução duplicada da MESMA task.
    Prova que `heartbeat_claim` é chamado de verdade (não mais código
    morto) enquanto a run está em andamento, com o mesmo `task_id`."""
    import backend.scheduling.background_tasks as bg_mod

    class _SlowChatClient:
        chamadas = 0

        async def astream(
            self, messages, *, tools=None, temperature=None, max_tokens=None
        ):
            _SlowChatClient.chamadas += 1
            await asyncio.sleep(0.3)
            yield _texto_chunk("devagar mas certo")

        async def agenerate(self, *a, **kw):
            raise NotImplementedError

    _patch_native_engine(
        monkeypatch, session_store=native_session_store, chat_client=_SlowChatClient()
    )
    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", AsyncMock())
    # Watchdog dispara bem mais rápido que o real (60s) — só pra caber num
    # teste de segundos sem mockar o event loop inteiro.
    monkeypatch.setattr(bg_mod, "_HEARTBEAT_INTERVAL_S", 0.05)

    from backend.scheduling.kanban import heartbeat_claim as _heartbeat_claim_real

    chamadas_heartbeat: list[tuple[str, str]] = []

    async def _spy_heartbeat(task_id: str, run_id: str, *, ttl_s: int = 900) -> bool:
        chamadas_heartbeat.append((task_id, run_id))
        return await _heartbeat_claim_real(task_id, run_id, ttl_s=ttl_s)

    # `_heartbeat_watchdog` importa `heartbeat_claim` localmente (padrão do
    # arquivo, evita import circular) — o patch precisa mirar o módulo de
    # origem (`kanban`), não `background_tasks`, que nunca tem esse nome no
    # próprio namespace.
    monkeypatch.setattr("backend.scheduling.kanban.heartbeat_claim", _spy_heartbeat)

    task = await bg.create_task(
        session_id="sess-heartbeat-longo",
        user_id="uuid-bbb",
        kind="routine",
        name="Devagar",
        instruction="i",
        trigger_type="manual",
    )

    await bg_mod.run_task(task, "manual")

    assert _SlowChatClient.chamadas == 1
    assert len(chamadas_heartbeat) >= 1, (
        "heartbeat_claim nunca foi chamado durante uma run de 0.3s com "
        "watchdog de 0.05s — o watchdog não está disparando"
    )
    assert all(tid == task.id for tid, _run_id in chamadas_heartbeat)


async def test_run_task_com_requires_review_termina_em_review_nao_done(
    db, native_session_store, monkeypatch
):
    """`trigger_config.requires_review` desvia o fim de
    uma run bem-sucedida pra `review` em vez de `done` direto."""
    client = _ScriptedChatClient([[_texto_chunk("Pronto pra revisão.")]])
    _patch_native_engine(
        monkeypatch, session_store=native_session_store, chat_client=client
    )
    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", AsyncMock())

    task = await bg.create_task(
        session_id="sess-review",
        user_id="uuid-bbb",
        kind="routine",
        name="Precisa revisão",
        instruction="i",
        trigger_type="manual",
        trigger_config={"requires_review": True},
    )

    await bg.run_task(task, "manual")

    from backend.scheduling.kanban import get_task_status

    assert (await get_task_status(task.id))["status"] == "review"


async def test_run_task_sem_requires_review_termina_em_done_como_antes(
    db, native_session_store, monkeypatch
):
    """Erro/borda: sem o campo (ou `False`), o comportamento pré-existente
    não muda — `done` direto, mesma regressão que quebraria qualquer task
    manual/webhook/once já em produção."""
    client = _ScriptedChatClient([[_texto_chunk("Feito.")]])
    _patch_native_engine(
        monkeypatch, session_store=native_session_store, chat_client=client
    )
    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", AsyncMock())

    task = await bg.create_task(
        session_id="sess-no-review",
        user_id="uuid-bbb",
        kind="routine",
        name="Sem revisão",
        instruction="i",
        trigger_type="manual",
    )

    await bg.run_task(task, "manual")

    from backend.scheduling.kanban import get_task_status

    assert (await get_task_status(task.id))["status"] == "done"


async def test_run_task_subagent_type_usa_soul_tool_registry_com_o_usuario_da_task(
    db, native_session_store, monkeypatch
):
    """Regressão do gap real (WS8): a execução de uma task com
    `trigger_config={"subagent_type": ...}` (agendada por
    `schedule_subagent_task`) precisa filtrar as tools da SOUL pelo
    `user_id` da task (`_soul_tool_registry`) — diferente da delegação
    síncrona, que já filtrava corretamente."""
    client = _ScriptedChatClient([[_texto_chunk("feito")]])
    _patch_native_engine(
        monkeypatch, session_store=native_session_store, chat_client=client
    )

    chamadas: list[tuple[str, str | None]] = []
    original = bg._soul_tool_registry

    def _spy(soul, user_id):
        chamadas.append((soul.name, user_id))
        return original(soul, user_id)

    monkeypatch.setattr(bg, "_soul_tool_registry", _spy)

    task = await bg.create_task(
        session_id="sess-sub",
        user_id="uuid-owner",
        kind="routine",
        name="Subagente coder",
        instruction="corrigir bug",
        trigger_type="once",
        trigger_config={"subagent_type": "coder"},
        next_run_at="2026-01-01T00:00:00+00:00",
    )

    await bg.run_task(task, "manual")

    assert chamadas == [("coder", "uuid-owner")]

    # Erro/borda: subagent_type fora do catálogo vira erro tratado (run
    # 'error'), nunca exceção crua propagada pro scheduler/runner.
    task_invalido = await bg.create_task(
        session_id="sess-sub",
        user_id="uuid-owner",
        kind="routine",
        name="SOUL inexistente",
        instruction="x",
        trigger_type="manual",
        trigger_config={"subagent_type": "nao-existe"},
    )
    resultado = await bg.run_task(task_invalido, "manual")
    assert resultado is None
    runs = await bg.list_runs("sess-sub")
    erro = next(r for r in runs if r["task_id"] == task_invalido.id)
    assert erro["status"] == "error"
    assert "subagent_type inválido" in erro["summary"]


async def test_run_task_isola_worktree_so_quando_a_soul_precisa_e_ha_workspace(
    db, native_session_store, monkeypatch
):
    """Gap real (revisão de 2026-08-30): nenhum teste exercitava o branch
    `soul.needs_worktree_isolation` (background_tasks.py:933) — a task de
    uma SOUL que edita filesystem/git (ex. `coder`, `needs_worktree_isolation
    =True`) precisa isolar num worktree quando a task tem `workspace_id`;
    uma SOUL read-only/sem filesystem (ex. `planner`, `False`) nunca deve
    chamar isso, mesmo com workspace_id setado."""
    client = _ScriptedChatClient([[_texto_chunk("feito")], [_texto_chunk("feito")]])
    _patch_native_engine(
        monkeypatch, session_store=native_session_store, chat_client=client
    )

    chamadas: list[tuple[str, str]] = []

    async def _fake_worktree_workspace_id(workspace_id: str, task_id: str) -> str:
        chamadas.append((workspace_id, task_id))
        return "ws-isolado-fake"

    monkeypatch.setattr(bg, "_worktree_workspace_id", _fake_worktree_workspace_id)

    task_coder = await bg.create_task(
        session_id="sess-worktree",
        user_id="uuid-owner",
        kind="routine",
        name="Subagente coder isolado",
        instruction="corrigir bug",
        trigger_type="once",
        trigger_config={"subagent_type": "coder"},
        workspace_id="ws-principal",
        next_run_at="2026-01-01T00:00:00+00:00",
    )
    await bg.run_task(task_coder, "manual")
    assert chamadas == [("ws-principal", task_coder.id)]

    # Erro/borda: SOUL sem needs_worktree_isolation (planner) não isola,
    # mesmo com workspace_id presente — não deve chamar de novo.
    task_planner = await bg.create_task(
        session_id="sess-worktree",
        user_id="uuid-owner",
        kind="routine",
        name="Subagente planner sem isolamento",
        instruction="planejar",
        trigger_type="once",
        trigger_config={"subagent_type": "planner"},
        workspace_id="ws-principal",
        next_run_at="2026-01-01T00:00:00+00:00",
    )
    await bg.run_task(task_planner, "manual")
    assert chamadas == [("ws-principal", task_coder.id)]  # não cresceu


async def test_run_task_error_path_records_error_and_skips_session(
    db, native_session_store, monkeypatch
):
    client = _ScriptedChatClient(exc=RuntimeError("LLM caiu"))
    _patch_native_engine(
        monkeypatch, session_store=native_session_store, chat_client=client
    )

    called: list[Any] = []

    async def _fake_upsert(*a, **k):
        called.append((a, k))

    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _fake_upsert)

    task = await bg.create_task(
        session_id="sess-err",
        user_id="u",
        kind="heartbreak",
        name="X",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )

    result = await bg.run_task(task, "manual")

    assert result is None  # falha não propaga, vira None
    assert called == []  # não registra thread visível se o agente falhou
    runs = await bg.list_runs("sess-err")
    assert runs[0]["status"] == "error"
    assert "LLM caiu" in runs[0]["summary"]


# ---------------------------------------------------------------------------
# Visibilidade da thread da run na sidebar (ListThreads)
# ---------------------------------------------------------------------------


async def test_run_task_incrementa_message_count_da_thread(
    db, native_session_store, monkeypatch
):
    """A run bem-sucedida chama _increment_message_count(run_thread_id) — é o
    que faz ListThreads (filtra message_count>0) mostrá-la na sidebar.

    Testa o wiring com mock (o caminho real de I/O de sessão publica eventos
    que não isolam por event-loop nos testes; ListThreads+increment reais têm
    cobertura própria em test_threads_*)."""
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        chat_client=_ScriptedChatClient([[_texto_chunk("feito")]]),
    )

    upserts: list[str] = []
    increments: list[str] = []

    async def _fake_upsert(thread_id, title=None, workspace_id=None, mode=None):
        upserts.append(thread_id)

    async def _fake_increment(thread_id):
        increments.append(thread_id)

    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _fake_upsert)
    monkeypatch.setattr(
        "backend.api.handlers.threads._increment_message_count", _fake_increment
    )

    task = await bg.create_task(
        session_id="sess-vis",
        user_id="u",
        kind="routine",
        name="Visível",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
        workspace_id="ws1",
    )

    run_thread_id = await bg.run_task(task, "manual")
    assert run_thread_id is not None
    # A run registrou a thread E incrementou seu message_count (→ listável).
    assert upserts == [run_thread_id]
    assert increments == [run_thread_id]

    # Erro/borda: run que FALHA (agente levanta) nem registra nem incrementa —
    # thread sem conteúdo não polui a sidebar.
    upserts.clear()
    increments.clear()
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        chat_client=_ScriptedChatClient(exc=RuntimeError("boom")),
    )
    task2 = await bg.create_task(
        session_id="sess-vis",
        user_id="u",
        kind="routine",
        name="Falha",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    assert await bg.run_task(task2, "manual") is None
    assert upserts == []
    assert increments == []


# ---------------------------------------------------------------------------
# A run reporta o resultado de volta na sessão-mãe
# ---------------------------------------------------------------------------


async def test_run_task_reporta_conclusao_na_sessao_mae(
    db, native_session_store, monkeypatch
):
    """Ao concluir, a run posta uma mensagem ASSISTANT no histórico da sessão
    que criou a task (task.session_id) via SessionStore.append_message — o
    orquestrador principal fica sabendo que a tarefa terminou."""
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        chat_client=_ScriptedChatClient([[_texto_chunk("3 arquivos alterados")]]),
    )

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _noop)
    monkeypatch.setattr("backend.api.handlers.threads._increment_message_count", _noop)

    task = await bg.create_task(
        session_id="parent-sess",
        user_id="u",
        kind="routine",
        name="Auditoria",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    run_thread_id = await bg.run_task(task, "manual")
    assert run_thread_id is not None

    historico_mae = await native_session_store.get_history("parent-sess")
    assert len(historico_mae) == 1
    msg = historico_mae[0]
    assert msg.role.value == "assistant"
    assert "Auditoria" in msg.text()  # nome da task
    assert "3 arquivos alterados" in msg.text()  # resumo
    assert run_thread_id in msg.text()  # link pra thread da run


async def test_report_to_parent_session_pula_sem_sessao_mae(
    db, native_session_store, monkeypatch
):
    """Erro/borda: sem session_id (ou session_id == run_thread_id) não reporta
    — não há sessão-mãe distinta pra notificar."""
    _patch_native_engine(monkeypatch, session_store=native_session_store)

    task_sem = bg.BackgroundTask(
        id="t",
        session_id="",
        user_id="u",
        kind="routine",
        name="X",
        instruction="i",
        trigger_type="manual",
    )
    assert await bg.report_to_parent_session(task_sem, "bg-1", "resumo") is False

    task_self = bg.BackgroundTask(
        id="t",
        session_id="bg-1",
        user_id="u",
        kind="routine",
        name="X",
        instruction="i",
        trigger_type="manual",
    )
    assert await bg.report_to_parent_session(task_self, "bg-1", "resumo") is False
    assert await native_session_store.get_history("bg-1") == []


async def test_report_to_parent_session_tolera_falha_do_session_store(db, monkeypatch):
    """Erro/borda: SessionStore falhando (ex.: banco corrompido) não deve
    propagar — report_to_parent_session é best-effort e devolve False."""

    async def _boom_get_session_store():
        raise RuntimeError("SessionStore indisponível")

    monkeypatch.setattr(agent_factory, "get_session_store", _boom_get_session_store)

    task = bg.BackgroundTask(
        id="t-boom",
        session_id="parent-boom",
        user_id="u",
        kind="routine",
        name="X",
        instruction="i",
        trigger_type="manual",
    )
    ok = await bg.report_to_parent_session(task, "bg-boom", "resumo")
    assert ok is False


# ---------------------------------------------------------------------------
# HITL em background — interrupt → awaiting_approval → resume
# ---------------------------------------------------------------------------


async def test_run_task_interrupt_marks_awaiting_and_resume_completes(
    db, native_session_store, monkeypatch
):
    """Uma run que pausa em HITL fica 'awaiting_approval' (não 'done') e o
    resume_background_run a retoma até concluir."""
    from backend.tools.context import ToolContext
    from backend.tools.registry import TOOL_REGISTRY, ToolExtras, vtool

    if TOOL_REGISTRY.get("escrever_arquivo_bg") is None:

        @vtool(extras=ToolExtras(destructive=True))
        async def escrever_arquivo_bg(ctx: ToolContext) -> str:
            """escreve um arquivo (fake)."""
            return "arquivo escrito"

    registry = ToolRegistry()
    registry.register(_require_spec("escrever_arquivo_bg"))

    monkeypatch.setattr(bg, "should_require_approval", lambda *_a, **_k: True)

    client1 = _ScriptedChatClient(
        [
            [
                _tool_call_chunk(
                    index=0, id="call_1", name="escrever_arquivo_bg", args="{}"
                )
            ]
        ]
    )
    client2 = _ScriptedChatClient([[_texto_chunk("arquivo escrito")]])
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        tool_registry=registry,
        chat_client=[client1, client2],
    )

    async def _fake_upsert(thread_id, title=None, workspace_id=None):
        return None

    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _fake_upsert)

    task = await bg.create_task(
        session_id="sess-hitl",
        user_id="u-hitl",
        kind="routine",
        name="Escreve",
        instruction="Escreva um arquivo",
        trigger_type="manual",
        trigger_config={"permission_mode": "ask"},  # modo que interrompe
        workspace_id="ws-h",
    )

    run_thread_id = await bg.run_task(task, "manual")
    assert run_thread_id is not None  # thread visível mesmo pausada

    runs = await bg.list_runs("sess-hitl")
    assert len(runs) == 1
    assert runs[0]["status"] == "awaiting_approval"
    assert runs[0]["finished_at"] is None  # não terminou — aguarda aprovação
    assert "escrever_arquivo_bg" in runs[0]["summary"]
    run_id = runs[0]["id"]

    # Resume aprovando → conclui.
    status = await bg.resume_background_run(run_id, "approve")
    assert status == "done"
    assert client1.chamadas == 1
    assert client2.chamadas == 1

    runs2 = await bg.list_runs("sess-hitl")
    assert runs2[0]["status"] == "done"
    assert runs2[0]["summary"] == "arquivo escrito"

    from backend.scheduling import kanban

    estado = await kanban.get_task_status(task.id)
    assert estado["status"] == "done"
    assert estado["claim_lock"] is None
    assert estado["claim_expires_at"] is None

    historico = await native_session_store.get_history(run_thread_id)
    assert [m.role.value for m in historico[-3:]] == ["assistant", "tool", "assistant"]
    assert historico[-2].text() == "arquivo escrito"  # resultado real da tool


async def test_self_block_survives_success_epilogue(db: str) -> None:
    """Um bloqueio feito pela própria run não pode ser sobrescrito por done."""
    from backend.scheduling import kanban

    task = await bg.create_task(
        session_id="sess-self-block",
        user_id="u-self-block",
        kind="routine",
        name="Bloqueia",
        instruction="aguarde intervenção",
        trigger_type="manual",
        trigger_config={},
    )
    run_id = "run-self-block"
    assert await kanban.claim_task(task.id, run_id)
    await kanban.block_task(
        task.id,
        "needs_input",
        "aguardando credencial",
        authorized_run_id=run_id,
    )

    await bg._mark_kanban_after_success(task, run_id=run_id)

    estado = await kanban.get_task_status(task.id)
    assert estado["status"] == "blocked"
    assert estado["block_kind"] == "needs_input"
    assert estado["claim_lock"] is None


async def test_interval_success_advances_next_run_with_claim_release(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recurring success advances its occurrence before releasing the claim."""
    from backend.scheduling import kanban

    task = await bg.create_task(
        session_id="sess-interval-fence",
        user_id="u-interval-fence",
        kind="routine",
        name="Recorrente",
        instruction="execute",
        trigger_type="interval",
        trigger_config={"cron_expr": "0 9 * * *"},
        next_run_at="2026-09-21T09:00:00+00:00",
    )
    run_id = "run-interval-fence"
    assert await kanban.claim_task(task.id, run_id)
    monkeypatch.setattr(bg, "_next_run", lambda _expr: "2026-09-22T09:00:00+00:00")

    await kanban.set_status(task.id, "ready", authorized_run_id=run_id)

    updated = await bg.get_task(task.id)
    assert updated is not None
    assert updated.next_run_at == "2026-09-22T09:00:00+00:00"
    estado = await kanban.get_task_status(task.id)
    assert estado["claim_lock"] is None
    assert estado["claim_expires_at"] is None


async def test_interval_completion_reloads_concurrently_edited_schedule(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Completion follows a schedule edit that races its first calculation."""
    import json
    import sqlite3

    from backend.scheduling import kanban

    task = await bg.create_task(
        session_id="sess-interval-edit",
        user_id="u-interval-edit",
        kind="routine",
        name="Recorrente editada",
        instruction="execute",
        trigger_type="interval",
        trigger_config={"cron_expr": "0 9 * * *"},
        next_run_at="2026-09-21T09:00:00+00:00",
    )
    run_id = "run-interval-edit"
    assert await kanban.claim_task(task.id, run_id)
    calls = 0

    def _next_run(cron_expr: str | None) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "UPDATE vectora_background_tasks SET trigger_config = ?, "
                    "next_run_at = ? WHERE id = ?",
                    (
                        json.dumps({"cron_expr": "0 10 * * *"}),
                        "2026-09-21T10:00:00+00:00",
                        task.id,
                    ),
                )
                conn.commit()
            return "2026-09-22T09:00:00+00:00"
        assert cron_expr == "0 10 * * *"
        return "2026-09-22T10:00:00+00:00"

    monkeypatch.setattr(bg, "_next_run", _next_run)
    await kanban.set_status(task.id, "ready", authorized_run_id=run_id)

    updated = await bg.get_task(task.id)
    assert updated is not None
    assert updated.trigger_config["cron_expr"] == "0 10 * * *"
    assert updated.next_run_at == "2026-09-22T10:00:00+00:00"


async def test_resume_background_run_rejects_unknown_or_finished_run(
    db, native_session_store, monkeypatch
):
    """Erro/borda: resume de run inexistente ou já concluída devolve None sem
    tocar no motor nativo."""
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        chat_client=_ScriptedChatClient([[_texto_chunk("x")]]),
    )

    async def _fake_upsert(*a, **k):
        return None

    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _fake_upsert)

    # Run inexistente.
    assert await bg.resume_background_run("nao-existe", "approve") is None

    # Run que concluiu (status 'done') não é retomável.
    task = await bg.create_task(
        session_id="sess-fin",
        user_id="u",
        kind="routine",
        name="Feito",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    await bg.run_task(task, "manual")  # conclui 'done' (sem interrupt)
    done_run = (await bg.list_runs("sess-fin"))[0]
    assert done_run["status"] == "done"
    assert await bg.resume_background_run(done_run["id"], "approve") is None


async def _run_task_ate_pausar(
    monkeypatch, native_session_store, *, tool_name: str, session_id: str
) -> tuple[Any, str]:
    """Roda `run_task` até pausar em HITL (chamando `tool_name` sinalizada
    como destrutiva) e devolve `(task, run_id)` — helper compartilhado pelos
    testes de decision (reject/edit) de `resume_background_run`."""
    from backend.tools.context import ToolContext
    from backend.tools.registry import TOOL_REGISTRY, ToolExtras, vtool

    if TOOL_REGISTRY.get(tool_name) is None:

        async def _anotar(ctx: ToolContext, valor: str = "") -> str:
            """anota um valor (fake, registra em _CHAMADAS_ANOTAR)."""
            _CHAMADAS_ANOTAR.append(valor)
            return f"anotado: {valor}"

        # `vtool` lê `fn.__name__` na hora da decoração — renomeia ANTES de
        # decorar (chamada direta, sem `@`) pra registrar sob `tool_name`,
        # não sob o nome literal `_anotar` de todo teste que usa este helper.
        _anotar.__name__ = tool_name
        vtool(extras=ToolExtras(destructive=True))(_anotar)

    registry = ToolRegistry()
    registry.register(_require_spec(tool_name))
    monkeypatch.setattr(bg, "should_require_approval", lambda *_a, **_k: True)

    client = _ScriptedChatClient(
        [
            [
                _tool_call_chunk(
                    index=0,
                    id="call_1",
                    name=tool_name,
                    args='{"valor": "original"}',
                )
            ]
        ]
    )
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        tool_registry=registry,
        chat_client=client,
    )

    async def _fake_upsert(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _fake_upsert)

    task = await bg.create_task(
        session_id=session_id,
        user_id="u",
        kind="routine",
        name="Decisão",
        instruction="i",
        trigger_type="manual",
        trigger_config={"permission_mode": "ask"},
    )
    await bg.run_task(task, "manual")
    # `list_runs_for_task` (não `list_runs(session_id)`) — vários testes
    # reusam o mesmo `session_id` pra runs de tasks diferentes; filtrar por
    # task_id evita pegar a run errada quando duas pausam na mesma sessão.
    run_id = (await bg.list_runs_for_task(task.id))[0]["id"]
    return task, run_id


_CHAMADAS_ANOTAR: list[str] = []


async def test_resume_background_run_reject_and_edit_decisions(
    db, native_session_store, monkeypatch
):
    """decision='reject' não executa a tool (fica registrado como rejeição);
    decision='edit:<json>' executa com os args editados — ambos concluem."""
    _CHAMADAS_ANOTAR.clear()

    _task_reject, run_id_reject = await _run_task_ate_pausar(
        monkeypatch,
        native_session_store,
        tool_name="anotar_bg_reject",
        session_id="sess-reject",
    )
    # A `tool_registry` do resume precisa incluir a tool sinalizada (senão
    # `_execute_single_call` não a encontra) — reusa a mesma do pause.
    from backend.tools.registry import TOOL_REGISTRY

    registry_reject = ToolRegistry()
    registry_reject.register(_require_spec("anotar_bg_reject"))
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        tool_registry=registry_reject,
        chat_client=_ScriptedChatClient([[_texto_chunk("rejeitado")]]),
    )

    status = await bg.resume_background_run(run_id_reject, "reject")
    assert status == "done"
    assert _CHAMADAS_ANOTAR == []  # tool nunca executou

    runs_reject = await bg.list_runs("sess-reject")
    assert runs_reject[0]["summary"] == "rejeitado"

    _task_edit, run_id_edit = await _run_task_ate_pausar(
        monkeypatch,
        native_session_store,
        tool_name="anotar_bg_edit",
        session_id="sess-edit",
    )
    registry_edit = ToolRegistry()
    registry_edit.register(_require_spec("anotar_bg_edit"))
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        tool_registry=registry_edit,
        chat_client=_ScriptedChatClient([[_texto_chunk("editado")]]),
    )

    status2 = await bg.resume_background_run(run_id_edit, 'edit:{"valor": "editado"}')
    assert status2 == "done"
    assert _CHAMADAS_ANOTAR == ["editado"]  # executou com o valor editado


async def test_resume_background_run_invalid_decision_marks_error(
    db, native_session_store, monkeypatch
):
    """Erro/borda: decision desconhecida levanta ValueError, capturado pelo
    except geral — a run vira 'error' (não fica presa em awaiting_approval) e
    o resume devolve None."""
    _CHAMADAS_ANOTAR.clear()
    task, run_id = await _run_task_ate_pausar(
        monkeypatch,
        native_session_store,
        tool_name="anotar_bg_bad_decision",
        session_id="sess-bad-decision",
    )

    status = await bg.resume_background_run(run_id, "banana")
    assert status is None
    assert _CHAMADAS_ANOTAR == []  # nem chegou a resolver a pendência

    run = await bg._get_run(run_id)
    assert run is not None
    assert run["status"] == "error"
    assert "decision inválida" in run["summary"]

    from backend.scheduling import kanban

    estado = await kanban.get_task_status(task.id)
    assert estado["status"] == "blocked"
    assert estado["claim_lock"] is None


async def test_resume_background_run_repauses_same_turn(
    db, native_session_store, monkeypatch
):
    """Se o resume dispara OUTRA ação destrutiva no mesmo turno, a run continua
    'awaiting_approval' (não vira 'done') — retomável de novo."""
    _CHAMADAS_ANOTAR.clear()
    _task, run_id = await _run_task_ate_pausar(
        monkeypatch,
        native_session_store,
        tool_name="anotar_bg_repause",
        session_id="sess-repause",
    )
    from backend.tools.registry import TOOL_REGISTRY

    registry = ToolRegistry()
    registry.register(_require_spec("anotar_bg_repause"))
    # 2º turno também pede a mesma tool destrutiva → pausa de novo.
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        tool_registry=registry,
        chat_client=_ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0,
                        id="call_2",
                        name="anotar_bg_repause",
                        args='{"valor": "de novo"}',
                    )
                ]
            ]
        ),
    )

    status = await bg.resume_background_run(run_id, "approve")
    assert status == "awaiting_approval"

    run = await bg._get_run(run_id)
    assert run is not None
    assert run["status"] == "awaiting_approval"
    assert "anotar_bg_repause" in run["summary"]


async def test_resume_background_run_run_conversation_exception_marks_error(
    db, native_session_store, monkeypatch
):
    """Erro/borda: exceção durante o `run_conversation` pós-resume marca a
    run como 'error' (não fica presa em awaiting_approval) e devolve None."""
    _CHAMADAS_ANOTAR.clear()
    _task, run_id = await _run_task_ate_pausar(
        monkeypatch,
        native_session_store,
        tool_name="anotar_bg_resume_exc",
        session_id="sess-resume-exc",
    )
    from backend.tools.registry import TOOL_REGISTRY

    registry = ToolRegistry()
    registry.register(_require_spec("anotar_bg_resume_exc"))
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        tool_registry=registry,
        chat_client=_ScriptedChatClient(exc=RuntimeError("modelo indisponível")),
    )

    status = await bg.resume_background_run(run_id, "approve")
    assert status is None

    run = await bg._get_run(run_id)
    assert run is not None
    assert run["status"] == "error"
    assert "modelo indisponível" in run["summary"]


async def test_resume_background_run_reporta_conclusao_na_sessao_mae(
    db, native_session_store, monkeypatch
):
    """report_to_parent_session também é chamado a partir do resume (não só de
    run_task) — a sessão-mãe recebe o aviso de conclusão pós-aprovação."""
    _CHAMADAS_ANOTAR.clear()
    _task, run_id = await _run_task_ate_pausar(
        monkeypatch,
        native_session_store,
        tool_name="anotar_bg_report_resume",
        session_id="parent-resume",
    )
    from backend.tools.registry import TOOL_REGISTRY

    registry = ToolRegistry()
    registry.register(_require_spec("anotar_bg_report_resume"))
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        tool_registry=registry,
        chat_client=_ScriptedChatClient([[_texto_chunk("ação concluída")]]),
    )

    status = await bg.resume_background_run(run_id, "approve")
    assert status == "done"

    historico_mae = await native_session_store.get_history("parent-resume")
    assert len(historico_mae) == 1
    assert "ação concluída" in historico_mae[0].text()


async def test_describe_pending_approval_sem_pendencia_e_com_pendencia():
    """Erro/borda: sem pendência (`None`) cai no fallback genérico; com
    pendência, a descrição carrega o nome da tool sinalizada."""
    assert bg._describe_pending_approval(None) == "Aguardando aprovação."
    assert (
        bg._describe_pending_approval({"tool_name": "terminal"})
        == "Aguardando aprovação: terminal"
    )
    # tool_name vazio/ausente cai no rótulo genérico, não quebra.
    assert (
        bg._describe_pending_approval({"tool_name": ""})
        == "Aguardando aprovação: ação desconhecida"
    )


# ---------------------------------------------------------------------------
# Tools do orquestrador: listar/consultar tasks e runs
# ---------------------------------------------------------------------------


async def test_orchestrator_tools_list_status_result(db, monkeypatch):
    """list_background_tasks/get_task_status/get_task_result devolvem o estado
    das tasks e runs da sessão — o orquestrador principal fica sabendo o que
    está rodando/terminou. Erro/borda: task/run inexistente devolve erro
    tipado, não exceção (§11)."""
    import json as _json

    from backend.tools.background import (
        get_task_result,
        get_task_status,
        list_background_tasks,
    )
    from backend.tools.context import ToolContext

    task = await bg.create_task(
        session_id="sess-tools",
        user_id="u",
        kind="routine",
        name="Diária",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    # Registra uma run concluída manualmente (sem invocar o agente).
    run_id = "run-xyz"
    await bg._insert_run(run_id, task, "sess-tools", "manual")
    await bg._finish_run(run_id, "done", "3 itens processados")

    ctx = ToolContext(thread_id="sess-tools", user_id="u")

    listed = _json.loads(await list_background_tasks(ctx=ctx))
    assert listed["status"] == "ok"
    assert len(listed["tasks"]) == 1
    assert listed["tasks"][0]["task_id"] == task.id
    assert listed["tasks"][0]["last_run"]["status"] == "done"

    status = _json.loads(await get_task_status(task_id=task.id, ctx=ctx))
    assert status["status"] == "ok"
    assert any(r["run_id"] == run_id for r in status["runs"])

    result = _json.loads(await get_task_result(run_id=run_id, ctx=ctx))
    assert result["status"] == "ok"
    assert result["summary"] == "3 itens processados"

    # Erro/borda: ids inexistentes → erro tipado.
    bad_status = _json.loads(await get_task_status(task_id="nope", ctx=ctx))
    assert bad_status["status"] == "error"
    bad_result = _json.loads(await get_task_result(run_id="nope", ctx=ctx))
    assert bad_result["status"] == "error"


async def test_list_background_tasks_sem_session_id_e_task_sem_run(db, monkeypatch):
    """Erro/borda: contexto sem thread_id → erro tipado (sem exceção). Task que
    nunca rodou aparece com last_run=None (não quebra o dict de latest_by_task)."""
    import json as _json

    from backend.tools.background import list_background_tasks
    from backend.tools.context import ToolContext

    sem_thread = ToolContext(user_id="u")
    out_sem = _json.loads(await list_background_tasks(ctx=sem_thread))
    assert out_sem["status"] == "error"

    await bg.create_task(
        session_id="sess-no-run",
        user_id="u",
        kind="routine",
        name="Nunca rodou",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    ctx = ToolContext(thread_id="sess-no-run", user_id="u")
    out = _json.loads(await list_background_tasks(ctx=ctx))
    assert out["status"] == "ok"
    assert len(out["tasks"]) == 1
    assert out["tasks"][0]["last_run"] is None


# ---------------------------------------------------------------------------
# Intervenção: cancelar / aprovar via tool do orquestrador
# ---------------------------------------------------------------------------


async def test_cancel_and_approve_task_action(db, monkeypatch):
    """cancel_background_run encerra uma run pendente; a tool approve_task_action
    (decision='cancel') faz o mesmo pelo orquestrador. Erro/borda: cancelar uma
    run já concluída → None / erro tipado."""
    import json as _json

    from backend.tools.background import approve_task_action
    from backend.tools.context import ToolContext

    task = await bg.create_task(
        session_id="sess-cancel",
        user_id="u",
        kind="routine",
        name="Perigosa",
        instruction="i",
        trigger_type="manual",
        trigger_config={"permission_mode": "ask"},
    )
    run_id = "run-await"
    from backend.scheduling import kanban

    assert await kanban.claim_task(task.id, run_id)
    await bg._insert_run(run_id, task, "sess-cancel", "manual")
    await bg._mark_run_awaiting(run_id, "aguardando terminal")

    ctx = ToolContext(thread_id="sess-cancel", user_id="u")
    out = _json.loads(
        await approve_task_action(run_id=run_id, decision="cancel", ctx=ctx)
    )
    assert out["status"] == "ok"
    assert out["run_status"] == "cancelled"

    run = await bg._get_run(run_id)
    assert run is not None
    assert run["status"] == "cancelled"
    estado = await kanban.get_task_status(task.id)
    assert estado["status"] == "blocked"
    assert estado["block_kind"] == "needs_input"
    assert estado["claim_lock"] is None

    # Erro/borda: cancelar de novo (já não está pendente) → None / erro tipado.
    assert await bg.cancel_background_run(run_id) is None
    out2 = _json.loads(
        await approve_task_action(run_id=run_id, decision="cancel", ctx=ctx)
    )
    assert out2["status"] == "error"


async def test_approve_task_action_approve_reject_edit(
    db, native_session_store, monkeypatch
):
    """approve_task_action delega approve/reject/edit para resume_background_run
    (só 'cancel' tinha cobertura via tool até aqui)."""
    import json as _json

    from backend.tools.background import approve_task_action
    from backend.tools.context import ToolContext

    _CHAMADAS_ANOTAR.clear()

    _task, run_approve = await _run_task_ate_pausar(
        monkeypatch,
        native_session_store,
        tool_name="anotar_bg_tool_approve",
        session_id="sess-approve-tool",
    )
    from backend.tools.registry import TOOL_REGISTRY

    registry = ToolRegistry()
    registry.register(_require_spec("anotar_bg_tool_approve"))
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        tool_registry=registry,
        chat_client=_ScriptedChatClient([[_texto_chunk("ok")]]),
    )
    ctx = ToolContext(thread_id="sess-approve-tool", user_id="u")
    out_approve = _json.loads(
        await approve_task_action(run_id=run_approve, decision="approve", ctx=ctx)
    )
    assert out_approve == {"status": "ok", "run_status": "done"}

    _task_reject, run_reject = await _run_task_ate_pausar(
        monkeypatch,
        native_session_store,
        tool_name="anotar_bg_tool_reject",
        session_id="sess-approve-tool",
    )
    registry_reject = ToolRegistry()
    registry_reject.register(_require_spec("anotar_bg_tool_reject"))
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        tool_registry=registry_reject,
        chat_client=_ScriptedChatClient([[_texto_chunk("recusado")]]),
    )
    out_reject = _json.loads(
        await approve_task_action(run_id=run_reject, decision="reject", ctx=ctx)
    )
    assert out_reject == {"status": "ok", "run_status": "done"}

    _task_edit, run_edit = await _run_task_ate_pausar(
        monkeypatch,
        native_session_store,
        tool_name="anotar_bg_tool_edit",
        session_id="sess-approve-tool",
    )
    registry_edit = ToolRegistry()
    registry_edit.register(_require_spec("anotar_bg_tool_edit"))
    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        tool_registry=registry_edit,
        chat_client=_ScriptedChatClient([[_texto_chunk("editado")]]),
    )
    out_edit = _json.loads(
        await approve_task_action(
            run_id=run_edit, decision='edit:{"valor": "x"}', ctx=ctx
        )
    )
    assert out_edit == {"status": "ok", "run_status": "done"}
    assert "x" in _CHAMADAS_ANOTAR


# ---------------------------------------------------------------------------
# Scheduler (interval)
# ---------------------------------------------------------------------------


async def test_scheduler_runs_due_and_skips_disabled(db, monkeypatch):
    fired: list[str] = []
    from backend.scheduling import kanban

    async def _fake_run(task, trigger_source, payload=None):
        fired.append(task.id)
        run_id = f"run-{task.id}"
        assert await kanban.claim_task(task.id, run_id)
        await bg._mark_kanban_after_success(task, run_id=run_id)
        return "bg-x"

    monkeypatch.setattr(bg, "run_task", _fake_run)

    due = await bg.create_task(
        session_id="s",
        user_id="u",
        kind="routine",
        name="due",
        instruction="i",
        trigger_type="interval",
        trigger_config={"cron_expr": "* * * * *"},
    )
    # Força o vencimento (next_run_at no passado, mas dentro da janela de
    # catch-up — passado remoto seria pulado como disparo obsoleto).
    from datetime import UTC as _UTC
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    vencido = (_dt.now(_UTC) - _td(minutes=1)).isoformat()
    await bg._set_next_run(due.id, vencido)

    disabled = await bg.create_task(
        session_id="s",
        user_id="u",
        kind="routine",
        name="off",
        instruction="i",
        trigger_type="interval",
        trigger_config={"cron_expr": "* * * * *"},
    )
    await bg._set_next_run(disabled.id, vencido)
    await bg.update_task(disabled.id, enabled=False)

    await bg.get_scheduler().tick()

    assert fired == [due.id]  # só a vencida e habilitada roda
    # Reagendou para o futuro.
    rescheduled = await bg.get_task(due.id)
    assert rescheduled is not None
    assert rescheduled.next_run_at != vencido


# ---------------------------------------------------------------------------
# Webhook → IA
# ---------------------------------------------------------------------------


async def test_dispatch_webhook_matches_provider_and_event(db, monkeypatch):
    fired: list[tuple[str, str]] = []

    async def _fake_run(task, trigger_source, payload=None):
        fired.append((task.id, trigger_source))
        return "bg-x"

    monkeypatch.setattr(bg, "run_task", _fake_run)

    task = await bg.create_task(
        session_id="s",
        user_id="u",
        kind="heartbreak",
        name="ci",
        instruction="Analise o push",
        trigger_type="webhook",
        trigger_config={"provider": "github", "events": ["push"]},
    )

    # Casa: github + push.
    assert await bg.dispatch_webhook_event("github", "push", {"x": 1}) == 1
    assert fired == [(task.id, "webhook")]

    fired.clear()
    # Não casa: provider diferente.
    assert await bg.dispatch_webhook_event("gitlab", "push", {}) == 0
    # Não casa: evento fora do filtro.
    assert await bg.dispatch_webhook_event("github", "issues", {}) == 0
    assert fired == []


# ---------------------------------------------------------------------------
# GitHub Issues → Kanban (sync determinístico, sem LLM)
# ---------------------------------------------------------------------------


async def _make_issue_sync_anchor(**overrides: Any) -> Any:
    cfg = {"provider": "github", "events": ["issues"]}
    cfg.update(overrides.pop("trigger_config", {}))
    return await bg.create_task(
        session_id=overrides.pop("session_id", "s-issues"),
        user_id=overrides.pop("user_id", "u1"),
        kind="heartbreak",
        name="sync issues",
        instruction="",
        trigger_type="webhook",
        trigger_config=cfg,
        workspace_id=overrides.pop("workspace_id", "ws1"),
    )


async def test_issue_opened_creates_kanban_card_without_llm(db):
    await _make_issue_sync_anchor()

    card = await bg.sync_github_issue_to_kanban(
        "opened",
        "acme/repo",
        {"number": 42, "title": "Bug no login", "body": "Detalhes", "html_url": "u"},
    )

    assert card is not None
    assert card.name == "Bug no login"
    assert card.instruction == "Detalhes"
    assert card.trigger_type == "manual"
    assert card.trigger_config["source"] == "github_issue"
    assert card.trigger_config["repo"] == "acme/repo"
    assert card.trigger_config["issue_number"] == 42

    found = await bg.find_task_by_github_issue("s-issues", "acme/repo", 42)
    assert found is not None
    assert found.id == card.id

    # Borda: nenhuma task 'webhook' habilitada casando 'issues' — sync
    # desligado, não cria nada nem levanta.
    from backend.scheduling import background_tasks as bg_mod

    conn = await bg_mod._get_db()
    await conn.execute("UPDATE vectora_background_tasks SET enabled = 0")
    await conn.commit()
    await conn.close()
    assert (
        await bg.sync_github_issue_to_kanban(
            "opened", "acme/repo", {"number": 43, "title": "y"}
        )
        is None
    )


async def test_issue_opened_reentrega_atualiza_em_vez_de_duplicar(db):
    """Reentrega do mesmo webhook (mesmo issue_number/repo) não duplica o card."""
    await _make_issue_sync_anchor()

    primeiro = await bg.sync_github_issue_to_kanban(
        "opened", "acme/repo", {"number": 7, "title": "Título original", "body": "v1"}
    )
    segundo = await bg.sync_github_issue_to_kanban(
        "opened",
        "acme/repo",
        {"number": 7, "title": "Título atualizado", "body": "v2"},
    )

    assert primeiro is not None
    assert segundo is not None
    assert segundo.id == primeiro.id  # mesmo card, não duplicou
    assert segundo.name == "Título atualizado"
    assert segundo.instruction == "v2"

    tasks = await bg.list_tasks("s-issues")
    cards_da_issue = [t for t in tasks if t.trigger_config.get("issue_number") == 7]
    assert len(cards_da_issue) == 1

    # Borda: payload sem "number" não cria nada e não levanta.
    assert await bg.sync_github_issue_to_kanban("opened", "acme/repo", {}) is None


async def test_issue_closed_moves_card_to_done_without_llm(db, monkeypatch):
    calls: list[Any] = []
    monkeypatch.setattr(
        agent_factory, "get_native_agent", lambda *a, **k: calls.append((a, k))
    )

    await _make_issue_sync_anchor()
    card = await bg.sync_github_issue_to_kanban(
        "opened", "acme/repo", {"number": 9, "title": "Fechar isso"}
    )
    assert card is not None
    assert card.status == "ready"

    updated = await bg.sync_github_issue_to_kanban(
        "closed", "acme/repo", {"number": 9, "title": "Fechar isso"}
    )

    assert updated is not None
    assert updated.status == "done"
    assert calls == []  # nunca chamou o agente — caminho 100% determinístico

    # Borda: issue sem card correspondente (nunca chegou "opened") não faz nada.
    sem_card = await bg.sync_github_issue_to_kanban(
        "closed", "acme/repo", {"number": 999, "title": "x"}
    )
    assert sem_card is None


async def test_issue_reopened_moves_card_back_to_ready(db):
    await _make_issue_sync_anchor()
    card = await bg.sync_github_issue_to_kanban(
        "opened", "acme/repo", {"number": 11, "title": "Reabrir"}
    )
    assert card is not None
    await bg.sync_github_issue_to_kanban(
        "closed", "acme/repo", {"number": 11, "title": "Reabrir"}
    )

    reaberto = await bg.sync_github_issue_to_kanban(
        "reopened", "acme/repo", {"number": 11, "title": "Reabrir"}
    )

    assert reaberto is not None
    assert reaberto.status == "ready"

    # Borda: action desconhecida (ex.: "labeled") não tem efeito nem levanta.
    ignorado = await bg.sync_github_issue_to_kanban(
        "labeled", "acme/repo", {"number": 11, "title": "Reabrir"}
    )
    assert ignorado is None


async def test_issue_edited_updates_title_and_instruction_without_status_change(db):
    await _make_issue_sync_anchor()
    card = await bg.sync_github_issue_to_kanban(
        "opened", "acme/repo", {"number": 21, "title": "Antigo", "body": "antigo"}
    )
    assert card is not None
    status_antes = card.status

    editado = await bg.sync_github_issue_to_kanban(
        "edited",
        "acme/repo",
        {"number": 21, "title": "Novo título", "body": "novo corpo"},
    )

    assert editado is not None
    assert editado.name == "Novo título"
    assert editado.instruction == "novo corpo"
    assert editado.status == status_antes  # não mexeu no status


# ---------------------------------------------------------------------------
# Alertas de observabilidade → Kanban (sync determinístico, sem LLM)
# ---------------------------------------------------------------------------


async def _make_observability_sync_anchor(**overrides: Any) -> Any:
    cfg = {"provider": "observability"}
    cfg.update(overrides.pop("trigger_config", {}))
    return await bg.create_task(
        session_id=overrides.pop("session_id", "s-obs"),
        user_id=overrides.pop("user_id", "u1"),
        kind="heartbreak",
        name="sync observabilidade",
        instruction="",
        trigger_type="webhook",
        trigger_config=cfg,
        workspace_id=overrides.pop("workspace_id", "ws1"),
    )


async def test_alerta_critico_cria_card_em_triage_sem_llm(db, monkeypatch):
    calls: list[Any] = []
    monkeypatch.setattr(
        agent_factory, "get_native_agent", lambda *a, **k: calls.append((a, k))
    )
    await _make_observability_sync_anchor()

    card = await bg.sync_observability_alert_to_kanban(
        {
            "title": "Erro 500 em /checkout",
            "description": "NullPointerException",
            "severity": "critical",
            "url": "https://sentry.io/issues/1",
            "external_id": "sentry-1",
        }
    )

    assert card is not None
    assert card.name == "Erro 500 em /checkout"
    assert card.instruction == "NullPointerException"
    assert card.status == "triage"
    assert card.trigger_type == "manual"
    assert card.trigger_config["source"] == "observability_alert"
    assert card.trigger_config["external_id"] == "sentry-1"
    assert calls == []  # nunca chamou o agente — caminho 100% determinístico

    found = await bg.find_task_by_observability_alert("s-obs", "sentry-1")
    assert found is not None
    assert found.id == card.id

    # Borda: nenhuma task 'webhook' habilitada com provider=observability —
    # sync desligado, não cria nada nem levanta.
    conn = await bg._get_db()
    await conn.execute("UPDATE vectora_background_tasks SET enabled = 0")
    await conn.commit()
    await conn.close()
    assert (
        await bg.sync_observability_alert_to_kanban(
            {"title": "y", "external_id": "sentry-2"}
        )
        is None
    )


async def test_alerta_baixo_cria_card_em_todo(db):
    await _make_observability_sync_anchor()

    card = await bg.sync_observability_alert_to_kanban(
        {"title": "CPU acima de 80%", "severity": "low", "external_id": "grafana-1"}
    )

    assert card is not None
    assert card.status == "todo"


async def test_reentrega_do_mesmo_external_id_atualiza_em_vez_de_duplicar(db):
    """Mesmo contrato de idempotência do sync de GitHub Issues: reentrega
    do mesmo `external_id` atualiza o card existente, nunca duplica."""
    await _make_observability_sync_anchor()

    primeiro = await bg.sync_observability_alert_to_kanban(
        {
            "title": "Latência alta em /api",
            "severity": "high",
            "external_id": "pd-42",
        }
    )
    segundo = await bg.sync_observability_alert_to_kanban(
        {
            "title": "Latência alta em /api (resolvido)",
            "severity": "low",
            "external_id": "pd-42",
        }
    )

    assert primeiro is not None
    assert segundo is not None
    assert segundo.id == primeiro.id  # mesmo card, não duplicou
    assert primeiro.status == "triage"
    assert segundo.name == "Latência alta em /api (resolvido)"
    assert segundo.status == "todo"

    tasks = await bg.list_tasks("s-obs")
    cards_do_alerta = [
        t for t in tasks if t.trigger_config.get("external_id") == "pd-42"
    ]
    assert len(cards_do_alerta) == 1

    # Borda: alerta sem "external_id" nunca chega até aqui em produção (o
    # endpoint HTTP valida antes) — a função também não levanta com um
    # payload mínimo mal formado do chamador.
    with pytest.raises(KeyError):
        await bg.sync_observability_alert_to_kanban({"title": "sem id"})


# ---------------------------------------------------------------------------
# Delegação de subagente (tool `task`) → histórico na aba Tarefas
# ---------------------------------------------------------------------------


async def test_record_subagent_delegation_creates_anchor_and_run(db):
    await bg.record_subagent_delegation(
        session_id="s1",
        user_id="u1",
        subagent_type="coder",
        description="Crie um arquivo X",
        status="done",
        summary="Arquivo criado.",
        workspace_id="ws1",
    )

    tasks = await bg.list_tasks("s1")
    assert len(tasks) == 1
    anchor = tasks[0]
    assert anchor.kind == "subagent"
    assert anchor.name == "Subagente: coder"
    assert anchor.trigger_config == {"subagent_type": "coder"}

    runs = await bg.list_runs("s1")
    assert len(runs) == 1
    assert runs[0]["task_id"] == anchor.id
    assert runs[0]["trigger_source"] == "subagent"
    assert runs[0]["status"] == "done"
    assert runs[0]["summary"] == "Arquivo criado."


async def test_record_subagent_delegation_reuses_anchor_across_calls(db):
    """Segunda delegação do mesmo subagente na mesma thread não duplica a âncora."""
    await bg.record_subagent_delegation(
        session_id="s1",
        user_id="u1",
        subagent_type="search",
        description="Pesquise X",
        status="done",
        summary="ok",
    )
    await bg.record_subagent_delegation(
        session_id="s1",
        user_id="u1",
        subagent_type="search",
        description="Pesquise Y",
        status="error",
        summary="erro de rede",
    )

    tasks = await bg.list_tasks("s1")
    assert len(tasks) == 1  # âncora única, reusada

    runs = await bg.list_runs("s1")
    assert len(runs) == 2
    assert {r["status"] for r in runs} == {"done", "error"}


async def test_record_subagent_delegation_tolerates_db_failure(db, monkeypatch):
    """Erro/borda: falha ao persistir nunca deve propagar (best-effort)."""

    async def _boom() -> None:
        raise RuntimeError("db indisponível")

    monkeypatch.setattr(bg, "_get_db", _boom)

    # Não levanta — apenas loga e segue.
    await bg.record_subagent_delegation(
        session_id="s1",
        user_id="u1",
        subagent_type="coder",
        description="x",
        status="done",
        summary="y",
    )


# ---------------------------------------------------------------------------
# Kanban ligado à execução real (status/claim/block)
# ---------------------------------------------------------------------------


async def test_create_task_status_inicial_e_ready_nao_todo(db):
    """Task recorrente (`next_run_at` futuro definido) nasce com status
    "scheduled", que `claim_task` também aceita — nunca com o DEFAULT
    "todo" do schema, que deixaria o board sempre vazio."""
    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="A",
        instruction="i",
        trigger_type="interval",
        trigger_config={"cron_expr": "0 9 * * *"},
    )
    assert task.status == "scheduled"

    # Erro/borda: o dataclass devolvido por create_task precisa bater com o
    # que foi de fato persistido — não só o valor em memória.
    persistida = await bg.get_task(task.id)
    assert persistida is not None
    assert persistida.status == "scheduled"


async def test_create_task_manual_sem_next_run_nasce_ready(db):
    """Task manual (sem `next_run_at`) já é acionável agora — nasce
    "ready", não "scheduled" (reservado pra quando há data futura)."""
    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="A",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    assert task.status == "ready"


async def test_run_task_recorrente_volta_pra_ready_ao_concluir(
    db, native_session_store, monkeypatch
):
    """Tarefa recorrente nunca termina — `done` seria um estado terminal
    errado pra algo que roda de novo amanhã."""
    from backend.scheduling import kanban

    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        chat_client=_ScriptedChatClient([[_texto_chunk("ok")]]),
    )

    async def _noop_upsert(*_a, **_k) -> None:
        return None

    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _noop_upsert)

    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="Recorrente",
        instruction="i",
        trigger_type="interval",
        trigger_config={"cron_expr": "0 9 * * *"},
    )
    assert (await kanban.get_task_status(task.id))["status"] == "scheduled"

    await bg.run_task(task, "scheduler")

    estado = await kanban.get_task_status(task.id)
    assert estado["status"] == "ready"


async def test_run_task_unica_vira_done_ao_concluir(
    db, native_session_store, monkeypatch
):
    from backend.scheduling import kanban

    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        chat_client=_ScriptedChatClient([[_texto_chunk("ok")]]),
    )

    async def _noop_upsert(*_a, **_k) -> None:
        return None

    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _noop_upsert)

    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="Única",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )

    await bg.run_task(task, "manual")

    estado = await kanban.get_task_status(task.id)
    assert estado["status"] == "done"


async def test_run_task_erro_vira_blocked_transient_em_vez_de_travar_em_running(
    db, native_session_store, monkeypatch
):
    """Erro/borda: uma run que lança marca o card como `blocked` (motivo
    `transient`, com a mensagem do erro) em vez de deixá-lo preso em
    `running` para sempre."""
    from backend.scheduling import kanban

    _patch_native_engine(
        monkeypatch,
        session_store=native_session_store,
        chat_client=_ScriptedChatClient(exc=RuntimeError("LLM caiu")),
    )

    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="Falha",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )

    await bg.run_task(task, "manual")

    estado = await kanban.get_task_status(task.id)
    assert estado["status"] == "blocked"
    assert estado["block_kind"] == "transient"
    assert "LLM caiu" in (estado["block_reason"] or "")


async def test_run_task_nao_duplica_quando_claim_ja_foi_tomado(
    db, native_session_store, monkeypatch
):
    """Erro/borda de corrida: duas chamadas de `run_task` pra mesma task
    (tick do scheduler + disparo manual quase simultâneo) — a segunda não
    cria uma 2ª run."""
    from backend.scheduling import kanban

    client = _ScriptedChatClient([[_texto_chunk("ok")]])
    _patch_native_engine(
        monkeypatch, session_store=native_session_store, chat_client=client
    )

    async def _noop_upsert(*_a, **_k) -> None:
        return None

    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _noop_upsert)

    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="Corrida",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )

    # Simula outro worker já tendo pego o claim antes desta chamada.
    assert await kanban.claim_task(task.id, "run-de-outro-worker") is True

    resultado = await bg.run_task(task, "manual")

    assert resultado is None
    assert client.chamadas == 0
    runs = await bg.list_runs("s1")
    assert runs == []


async def test_update_task_toggle_sincroniza_status(db):
    from backend.scheduling import kanban

    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="Toggle",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    assert (await kanban.get_task_status(task.id))["status"] == "ready"

    await bg.update_task(task.id, enabled=False)
    assert (await kanban.get_task_status(task.id))["status"] == "todo"

    await bg.update_task(task.id, enabled=True)
    assert (await kanban.get_task_status(task.id))["status"] == "ready"


async def test_update_task_nao_reabilita_por_cima_de_bloqueio_ativo(db):
    """Erro/borda: reabilitar uma tarefa bloqueada (ex.: budget estourado)
    não pode fingir que o bloqueio não existe mais."""
    from backend.scheduling import kanban

    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="Bloqueada",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    await kanban.block_task(task.id, "capability", "orçamento estourado")

    await bg.update_task(task.id, enabled=True)

    estado = await kanban.get_task_status(task.id)
    assert estado["status"] == "blocked"
    assert estado["block_kind"] == "capability"


class TestRunTaskComPerfilDeAgente:
    """Task com `agent_profile_id` roda com a instrução/modelo do perfil em
    vez do comportamento padrão do orchestrator."""

    async def test_model_override_do_perfil_e_passado_ao_chat_client(
        self, db, native_session_store, monkeypatch
    ):
        import aiosqlite

        from backend.services import agent_profiles

        async def _connect_profiles():
            conn: Any = await aiosqlite.connect(db)
            conn.row_factory = lambda c, r: dict(
                zip([col[0] for col in c.description], r, strict=False)
            )
            return conn

        monkeypatch.setattr(agent_profiles, "_get_db", _connect_profiles)

        await agent_profiles.create_profile(
            "u1", "Perfil X", model_override="openrouter:gpt-4o"
        )
        profiles = await agent_profiles.list_profiles("u1")
        profile_id = profiles[0].id

        factory = _patch_native_engine(
            monkeypatch,
            session_store=native_session_store,
            chat_client=_ScriptedChatClient([[_texto_chunk("ok")]]),
        )

        async def _noop(*a, **k):
            return None

        monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _noop)

        task = await bg.create_task(
            session_id="sess-perfil",
            user_id="u1",
            kind="routine",
            name="Com perfil",
            instruction="Rode os testes",
            trigger_type="manual",
            trigger_config={},
            agent_profile_id=profile_id,
        )

        await bg.run_task(task, "manual")

        # FallbackChatClient é construído com o model_override do perfil —
        # 1ª chamada é a que roda o turno de verdade.
        assert factory.calls[0] == "openrouter:gpt-4o"

    async def test_task_sem_perfil_nao_muda_comportamento(
        self, db, native_session_store, monkeypatch
    ):
        """Task sem agent_profile_id nunca chama get_profile nem altera o
        model passado."""
        factory = _patch_native_engine(
            monkeypatch,
            session_store=native_session_store,
            chat_client=_ScriptedChatClient([[_texto_chunk("ok")]]),
        )

        async def _noop(*a, **k):
            return None

        monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _noop)

        task = await bg.create_task(
            session_id="sess-sem-perfil",
            user_id="u1",
            kind="routine",
            name="Sem perfil",
            instruction="Rode os testes",
            trigger_type="manual",
            trigger_config={},
        )

        await bg.run_task(task, "manual")

        assert factory.calls[0] == ""

    async def test_perfil_apagado_degrada_para_comportamento_padrao(
        self, db, native_session_store, monkeypatch
    ):
        """Erro/borda: agent_profile_id aponta pra um perfil que não existe
        mais (apagado) — a run continua normalmente, sem instrução/modelo
        extra, em vez de falhar."""
        import aiosqlite

        from backend.services import agent_profiles

        async def _connect_profiles():
            conn: Any = await aiosqlite.connect(db)
            conn.row_factory = lambda c, r: dict(
                zip([col[0] for col in c.description], r, strict=False)
            )
            return conn

        monkeypatch.setattr(agent_profiles, "_get_db", _connect_profiles)

        _patch_native_engine(
            monkeypatch,
            session_store=native_session_store,
            chat_client=_ScriptedChatClient([[_texto_chunk("ok")]]),
        )

        async def _noop(*a, **k):
            return None

        monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _noop)

        task = await bg.create_task(
            session_id="sess-perfil-sumiu",
            user_id="u1",
            kind="routine",
            name="Perfil sumiu",
            instruction="Rode os testes",
            trigger_type="manual",
            trigger_config={},
            agent_profile_id="perfil-que-nunca-existiu",
        )

        run_thread_id = await bg.run_task(task, "manual")

        assert run_thread_id is not None
