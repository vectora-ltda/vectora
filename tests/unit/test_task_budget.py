"""Budget por tarefa em segundo plano, com corte automático.

Cada tarefa pode ter um `budget_cents` (próprio ou herdado do perfil de
agente). `check_budget` soma o custo estimado das runs concluídas e barra
apenas a PRÓXIMA run quando o teto é ultrapassado — a run em andamento
nunca é abortada, para não truncar output parcial. Sem `budget_cents`
definido (`None`), a tarefa nunca é barrada.
"""

from __future__ import annotations

import pytest


@pytest.fixture
async def db(tmp_path, monkeypatch):
    import aiosqlite

    from backend.scheduling import budget

    conn = await aiosqlite.connect(tmp_path / "budget.db")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(
        """
        CREATE TABLE vectora_background_tasks (
            id           TEXT PRIMARY KEY,
            session_id   TEXT NOT NULL,
            user_id      TEXT NOT NULL,
            kind         TEXT NOT NULL,
            name         TEXT NOT NULL,
            instruction  TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'todo',
            block_kind   TEXT,
            block_reason TEXT,
            claim_lock   TEXT,
            claim_expires_at TEXT,
            budget_cents INTEGER,
            budget_warn_percent INTEGER,
            agent_profile_id TEXT,
            block_count  INTEGER NOT NULL DEFAULT 0,
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE vectora_background_runs (
            id                    TEXT PRIMARY KEY,
            task_id               TEXT NOT NULL,
            session_id            TEXT NOT NULL,
            trigger_source        TEXT NOT NULL,
            status                TEXT NOT NULL DEFAULT 'running',
            tokens_used           INTEGER,
            estimated_cost_cents  REAL,
            liveness              TEXT,
            started_at            TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    await conn.commit()

    async def _get_db():
        return conn

    monkeypatch.setattr(budget, "_get_db", _get_db)
    from backend.scheduling import kanban

    monkeypatch.setattr(kanban, "_get_db", _get_db)
    yield conn
    await conn.close()


async def _task(
    conn,
    task_id: str,
    budget_cents: int | None = None,
    agent_profile_id: str | None = None,
    budget_warn_percent: int | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO vectora_background_tasks "
        "(id, session_id, user_id, kind, name, instruction, trigger_type, "
        "budget_cents, agent_profile_id, budget_warn_percent) "
        "VALUES (?, 's1', 'u1', 'subagent', 'n', 'i', 'interval', ?, ?, ?)",
        (task_id, budget_cents, agent_profile_id, budget_warn_percent),
    )
    await conn.commit()


async def _run(conn, task_id: str, run_id: str, custo: float | None) -> None:
    await conn.execute(
        "INSERT INTO vectora_background_runs "
        "(id, task_id, session_id, trigger_source, status, estimated_cost_cents) "
        "VALUES (?, ?, 's1', 'scheduler', 'done', ?)",
        (run_id, task_id, custo),
    )
    await conn.commit()


class TestRastreioDeCusto:
    def test_usage_metadata_vira_custo_estimado(self):
        from backend.scheduling.budget import estimate_cost_cents

        custo = estimate_cost_cents(
            "openai:gpt-4o", {"input_tokens": 1000, "output_tokens": 500}
        )

        assert custo is not None
        assert custo > 0

    def test_sem_usage_metadata_devolve_none_e_nao_zero(self):
        """Erro/borda: provider que não expõe uso não gastou zero — não se
        sabe. Gravar 0 faria o budget nunca estourar."""
        from backend.scheduling.budget import estimate_cost_cents

        assert estimate_cost_cents("openai:gpt-4o", None) is None
        assert estimate_cost_cents("openai:gpt-4o", {}) is None

    def test_modelo_sem_tabela_de_preco_devolve_none(self):
        """Erro/borda: chutar preço de modelo desconhecido daria um número
        inventado que o usuário leria como real."""
        from backend.scheduling.budget import estimate_cost_cents

        assert (
            estimate_cost_cents(
                "provider-novo:modelo-x", {"input_tokens": 10, "output_tokens": 5}
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_custo_e_persistido_na_run(self, db):
        from backend.scheduling.budget import record_run_cost

        await _task(db, "t1")
        await _run(db, "t1", "r1", None)
        await record_run_cost("r1", tokens_used=1500, cost_cents=2.5)

        async with db.execute(
            "SELECT tokens_used, estimated_cost_cents FROM vectora_background_runs "
            "WHERE id = 'r1'"
        ) as cur:
            row = await cur.fetchone()

        assert row["tokens_used"] == 1500
        assert row["estimated_cost_cents"] == pytest.approx(2.5)

    @pytest.mark.asyncio
    async def test_liveness_e_persistido_junto_do_custo(self, db):
        """`record_run_cost` também grava o sinal de liveness
        (backend/scheduling/liveness.py) — opcional, `None` por padrão."""
        from backend.scheduling.budget import record_run_cost

        await _task(db, "t1")
        await _run(db, "t1", "r1", None)
        await record_run_cost(
            "r1", tokens_used=100, cost_cents=1.0, liveness="planning_only"
        )

        async with db.execute(
            "SELECT liveness FROM vectora_background_runs WHERE id = 'r1'"
        ) as cur:
            row = await cur.fetchone()

        assert row["liveness"] == "planning_only"

    @pytest.mark.asyncio
    async def test_liveness_ausente_por_padrao(self, db):
        """Edge: chamar sem `liveness` (compat com os call-sites antigos)
        grava NULL, não quebra."""
        from backend.scheduling.budget import record_run_cost

        await _task(db, "t1")
        await _run(db, "t1", "r1", None)
        await record_run_cost("r1", tokens_used=100, cost_cents=1.0)

        async with db.execute(
            "SELECT liveness FROM vectora_background_runs WHERE id = 'r1'"
        ) as cur:
            row = await cur.fetchone()

        assert row["liveness"] is None


class TestCorteAutomatico:
    @pytest.mark.asyncio
    async def test_task_sem_budget_nunca_e_barrada(self, db):
        """O corte é opt-in: task com `budget_cents = NULL` nunca é
        barrada, não importa o custo acumulado."""
        from backend.scheduling.budget import check_budget

        await _task(db, "t1", budget_cents=None)
        await _run(db, "t1", "r1", custo=9999.0)

        assert await check_budget("t1") is True

    @pytest.mark.asyncio
    async def test_task_sem_budget_herda_do_perfil(self, db, monkeypatch):
        """Task sem budget próprio, mas com agent_profile_id, herda o teto
        do perfil — nunca sobrescreve um budget que a task já definiu
        explicitamente (ver teste seguinte)."""
        from types import SimpleNamespace

        from backend.scheduling.budget import check_budget

        await _task(db, "t1", budget_cents=None, agent_profile_id="prof-1")
        await _run(db, "t1", "r1", custo=150.0)

        async def _fake_get_profile(profile_id):
            assert profile_id == "prof-1"
            return SimpleNamespace(budget_cents=100)

        monkeypatch.setattr(
            "backend.services.agent_profiles.get_profile", _fake_get_profile
        )

        assert await check_budget("t1") is False

    @pytest.mark.asyncio
    async def test_budget_proprio_da_task_nao_e_sobrescrito_pelo_perfil(
        self, db, monkeypatch
    ):
        from types import SimpleNamespace

        from backend.scheduling.budget import check_budget

        await _task(db, "t1", budget_cents=1000, agent_profile_id="prof-1")
        await _run(db, "t1", "r1", custo=150.0)

        async def _fake_get_profile(profile_id):
            # Nunca deveria ser chamado — task já tem budget_cents próprio.
            raise AssertionError("get_profile não deveria ser chamado")

        monkeypatch.setattr(
            "backend.services.agent_profiles.get_profile", _fake_get_profile
        )

        assert await check_budget("t1") is True

    @pytest.mark.asyncio
    async def test_falha_ao_carregar_perfil_degrada_para_sem_limite(
        self, db, monkeypatch
    ):
        """Erro/borda: perfil apagado ou DB indisponível não pode impedir a
        run de rodar — degrada pro comportamento padrão (sem limite)."""
        from backend.scheduling.budget import check_budget

        await _task(db, "t1", budget_cents=None, agent_profile_id="prof-sumiu")
        await _run(db, "t1", "r1", custo=9999.0)

        async def _fake_get_profile(profile_id):
            raise RuntimeError("perfil não encontrado")

        monkeypatch.setattr(
            "backend.services.agent_profiles.get_profile", _fake_get_profile
        )

        assert await check_budget("t1") is True

    @pytest.mark.asyncio
    async def test_dentro_do_budget_libera(self, db):
        from backend.scheduling.budget import check_budget

        await _task(db, "t1", budget_cents=100)
        await _run(db, "t1", "r1", custo=30.0)
        await _run(db, "t1", "r2", custo=20.0)

        assert await check_budget("t1") is True

    @pytest.mark.asyncio
    async def test_budget_estourado_barra_a_proxima_run(self, db):
        from backend.scheduling.budget import check_budget

        await _task(db, "t1", budget_cents=100)
        await _run(db, "t1", "r1", custo=80.0)
        await _run(db, "t1", "r2", custo=30.0)

        assert await check_budget("t1") is False

    @pytest.mark.asyncio
    async def test_task_barrada_vira_blocked_com_capability(self, db):
        """O motivo aparece no card: `block_kind="capability"` sinaliza que
        a tarefa não pode continuar rodando como está configurada."""
        from backend.scheduling.budget import check_budget
        from backend.scheduling.kanban import get_task_status

        await _task(db, "t1", budget_cents=10)
        await _run(db, "t1", "r1", custo=50.0)

        await check_budget("t1")

        estado = await get_task_status("t1")
        assert estado["status"] == "blocked"
        assert estado["block_kind"] == "capability"
        assert "budget" in (estado["block_reason"] or "").lower()

    @pytest.mark.asyncio
    async def test_run_sem_custo_conhecido_nao_conta_como_zero(self, db):
        """Erro/borda: runs com custo desconhecido (provider sem usage) não
        podem ser somadas como 0 — o budget nunca estouraria."""
        from backend.scheduling.budget import accumulated_cents

        await _task(db, "t1", budget_cents=100)
        await _run(db, "t1", "r1", custo=None)
        await _run(db, "t1", "r2", custo=40.0)

        total, desconhecidas = await accumulated_cents("t1")
        assert total == pytest.approx(40.0)
        assert desconhecidas == 1

    @pytest.mark.asyncio
    async def test_budget_zero_barra_de_cara(self, db):
        """Erro/borda: `budget_cents=0` é "não gaste nada", não "sem limite" —
        confundir os dois com `None` liberaria a tarefa."""
        from backend.scheduling.budget import check_budget

        await _task(db, "t1", budget_cents=0)

        assert await check_budget("t1") is False


class TestWarnPercent:
    """Aviso informativo ao cruzar `budget_warn_percent`, sem
    bloquear a task — só o hard-stop (`budget_cents` estourado) bloqueia."""

    @pytest.mark.asyncio
    async def test_cruzar_warn_percent_nao_bloqueia_mas_sinaliza(self, db):
        from backend.scheduling.budget import budget_status, check_budget

        await _task(db, "t1", budget_cents=100, budget_warn_percent=80)
        await _run(db, "t1", "r1", custo=85.0)

        assert await check_budget("t1") is True
        status = await budget_status("t1")
        assert status["warn"] is True
        assert status["over"] is False

    @pytest.mark.asyncio
    async def test_sem_warn_percent_configurado_nao_quebra(self, db):
        """Erro/borda: task com budget_cents mas sem budget_warn_percent
        (None) nunca marca `warn=True` — não é um bug, é o opt-in de
        sempre (mesmo espírito do próprio budget_cents)."""
        from backend.scheduling.budget import budget_status, check_budget

        await _task(db, "t1", budget_cents=100, budget_warn_percent=None)
        await _run(db, "t1", "r1", custo=95.0)

        assert await check_budget("t1") is True
        status = await budget_status("t1")
        assert status["warn"] is False

    @pytest.mark.asyncio
    async def test_estourar_o_teto_marca_warn_e_over_juntos(self, db):
        from backend.scheduling.budget import budget_status

        await _task(db, "t1", budget_cents=100, budget_warn_percent=80)
        await _run(db, "t1", "r1", custo=150.0)

        status = await budget_status("t1")
        assert status["warn"] is True
        assert status["over"] is True

    @pytest.mark.asyncio
    async def test_task_inexistente_devolve_status_neutro(self, db):
        """Bad path: task_id desconhecido não lança — devolve o status
        'sem limite algum', mesmo contrato de `check_budget` pra task
        ausente."""
        from backend.scheduling.budget import budget_status

        status = await budget_status("nao-existe")

        assert status["limit_cents"] is None
        assert status["warn"] is False
        assert status["over"] is False


class TestRunEmAndamento:
    @pytest.mark.asyncio
    async def test_estourar_no_meio_nao_aborta_a_run_atual(self, db):
        """Estourar o budget no meio de uma run não a aborta — trunca output
        parcial. A run corrente termina normalmente; só a PRÓXIMA é barrada.
        """
        from backend.scheduling.budget import check_budget, record_run_cost

        await _task(db, "t1", budget_cents=50)
        await _run(db, "t1", "r1", custo=None)

        # A run corrente termina e só então registra o custo que estourou.
        await record_run_cost("r1", tokens_used=100000, cost_cents=200.0)

        async with db.execute(
            "SELECT status FROM vectora_background_runs WHERE id = 'r1'"
        ) as cur:
            assert (await cur.fetchone())["status"] == "done"

        # A próxima é que não começa.
        assert await check_budget("t1") is False
