"""Tests — storage/migrations/runner.py e data_migration.py.

Runner SQLite: usa as migrations em storage/migrations/sqlite/*.sql.
DataMigration: dry-run em memória.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


class TestMigrationRunner:
    """Schema migration runner — arquivo único (sqlite/schema.sql)."""

    @pytest.fixture
    async def runner_conn(self, tmp_path):
        import aiosqlite

        db_path = str(tmp_path / "test_migrations.db")
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        yield conn
        await conn.close()

    @pytest.fixture
    async def raw_conn(self, tmp_path):
        """Conexão sem row_factory — simula o contexto de produção (server.py)."""
        import aiosqlite

        db_path = str(tmp_path / "test_migrations_raw.db")
        conn = await aiosqlite.connect(db_path)
        yield conn
        await conn.close()

    @pytest.mark.asyncio
    async def test_status_pending_on_empty_db(self, runner_conn):
        """status() mostra applied=False num banco vazio."""
        from backend.storage.migrations.runner import MigrationRunner

        runner = MigrationRunner(runner_conn)
        status = await runner.status()
        assert status.applied is False
        assert status.applied_at is None
        assert status.checksum

    @pytest.mark.asyncio
    async def test_status_shows_applied_after_upgrade(self, runner_conn):
        """status() mostra applied=True e drift=False após upgrade() rodar."""
        from backend.storage.migrations.runner import MigrationRunner

        runner = MigrationRunner(runner_conn)
        await runner.upgrade()
        status = await runner.status()
        assert status.applied is True
        assert status.drift is False
        assert status.applied_at is not None

    @pytest.mark.asyncio
    async def test_upgrade_applies_schema_and_returns_true(self, runner_conn):
        """upgrade() aplica o schema.sql inteiro e retorna True na 1ª chamada."""
        from backend.storage.migrations.runner import MigrationRunner

        runner = MigrationRunner(runner_conn)
        applied = await runner.upgrade()
        assert applied is True

    @pytest.mark.asyncio
    async def test_upgrade_idempotent(self, runner_conn):
        """Segunda chamada a upgrade() é no-op (checksum já bate) — retorna False."""
        from backend.storage.migrations.runner import MigrationRunner

        runner = MigrationRunner(runner_conn)
        await runner.upgrade()
        second = await runner.upgrade()
        assert second is False

    @pytest.mark.asyncio
    async def test_schema_migrations_table_created(self, runner_conn):
        """Tabela de controle schema_migrations é criada ao instanciar runner."""
        from backend.storage.migrations.runner import MigrationRunner

        runner = MigrationRunner(runner_conn)
        await runner.upgrade()  # força criação da tabela

        cur = await runner_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        row = await cur.fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_upgrade_without_row_factory(self, raw_conn):
        """upgrade() funciona com conexão sem row_factory (caso de produção)."""
        from backend.storage.migrations.runner import MigrationRunner

        runner = MigrationRunner(raw_conn)
        applied = await runner.upgrade()
        assert applied is True
        second = await runner.upgrade()
        assert second is False

    @pytest.mark.asyncio
    async def test_colunas_do_kanban_chegam_em_banco_ja_populado(
        self, runner_conn, monkeypatch
    ):
        """`CREATE TABLE IF NOT EXISTS` é no-op quando a tabela já existe sem
        as colunas `status`/`block_kind`/`claim_lock`/`budget_cents` etc —
        os `ALTER TABLE` correspondentes precisam rodar mesmo assim, senão o
        tick do scheduler falha com `sqlite3.OperationalError: no such
        column: status` contra um banco com o schema antigo de
        `vectora_background_tasks`."""
        from backend.storage.migrations.runner import MigrationRunner

        # Simula o shape "pré-kanban": só as colunas que existiam antes.
        await runner_conn.executescript(
            """
            CREATE TABLE vectora_background_tasks (
                id             TEXT PRIMARY KEY,
                session_id     TEXT NOT NULL,
                workspace_id   TEXT,
                user_id        TEXT NOT NULL,
                kind           TEXT NOT NULL,
                name           TEXT NOT NULL,
                instruction    TEXT NOT NULL,
                trigger_type   TEXT NOT NULL,
                trigger_config TEXT NOT NULL DEFAULT '{}',
                enabled        INTEGER NOT NULL DEFAULT 1,
                last_run_at    TEXT,
                next_run_at    TEXT,
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE vectora_background_runs (
                id             TEXT PRIMARY KEY,
                task_id        TEXT NOT NULL,
                session_id     TEXT NOT NULL,
                run_thread_id  TEXT,
                trigger_source TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'running',
                summary        TEXT,
                started_at     TEXT NOT NULL DEFAULT (datetime('now')),
                finished_at    TEXT
            );
            """
        )
        await runner_conn.commit()

        assert await MigrationRunner(runner_conn).upgrade() is True

        cur = await runner_conn.execute("PRAGMA table_info(vectora_background_tasks)")
        cols = {row[1] for row in await cur.fetchall()}
        assert {
            "status",
            "block_kind",
            "block_reason",
            "claim_lock",
            "claim_expires_at",
            "budget_cents",
        } <= cols

        cur = await runner_conn.execute("PRAGMA table_info(vectora_background_runs)")
        cols = {row[1] for row in await cur.fetchall()}
        assert {"tokens_used", "estimated_cost_cents"} <= cols

        # Erro/borda: release_stale_claims do scheduler roda sem lançar
        # contra o schema migrado.
        from backend.scheduling import kanban

        async def _get_db():
            return runner_conn

        monkeypatch.setattr(kanban, "_get_db", _get_db)
        assert await kanban.release_stale_claims() == 0

    @pytest.mark.asyncio
    async def test_vectora_sessions_table_created(self, runner_conn):
        """O schema cria a tabela vectora_sessions."""
        from backend.storage.migrations.runner import MigrationRunner

        await MigrationRunner(runner_conn).upgrade()
        cur = await runner_conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='vectora_sessions'"
        )
        assert await cur.fetchone() is not None

    @pytest.mark.asyncio
    async def test_vectora_sessions_has_extra_column(self, runner_conn):
        """vectora_sessions tem a coluna extra (onde mode/title/workspace vivem)."""
        from backend.storage.migrations.runner import MigrationRunner

        await MigrationRunner(runner_conn).upgrade()
        cur = await runner_conn.execute("PRAGMA table_info(vectora_sessions)")
        cols = {row[1] for row in await cur.fetchall()}
        assert "extra" in cols
        assert "thread_id" in cols

    @pytest.mark.asyncio
    async def test_reapply_after_content_change_updates_checksum(
        self, runner_conn, tmp_path
    ):
        """Editar o schema.sql muda o checksum: upgrade() reaplica e status() reflete."""
        from backend.storage.migrations.runner import MigrationRunner

        original = (
            Path(__file__).resolve().parents[2]
            / "backend"
            / "storage"
            / "migrations"
            / "sqlite"
            / "schema.sql"
        ).read_text(encoding="utf-8")

        edited_file = tmp_path / "schema_edited.sql"
        edited_file.write_text(
            original
            + "\nCREATE TABLE IF NOT EXISTS _test_marker (id INTEGER PRIMARY KEY);\n",
            encoding="utf-8",
        )

        runner = MigrationRunner(runner_conn, schema_file=edited_file)
        first = await runner.upgrade()
        assert first is True

        cur = await runner_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_test_marker'"
        )
        assert await cur.fetchone() is not None

        status = await runner.status()
        assert status.applied is True
        assert status.drift is False

    @pytest.mark.asyncio
    async def test_plan_diagnostica_sem_mutar_o_banco(self, runner_conn):
        """O plano expõe drift e contagem sem aplicar ou criar tabelas do
        schema de negócio."""
        from backend.storage.migrations.runner import MigrationRunner

        runner = MigrationRunner(runner_conn)
        plan = await runner.plan()
        assert plan["will_apply"] is True
        assert plan["statement_count"] > 0
        cur = await runner_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vectora_sessions'"
        )
        assert await cur.fetchone() is None

    @pytest.mark.asyncio
    async def test_history_diagnostica_sem_mutar_banco_vazio(self, runner_conn):
        """history() é somente leitura quando não há tabelas de controle."""
        from backend.storage.migrations.runner import MigrationRunner

        runner = MigrationRunner(runner_conn)
        assert await runner.history() == []
        cur = await runner_conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        assert await cur.fetchall() == []

    @pytest.mark.asyncio
    async def test_history_ignora_formato_sem_id(self, runner_conn):
        """history() rejeita tabela de controle sem a coluna de ordenação."""
        from backend.storage.migrations.runner import MigrationRunner

        await runner_conn.execute(
            "CREATE TABLE schema_migration_history "
            "(checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        await runner_conn.commit()
        assert await MigrationRunner(runner_conn).history() == []

    @pytest.mark.asyncio
    async def test_alter_add_column_skips_existing_column(self, runner_conn):
        """ALTER TABLE ... ADD COLUMN não falha quando a coluna já existe."""
        from backend.storage.migrations.runner import MigrationRunner

        runner = MigrationRunner(runner_conn)
        await runner.upgrade()

        # Reaplicar manualmente o statement ALTER de uma coluna já criada pelo
        # CREATE TABLE não deve levantar "duplicate column name".
        await runner._execute_statement(
            "ALTER TABLE vectora_sessions ADD COLUMN extra TEXT"
        )

    @pytest.mark.asyncio
    async def test_upgrade_from_old_versioned_control_table(self, runner_conn):
        """Um `schema_migrations` no formato antigo versionado (version, name,
        applied_at, checksum) — sem coluna `id` — não pode fazer apply()
        quebrar com "no such column: id"."""
        from backend.storage.migrations.runner import MigrationRunner

        await runner_conn.executescript("""
            CREATE TABLE schema_migrations (
                version    TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                checksum   TEXT NOT NULL
            );
            INSERT INTO schema_migrations VALUES
                ('0001', 'auth', '2026-07-13T13:31:47+00:00', 'deadbeef');
        """)
        await runner_conn.commit()

        runner = MigrationRunner(runner_conn)
        applied = await runner.apply()
        assert applied is True

        status = await runner.status()
        assert status.applied is True
        assert status.drift is False


class TestReadSchemaPermissionRetry:
    """Lock transitório (AV/indexador) em schema.sql recém-extraído pelo
    instalador — `_read_schema` reage a `PermissionError` com retry curto
    em vez de derrubar o boot inteiro nessa janela."""

    @pytest.fixture
    async def runner_conn(self, tmp_path):
        import aiosqlite

        db_path = str(tmp_path / "test_migrations.db")
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        yield conn
        await conn.close()

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="msvcrt.locking só existe no Windows — o lock transitório do "
        "S0-4 (AV/indexador escaneando schema.sql) é um cenário Windows-only",
    )
    async def test_retry_ate_sucesso_quando_lock_solta_antes_do_teto(
        self, runner_conn, tmp_path, monkeypatch
    ):
        """Lock (simulado por abrir o arquivo em modo exclusivo) solta antes
        do teto de tentativas — `apply()` retenta e conclui normalmente."""
        # `if sys.platform == "win32":` (não só o skipif do decorator, que só
        # afeta o runtime) — `msvcrt` não existe fora do Windows, e o type
        # checker (`ty`, roda também no CI ubuntu-latest) analisa o corpo da
        # função independente do marker do pytest. Sem esse guard estático,
        # `msvcrt.locking`/`LK_NBLCK`/`LK_UNLCK` quebram o `ty check` no CI.
        if sys.platform == "win32":
            import msvcrt

            from backend.storage.migrations import runner as runner_mod
            from backend.storage.migrations.runner import MigrationRunner

            schema_file = tmp_path / "schema.sql"
            schema_file.write_text(
                "CREATE TABLE IF NOT EXISTS _retry_marker (id INTEGER PRIMARY KEY);",
                encoding="utf-8",
            )
            monkeypatch.setattr(runner_mod, "_READ_SCHEMA_RETRY_DELAY_S", 0.01)

            # Abre em modo exclusivo (nega leitura de outros handles no
            # Windows) e libera antes da última tentativa — reproduz o lock
            # transitório de verdade, não um mock de exceção.
            fh = schema_file.open("r+b")
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

            async def _release_soon():
                await asyncio.sleep(0.02)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                fh.close()

            release_task = asyncio.ensure_future(_release_soon())
            try:
                runner = MigrationRunner(runner_conn, schema_file=schema_file)
                applied = await runner.apply()
            finally:
                await release_task
            assert applied is True

            cur = await runner_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='_retry_marker'"
            )
            assert await cur.fetchone() is not None

    @pytest.mark.asyncio
    async def test_permission_error_persistente_propaga_apos_esgotar_tentativas(
        self, runner_conn, tmp_path, monkeypatch
    ):
        """Erro/borda: lock que NUNCA solta (permissão genuinamente negada)
        continua falhando após as tentativas — sem mascarar um erro real."""
        from pathlib import Path

        from backend.storage.migrations import runner as runner_mod
        from backend.storage.migrations.runner import MigrationRunner

        schema_file = tmp_path / "schema.sql"
        schema_file.write_text("CREATE TABLE x (id INTEGER);", encoding="utf-8")
        monkeypatch.setattr(runner_mod, "_READ_SCHEMA_RETRY_DELAY_S", 0.01)

        def _sempre_nega(self, *args, **kwargs):
            raise PermissionError("Permission denied (simulado)")

        monkeypatch.setattr(Path, "read_text", _sempre_nega)

        runner = MigrationRunner(runner_conn, schema_file=schema_file)
        with pytest.raises(PermissionError):
            await runner.apply()


class TestDataMigrationDryRun:
    """Migrações de dados — apenas dry-run para não precisar de infra."""

    @pytest.mark.asyncio
    async def test_to_postgres_dry_run(self, tmp_path):
        """dry-run retorna contagem sem conectar ao Postgres."""
        import aiosqlite

        from backend.storage.migrations.data_migration import migrate_to_postgres

        db_path = str(tmp_path / "source.db")
        async with aiosqlite.connect(db_path) as conn:
            # Cria tabela de teste
            await conn.execute(
                "CREATE TABLE vectora_users (id TEXT PRIMARY KEY, name TEXT)"
            )
            await conn.execute("INSERT INTO vectora_users VALUES ('u1', 'Alice')")
            await conn.commit()

        result = await migrate_to_postgres(
            sqlite_path=db_path,
            postgres_dsn="postgresql://fake/db",
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert result["total_rows"] >= 1
        assert "vectora_users" in result["tables"]

    @pytest.mark.asyncio
    async def test_to_postgres_dry_run_missing_table(self, tmp_path):
        """Tabela ausente não levanta erro no dry-run."""
        import aiosqlite

        from backend.storage.migrations.data_migration import migrate_to_postgres

        db_path = str(tmp_path / "empty.db")
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("CREATE TABLE unrelated (id INTEGER)")

        result = await migrate_to_postgres(
            sqlite_path=db_path,
            postgres_dsn="postgresql://fake/db",
            dry_run=True,
        )
        assert result["total_rows"] == 0

    @pytest.mark.asyncio
    async def test_memory_to_native_store_missing_table(self, tmp_path):
        """Banco sem tabela 'memories' retorna total=0 sem erro."""
        import aiosqlite

        from backend.storage.migrations.data_migration import (
            migrate_memory_to_native_store,
        )

        db_path = str(tmp_path / "no_memories.db")
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("CREATE TABLE other (id INTEGER)")

        # dry-run — não precisa de store configurado
        result = await migrate_memory_to_native_store(
            sqlite_path=db_path,
            dry_run=True,
        )
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_memory_to_native_store_dry_run(self, tmp_path):
        """dry-run conta registros sem chamar store."""
        import aiosqlite

        from backend.storage.migrations.data_migration import (
            migrate_memory_to_native_store,
        )

        db_path = str(tmp_path / "memories.db")
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "CREATE TABLE memories (id TEXT PRIMARY KEY, user_id TEXT, content TEXT)"
            )
            await conn.execute(
                "INSERT INTO memories VALUES ('m1', 'u1', '{\"text\": \"hello\"}')"
            )
            await conn.commit()

        result = await migrate_memory_to_native_store(
            sqlite_path=db_path,
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert result["total"] == 1


class TestDataMigrationLanceDBSource:
    """`migrate_to_qdrant`/`migrate_to_pgvector` — cobertura era zero antes
    destes testes (nem mock, nem tabela real). Origem sempre uma tabela
    LanceDB real (`tmp_path`); destino (Qdrant/Postgres) mockado, já que
    não há serviço externo disponível neste ambiente de teste.

    `lancedb.connect_async()` real trava de forma determinística em CI
    (Linux) — ver comentário completo em pyproject.toml (seção `timeout`).
    Isolado em step separado do workflow via marcador `real_lancedb`."""

    pytestmark = pytest.mark.real_lancedb

    @pytest.fixture
    async def source_table(self, tmp_path):
        import lancedb

        db = await lancedb.connect_async(str(tmp_path / "lancedb"))
        await db.create_table(
            "articles",
            data=[
                {
                    "id": f"doc-{i}",
                    "vector": [float(i)] * 4,
                    "text": f"conteúdo {i}",
                }
                for i in range(5)
            ],
        )
        return str(tmp_path / "lancedb")

    @pytest.mark.asyncio
    async def test_to_qdrant_colecao_ausente_retorna_erro_sem_lancar(self, tmp_path):
        import lancedb

        from backend.storage.migrations.data_migration import migrate_to_qdrant

        await lancedb.connect_async(str(tmp_path / "lancedb"))

        result = await migrate_to_qdrant(
            lancedb_path=str(tmp_path / "lancedb"),
            qdrant_url="http://fake-qdrant",
            collection="nao-existe",
        )

        assert result["total"] == 0
        assert result["upserted"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_to_qdrant_dry_run_conta_sem_conectar(self, source_table):
        from backend.storage.migrations.data_migration import migrate_to_qdrant

        result = await migrate_to_qdrant(
            lancedb_path=source_table,
            qdrant_url="http://fake-qdrant",
            dry_run=True,
        )

        assert result["dry_run"] is True
        assert result["total"] == 5
        assert result["upserted"] == 0

    @pytest.mark.asyncio
    async def test_to_qdrant_upsert_todos_os_vetores_em_batches(self, source_table):
        from unittest.mock import MagicMock, patch

        from backend.storage.migrations.data_migration import migrate_to_qdrant

        mock_client = MagicMock()
        with patch("qdrant_client.QdrantClient", return_value=mock_client):
            result = await migrate_to_qdrant(
                lancedb_path=source_table,
                qdrant_url="http://fake-qdrant",
                batch_size=2,
            )

        assert result["total"] == 5
        assert result["upserted"] == 5
        # 5 linhas em batches de 2 → 3 chamadas de upsert (2, 2, 1)
        assert mock_client.upsert.call_count == 3
        all_point_ids = {
            p.id
            for call in mock_client.upsert.call_args_list
            for p in call.kwargs["points"]
        }
        assert all_point_ids == {f"doc-{i}" for i in range(5)}

    @pytest.mark.asyncio
    async def test_to_qdrant_pula_linhas_sem_vetor_sem_lancar(self, tmp_path):
        """Borda: linha com `vector` nulo (registro corrompido ou legado sem
        embedding gerado) é pulada, não vira `PointStruct` com
        `vector=None` (o que o Qdrant rejeitaria)."""
        from unittest.mock import MagicMock, patch

        import lancedb
        import pyarrow as pa

        from backend.storage.migrations.data_migration import migrate_to_qdrant

        db = await lancedb.connect_async(str(tmp_path / "lancedb"))
        schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), 2)),
                pa.field("text", pa.string()),
            ]
        )
        table = await db.create_table("articles", schema=schema)
        await table.add([{"id": "doc-0", "vector": [0.1, 0.2], "text": "com vetor"}])
        await table.add([{"id": "doc-1", "vector": None, "text": "sem vetor"}])

        mock_client = MagicMock()
        with patch("qdrant_client.QdrantClient", return_value=mock_client):
            result = await migrate_to_qdrant(
                lancedb_path=str(tmp_path / "lancedb"),
                qdrant_url="http://fake-qdrant",
            )

        assert result["total"] == 2
        assert result["upserted"] == 1
        (call,) = mock_client.upsert.call_args_list
        assert [p.id for p in call.kwargs["points"]] == ["doc-0"]

    @pytest.mark.asyncio
    async def test_to_pgvector_colecao_ausente_retorna_erro_sem_lancar(self, tmp_path):
        import lancedb

        from backend.storage.migrations.data_migration import migrate_to_pgvector

        await lancedb.connect_async(str(tmp_path / "lancedb"))

        result = await migrate_to_pgvector(
            lancedb_path=str(tmp_path / "lancedb"),
            postgres_dsn="postgresql://fake/db",
            collection="nao-existe",
        )

        assert result["total"] == 0
        assert result["upserted"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_to_pgvector_dry_run_conta_sem_conectar(self, source_table):
        from backend.storage.migrations.data_migration import migrate_to_pgvector

        result = await migrate_to_pgvector(
            lancedb_path=source_table,
            postgres_dsn="postgresql://fake/db",
            dry_run=True,
        )

        assert result["dry_run"] is True
        assert result["total"] == 5
        assert result["upserted"] == 0

    @pytest.mark.asyncio
    async def test_to_pgvector_upsert_todos_os_vetores_em_batches(self, source_table):
        from unittest.mock import AsyncMock, patch

        from backend.storage.migrations.data_migration import migrate_to_pgvector

        mock_conn = AsyncMock()
        with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
            result = await migrate_to_pgvector(
                lancedb_path=source_table,
                postgres_dsn="postgresql://fake/db",
                batch_size=2,
            )

        assert result["total"] == 5
        assert result["upserted"] == 5
        mock_conn.close.assert_awaited_once()
        # 1 CREATE EXTENSION + 1 CREATE TABLE + 5 INSERT
        insert_calls = [
            call
            for call in mock_conn.execute.call_args_list
            if call.args and "INSERT INTO" in call.args[0]
        ]
        assert len(insert_calls) == 5
        # Idempotência por construção: reprocessar a mesma linha (id
        # determinístico) não duplica no destino — sem isso, rodar a
        # migração duas vezes dobraria as linhas no Postgres.
        assert all("ON CONFLICT (id) DO NOTHING" in c.args[0] for c in insert_calls)

    @pytest.mark.asyncio
    async def test_to_pgvector_pula_linhas_sem_vetor_sem_lancar(self, tmp_path):
        from unittest.mock import AsyncMock, patch

        import lancedb
        import pyarrow as pa

        from backend.storage.migrations.data_migration import migrate_to_pgvector

        db = await lancedb.connect_async(str(tmp_path / "lancedb"))
        schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), 2)),
                pa.field("text", pa.string()),
            ]
        )
        table = await db.create_table("articles", schema=schema)
        await table.add([{"id": "doc-0", "vector": [0.1, 0.2], "text": "com vetor"}])
        await table.add([{"id": "doc-1", "vector": None, "text": "sem vetor"}])

        mock_conn = AsyncMock()
        with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
            result = await migrate_to_pgvector(
                lancedb_path=str(tmp_path / "lancedb"),
                postgres_dsn="postgresql://fake/db",
            )

        assert result["total"] == 2
        assert result["upserted"] == 1
        insert_calls = [
            call
            for call in mock_conn.execute.call_args_list
            if call.args and "INSERT INTO" in call.args[0]
        ]
        assert len(insert_calls) == 1
        assert insert_calls[0].args[1] == "doc-0"

    @pytest.mark.asyncio
    async def test_to_pgvector_fecha_conexao_mesmo_com_falha_no_insert(
        self, source_table
    ):
        """Erro/borda: se um INSERT falhar no meio da migração, `pg_conn`
        precisa ser fechado mesmo assim (bloco `finally`) — senão a conexão
        vaza a cada tentativa de migração que falha."""
        from unittest.mock import AsyncMock, patch

        from backend.storage.migrations.data_migration import migrate_to_pgvector

        mock_conn = AsyncMock()

        async def _execute(query: str, *_args):
            if "INSERT INTO" in query:
                raise RuntimeError("conexão perdida")

        mock_conn.execute = AsyncMock(side_effect=_execute)

        with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
            with pytest.raises(RuntimeError, match="conexão perdida"):
                await migrate_to_pgvector(
                    lancedb_path=source_table,
                    postgres_dsn="postgresql://fake/db",
                )

        mock_conn.close.assert_awaited_once()
