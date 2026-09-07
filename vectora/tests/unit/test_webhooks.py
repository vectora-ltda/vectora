"""Testes para backend/api/handlers/webhooks.py — infraestrutura genérica de webhooks e handlers específicos por provider (GitHub, etc.).

Cobre:
- Verificação de assinatura por provider (válida → 200, inválida → 401)
- Payload malformado → 400
- Dispatcher chama handler correto por provider
- SSE bridge emite evento para clientes conectados
- Persistência no banco (mock do get_db)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.handlers.webhooks import (
    CHANNEL_SSE,
    _emit_sse_event,
    _sse_queues,
    _verify_github,
    _verify_gitlab,
    _verify_linear,
    _verify_mailgun,
    on_remote_sse_event,
    router,
)

# ---------------------------------------------------------------------------
# Fixture de app mínimo com o router de webhooks
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Verificadores de assinatura — testes unitários puros
# ---------------------------------------------------------------------------


class TestVerifyGitHub:
    def _sig(self, body: bytes, secret: str) -> str:
        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_assinatura_valida(self) -> None:
        body = b'{"action":"opened"}'
        secret = "meu-secret"
        headers = {"x-hub-signature-256": self._sig(body, secret)}
        assert _verify_github(body, headers, secret) is True

    def test_assinatura_invalida(self) -> None:
        body = b'{"action":"opened"}'
        headers = {"x-hub-signature-256": "sha256=invalido"}
        assert _verify_github(body, headers, "secret") is False

    def test_sem_header(self) -> None:
        assert _verify_github(b"body", {}, "secret") is False

    def test_prefixo_errado(self) -> None:
        body = b"body"
        headers = {"x-hub-signature-256": "md5=abc"}
        assert _verify_github(body, headers, "secret") is False


class TestVerifyGitLab:
    def test_token_correto(self) -> None:
        assert (
            _verify_gitlab(b"body", {"x-gitlab-token": "meu-token"}, "meu-token")
            is True
        )

    def test_token_errado(self) -> None:
        assert _verify_gitlab(b"body", {"x-gitlab-token": "errado"}, "correto") is False

    def test_sem_header(self) -> None:
        assert _verify_gitlab(b"body", {}, "correto") is False


class TestVerifyLinear:
    def _sig(self, body: bytes, secret: str) -> str:
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_assinatura_valida(self) -> None:
        body = b'{"type":"Issue","action":"create"}'
        secret = "linear-secret"
        headers = {"x-linear-signature": self._sig(body, secret)}
        assert _verify_linear(body, headers, secret) is True

    def test_assinatura_invalida(self) -> None:
        headers = {"x-linear-signature": "invalido"}
        assert _verify_linear(b"body", headers, "secret") is False


class TestVerifyMailgun:
    def _sig(self, ts: str, token: str, key: str) -> str:
        data = (ts + token).encode()
        return hmac.new(key.encode(), data, hashlib.sha256).hexdigest()

    def test_assinatura_valida(self) -> None:
        ts = str(int(time.time()))
        token = "abc123"
        key = "mailgun-key"
        sig = self._sig(ts, token, key)
        body = json.dumps({"timestamp": ts, "token": token, "signature": sig}).encode()
        assert _verify_mailgun(body, {}, key) is True

    def test_assinatura_invalida(self) -> None:
        body = json.dumps(
            {"timestamp": "1", "token": "t", "signature": "errado"}
        ).encode()
        assert _verify_mailgun(body, {}, "chave") is False

    def test_payload_invalido(self) -> None:
        assert _verify_mailgun(b"nao-e-json", {}, "chave") is False


# ---------------------------------------------------------------------------
# Endpoint /webhook/{provider}
# ---------------------------------------------------------------------------


class TestWebhookEndpoint:
    def _github_body_and_sig(self, payload: dict, secret: str) -> tuple[bytes, str]:
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return body, sig

    @patch("backend.api.handlers.webhooks._persist_event", new_callable=AsyncMock)
    def test_github_assinatura_valida(
        self, mock_persist: AsyncMock, client: TestClient
    ) -> None:
        payload = {"action": "opened", "pull_request": {"number": 1, "title": "PR"}}
        secret = "meu-secret"
        body, sig = self._github_body_and_sig(payload, secret)
        with patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": secret}):
            resp = client.post(
                "/webhook/github",
                content=body,
                headers={
                    "x-github-event": "pull_request",
                    "x-hub-signature-256": sig,
                    "content-type": "application/json",
                },
            )
        assert resp.status_code == 200
        mock_persist.assert_awaited_once()

    @patch("backend.api.handlers.webhooks._persist_event", new_callable=AsyncMock)
    def test_github_assinatura_invalida(
        self, mock_persist: AsyncMock, client: TestClient
    ) -> None:
        with patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": "correto"}):
            resp = client.post(
                "/webhook/github",
                content=b'{"action":"opened"}',
                headers={
                    "x-github-event": "push",
                    "x-hub-signature-256": "sha256=errado",
                    "content-type": "application/json",
                },
            )
        assert resp.status_code == 401
        mock_persist.assert_not_awaited()

    @patch("backend.api.handlers.webhooks._persist_event", new_callable=AsyncMock)
    def test_sem_secret_configurado_aceita(
        self, mock_persist: AsyncMock, client: TestClient
    ) -> None:
        with patch.dict("os.environ", {}, clear=True):
            resp = client.post(
                "/webhook/github",
                json={"action": "opened"},
                headers={"x-github-event": "push"},
            )
        assert resp.status_code == 200

    @patch("backend.api.handlers.webhooks._persist_event", new_callable=AsyncMock)
    def test_payload_malformado(
        self, mock_persist: AsyncMock, client: TestClient
    ) -> None:
        with patch.dict("os.environ", {}, clear=True):
            resp = client.post(
                "/webhook/github",
                content=b"isto nao e json{",
                headers={
                    "x-github-event": "push",
                    "content-type": "application/json",
                },
            )
        assert resp.status_code == 400

    @patch("backend.api.handlers.webhooks._persist_event", new_callable=AsyncMock)
    def test_provider_suportado_sem_verificador_aceita(
        self, mock_persist: AsyncMock, client: TestClient
    ) -> None:
        resp = client.post("/webhook/sendgrid", json={"event": "delivered"})
        assert resp.status_code == 200

    @patch("backend.api.handlers.webhooks._persist_event", new_callable=AsyncMock)
    def test_provider_nao_suportado_e_rejeitado_antes_de_persistir(
        self, mock_persist: AsyncMock, client: TestClient
    ) -> None:
        resp = client.post("/webhook/slack", json={"type": "url_verification"})
        assert resp.status_code == 404
        mock_persist.assert_not_awaited()


# ---------------------------------------------------------------------------
# SSE bridge
# ---------------------------------------------------------------------------


class TestSSEBridge:
    """`_emit_sse_event` publica no KV (cross-réplica); `on_remote_sse_event`
    (o subscriber, registrado em cache_sync.start_cache_sync) é quem de fato
    escreve em `_sse_queues`. As duas pontas são testadas separadamente e
    depois juntas de ponta a ponta via um MemoryKV real."""

    def test_emit_sse_event_publica_no_canal_kv(self) -> None:
        with patch("backend.persistence.kv.publish_soon") as mock_publish:
            _emit_sse_event("github", "push", {"ref": "main"})

        assert mock_publish.call_count == 1
        channel, payload = mock_publish.call_args[0]
        assert channel == CHANNEL_SSE
        event = json.loads(payload)
        assert event["provider"] == "github"
        assert event["event_type"] == "push"
        assert event["data"]["ref"] == "main"

    def test_on_remote_sse_event_entrega_para_filas(self) -> None:
        import asyncio

        q: asyncio.Queue = asyncio.Queue()
        _sse_queues.append(q)
        try:
            payload = json.dumps(
                {
                    "type": "webhook_event",
                    "provider": "github",
                    "event_type": "push",
                    "data": {"ref": "main"},
                }
            )
            on_remote_sse_event(payload)
            assert not q.empty()
            event = q.get_nowait()
            assert event["provider"] == "github"
            assert event["data"]["ref"] == "main"
        finally:
            _sse_queues.remove(q)

    def test_on_remote_sse_event_fila_cheia_nao_levanta(self) -> None:
        import asyncio

        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        q.put_nowait({"dummy": True})
        _sse_queues.append(q)
        try:
            on_remote_sse_event(json.dumps({"provider": "github"}))
        finally:
            _sse_queues.remove(q)

    def test_on_remote_sse_event_payload_invalido_nao_levanta(self) -> None:
        # Erro: payload que não é JSON válido — não deve propagar exceção
        # (viria de outra réplica via KV, fora do nosso controle de forma).
        on_remote_sse_event("isso não é json")

    @pytest.mark.asyncio
    async def test_emit_sse_event_ponta_a_ponta_via_kv_real(self) -> None:
        """Publica via `_emit_sse_event` e confirma que chega em
        `_sse_queues` passando pelo roundtrip real do KV (MemoryKV em modo
        lite) — prova que a ponte publish→subscribe funciona de fato, não
        só que cada metade foi chamada com os argumentos certos."""
        import asyncio

        from backend.persistence import kv as kv_module

        kv_module.reset_kv()
        try:
            kv = await kv_module.get_kv()
            kv.subscribe(CHANNEL_SSE, on_remote_sse_event)
            await kv.start()

            q: asyncio.Queue = asyncio.Queue()
            _sse_queues.append(q)
            try:
                _emit_sse_event("linear", "issue.created", {"id": "ISS-1"})
                # publish_soon agenda uma task no loop corrente — cede o
                # controle uma vez pra ela rodar antes de checar a fila.
                await asyncio.sleep(0)
                event = await asyncio.wait_for(q.get(), timeout=1.0)
                assert event["provider"] == "linear"
                assert event["data"]["id"] == "ISS-1"
            finally:
                _sse_queues.remove(q)
        finally:
            kv_module.reset_kv()


# ---------------------------------------------------------------------------
# Handlers específicos — GitHub
# ---------------------------------------------------------------------------


class TestGitHubHandlers:
    @pytest.mark.asyncio
    async def test_handle_github_workflow_run(self) -> None:
        from unittest.mock import patch as p

        from backend.api.handlers.webhooks import _handle_github

        payload = {
            "action": "completed",
            "workflow_run": {
                "id": 1,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/r/actions/runs/1",
            },
            "repository": {"full_name": "user/repo"},
        }
        emitted: list[dict] = []

        def fake_emit(provider: str, event_type: str, data: dict) -> None:
            emitted.append(
                {"provider": provider, "event_type": event_type, "data": data}
            )

        with p("backend.api.handlers.webhooks._emit_sse_event", fake_emit):
            await _handle_github("workflow_run", payload, MagicMock())

        assert len(emitted) == 1
        assert emitted[0]["event_type"] == "workflow_run.completed"
        assert emitted[0]["data"]["conclusion"] == "success"

    @pytest.mark.asyncio
    async def test_handle_github_pull_request(self) -> None:
        from unittest.mock import patch as p

        from backend.api.handlers.webhooks import _handle_github

        payload = {
            "action": "opened",
            "pull_request": {
                "number": 42,
                "title": "feat: nova feature",
                "state": "open",
                "merged": False,
                "html_url": "https://github.com/r/pull/42",
            },
            "repository": {"full_name": "user/repo"},
        }
        emitted: list[dict] = []

        with p(
            "backend.api.handlers.webhooks._emit_sse_event",
            lambda *a, **kw: emitted.append(
                kw or {"provider": a[0], "event_type": a[1], "data": a[2]}
            ),
        ):
            await _handle_github("pull_request", payload, MagicMock())

        assert len(emitted) == 1

    @pytest.mark.asyncio
    async def test_handle_github_push(self) -> None:
        from unittest.mock import patch as p

        from backend.api.handlers.webhooks import _handle_github

        payload = {
            "ref": "refs/heads/main",
            "after": "abc123",
            "commits": [{}, {}],
            "pusher": {"name": "dev"},
            "repository": {"full_name": "user/repo"},
        }
        emitted: list[dict] = []

        def fake_emit(provider: str, event_type: str, data: dict) -> None:
            emitted.append({"event_type": event_type, "data": data})

        with p("backend.api.handlers.webhooks._emit_sse_event", fake_emit):
            await _handle_github("push", payload, MagicMock())

        assert emitted[0]["event_type"] == "push"
        assert emitted[0]["data"]["commit_count"] == 2

    @pytest.mark.asyncio
    async def test_handle_github_issues(self) -> None:
        from unittest.mock import patch as p

        from backend.api.handlers.webhooks import _handle_github

        payload = {
            "action": "opened",
            "issue": {
                "number": 10,
                "title": "Bug no login",
                "state": "open",
                "html_url": "https://github.com/r/issues/10",
            },
            "repository": {"full_name": "user/repo"},
        }
        emitted: list[dict] = []

        def fake_emit(provider: str, event_type: str, data: dict) -> None:
            emitted.append({"event_type": event_type, "data": data})

        with (
            p("backend.api.handlers.webhooks._emit_sse_event", fake_emit),
            p(
                "backend.scheduling.background_tasks.sync_github_issue_to_kanban",
                AsyncMock(return_value=None),
            ) as sync_mock,
        ):
            await _handle_github("issues", payload, MagicMock())

        assert emitted[0]["data"]["issue_number"] == 10
        # Repassa action/repo/issue pro sync determinístico do Kanban.
        sync_mock.assert_awaited_once_with("opened", "user/repo", payload["issue"])

    @pytest.mark.asyncio
    async def test_handle_github_issues_malformed_payload_never_raises(self) -> None:
        """Payload sem `action`/`issue`/`repository` não derruba o handler."""
        from unittest.mock import patch as p

        from backend.api.handlers.webhooks import _handle_github

        with (
            p("backend.api.handlers.webhooks._emit_sse_event", lambda *a, **kw: None),
            p(
                "backend.scheduling.background_tasks.sync_github_issue_to_kanban",
                AsyncMock(return_value=None),
            ) as sync_mock,
        ):
            # Nenhuma exceção propagada mesmo com payload vazio.
            await _handle_github("issues", {}, MagicMock())

        # action="" e repo="" — sync recebe strings vazias, decide sozinho não fazer nada.
        sync_mock.assert_awaited_once_with("", "", {})

    @pytest.mark.asyncio
    async def test_handle_github_issues_sync_failure_is_swallowed(self) -> None:
        """Erro dentro do sync do Kanban é logado, não propaga pro dispatcher."""
        from unittest.mock import patch as p

        from backend.api.handlers.webhooks import _handle_github

        payload = {
            "action": "opened",
            "issue": {"number": 1, "title": "x"},
            "repository": {"full_name": "user/repo"},
        }

        with (
            p("backend.api.handlers.webhooks._emit_sse_event", lambda *a, **kw: None),
            p(
                "backend.scheduling.background_tasks.sync_github_issue_to_kanban",
                AsyncMock(side_effect=RuntimeError("db indisponível")),
            ),
        ):
            await _handle_github("issues", payload, MagicMock())  # não levanta


# ---------------------------------------------------------------------------
# Endpoint genérico de observabilidade — POST /webhook/observability
# ---------------------------------------------------------------------------


class TestObservabilityWebhookEndpoint:
    """Contrato fixo {title, description?, severity?, url?, external_id},
    sem parsing nativo de vendor — Sentry/Grafana/PagerDuty apontam o
    webhook de saída pra cá."""

    @pytest.fixture
    def obs_db(self, tmp_path, monkeypatch):
        import asyncio
        from pathlib import Path

        import aiosqlite

        import backend
        from backend.scheduling import background_tasks as bg
        from backend.scheduling import kanban

        schema_path = (
            Path(backend.__file__).parent
            / "storage"
            / "migrations"
            / "sqlite"
            / "schema.sql"
        )
        db_path = str(tmp_path / "obs.db")

        async def _connect() -> Any:
            conn: Any = await aiosqlite.connect(db_path)
            conn.row_factory = lambda c, r: dict(
                zip([col[0] for col in c.description], r, strict=False)
            )
            return conn

        async def _setup() -> None:
            conn = await _connect()
            await conn.executescript(schema_path.read_text(encoding="utf-8"))
            await conn.commit()
            await conn.close()

        asyncio.run(_setup())
        monkeypatch.setattr(bg, "_get_db", _connect)
        monkeypatch.setattr(kanban, "_get_db", _connect)
        return db_path

    def _post(self, client: TestClient, payload: dict, secret: str | None = "s3cr3t"):
        headers = {"content-type": "application/json"}
        if secret is not None:
            headers["x-webhook-secret"] = secret
        return client.post("/webhook/observability", json=payload, headers=headers)

    def test_secret_invalido_retorna_401_e_nao_processa(
        self, client: TestClient
    ) -> None:
        with (
            patch.dict("os.environ", {"OBSERVABILITY_WEBHOOK_SECRET": "correto"}),
            patch(
                "backend.api.handlers.webhooks._persist_event",
                new_callable=AsyncMock,
            ) as mock_persist,
        ):
            resp = self._post(
                client, {"title": "x", "external_id": "1"}, secret="errado"
            )
        assert resp.status_code == 401
        mock_persist.assert_not_awaited()

    def test_secret_ausente_retorna_401(self, client: TestClient) -> None:
        with patch.dict("os.environ", {"OBSERVABILITY_WEBHOOK_SECRET": "correto"}):
            resp = self._post(client, {"title": "x", "external_id": "1"}, secret=None)
        assert resp.status_code == 401

    def test_secret_nao_configurado_no_servidor_retorna_401(
        self, client: TestClient
    ) -> None:
        # Sem OBSERVABILITY_WEBHOOK_SECRET no ambiente não há verificação
        # HMAC de vendor pra cair como alternativa (diferente do github/etc)
        # — a rota fica fechada até o operador configurar o secret.
        with patch.dict("os.environ", {}, clear=True):
            resp = self._post(client, {"title": "x", "external_id": "1"})
        assert resp.status_code == 401

    @patch("backend.api.handlers.webhooks._persist_event", new_callable=AsyncMock)
    def test_payload_sem_title_retorna_400(
        self, mock_persist: AsyncMock, client: TestClient
    ) -> None:
        with patch.dict("os.environ", {"OBSERVABILITY_WEBHOOK_SECRET": "s3cr3t"}):
            resp = self._post(client, {"external_id": "1"})
        assert resp.status_code == 400
        mock_persist.assert_not_awaited()

    @patch("backend.api.handlers.webhooks._persist_event", new_callable=AsyncMock)
    def test_payload_sem_external_id_retorna_400(
        self, mock_persist: AsyncMock, client: TestClient
    ) -> None:
        with patch.dict("os.environ", {"OBSERVABILITY_WEBHOOK_SECRET": "s3cr3t"}):
            resp = self._post(client, {"title": "Erro 500 em produção"})
        assert resp.status_code == 400
        mock_persist.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_severidade_mapeia_status_e_reentrega_atualiza_card(
        self, client: TestClient, obs_db: str
    ) -> None:
        """Caminho feliz: severidade `critical` cria o card em `triage`.
        Reentrega do mesmo `external_id` com severidade `low` atualiza o
        MESMO card (nome + status), sem criar um segundo."""
        from backend.scheduling import background_tasks as bg

        with patch.dict("os.environ", {"VECTORA_LICENSE_BYPASS": "1"}):
            anchor = await bg.create_task(
                session_id="s-obs",
                user_id="u1",
                kind="routine",
                name="Sync observabilidade",
                instruction="",
                trigger_type="webhook",
                trigger_config={"provider": "observability"},
            )

        with patch.dict("os.environ", {"OBSERVABILITY_WEBHOOK_SECRET": "s3cr3t"}):
            resp_critical = self._post(
                client,
                {
                    "title": "Erro 500 em /checkout",
                    "description": "NullPointerException no gateway de pagamento",
                    "severity": "critical",
                    "url": "https://sentry.io/issues/1",
                    "external_id": "sentry-1",
                },
            )
            assert resp_critical.status_code == 200

            tasks = await bg.list_tasks(anchor.session_id)
            cards = [
                t for t in tasks if t.trigger_config.get("external_id") == "sentry-1"
            ]
            assert len(cards) == 1
            assert cards[0].status == "triage"

            resp_low = self._post(
                client,
                {
                    "title": "Erro 500 em /checkout (resolvido)",
                    "severity": "low",
                    "external_id": "sentry-1",
                },
            )
            assert resp_low.status_code == 200

        tasks_after = await bg.list_tasks(anchor.session_id)
        cards_after = [
            t for t in tasks_after if t.trigger_config.get("external_id") == "sentry-1"
        ]
        assert len(cards_after) == 1
        assert cards_after[0].status == "todo"
        assert cards_after[0].name == "Erro 500 em /checkout (resolvido)"

    @pytest.mark.asyncio
    async def test_sem_task_ancora_nao_cria_card_mas_responde_200(
        self, client: TestClient, obs_db: str
    ) -> None:
        """Sync desligado (nenhuma task 'webhook' com provider=observability
        configurada) — o endpoint aceita e persiste o evento, mas nenhum
        card nasce. Erro/borda do sync, não do transporte HTTP."""
        with patch.dict("os.environ", {"OBSERVABILITY_WEBHOOK_SECRET": "s3cr3t"}):
            resp = self._post(
                client,
                {
                    "title": "Latência alta",
                    "severity": "high",
                    "external_id": "grafana-1",
                },
            )
        assert resp.status_code == 200
