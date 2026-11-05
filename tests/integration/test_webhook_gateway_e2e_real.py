"""Pendência 1.5 — E2E real do webhook via gateway de produção.

Sobe uma instância isolada, registra no gateway real (`gateway.vectora.chat`)
com `VECTORA_APP_SECRET` (produto, não por-usuário — ver `settings.py`),
conecta o `GatewayClient`, dispara um evento via `POST
https://{token}.vectora.chat/webhook/{provider}` de fora, e confirma que
`dispatch_webhook_event` roda a `background_task` correspondente.

Guardado atrás de skip se `VECTORA_APP_SECRET`/rede não disponíveis — mesma
hermeticidade dos demais testes de integração real do projeto (ver
`tests/integration/test_mcp_marketplace_3rd_party_real.py`). A checagem de
rede roda dentro do corpo do teste (não no decorator `skipif`, avaliado na
coleta) — assim `scons tests`/`pytest --collect-only` nunca fazem uma
chamada de rede só por importar este módulo.

Marcado `@pytest.mark.live` (mesma convenção de `test_llm_live.py`/
`test_web_search_live.py`): fora do `scons tests` padrão (`-m "not live"`),
roda só com `uv run pytest tests/integration/test_webhook_gateway_e2e_real.py
-m live` explícito.

Efeito colateral real: registra uma nova instalação no gateway de produção
(mesmo fluxo que uma instalação real do Vectora faria no primeiro boot com
gateway habilitado). Não é destrutivo nem afeta outras instalações.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

import backend

pytestmark = [pytest.mark.asyncio, pytest.mark.live]

_SCHEMA = (
    Path(backend.__file__).parent / "storage" / "migrations" / "sqlite" / "schema.sql"
)
_GATEWAY_HTTP_URL = "https://gateway.vectora.chat/"
_APP_SECRET = os.environ.get("VECTORA_APP_SECRET", "")


def _gateway_reachable(url: str, timeout: float = 3.0) -> bool:
    """`True` se o Worker responde (mesmo 404) — só rede/serviço fora do ar
    devem impedir o teste, não a ausência de uma rota específica."""
    try:
        resp = httpx.get(url, timeout=timeout)
        return resp.status_code < 500
    except Exception:
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _BackgroundUvicorn:
    """`uvicorn.Server` real rodando numa task asyncio — o `GatewayClient`
    faz requisições HTTP de verdade contra `local_url`, então precisa de um
    servidor de verdade escutando numa porta, não um `TestClient`."""

    def __init__(self, app: FastAPI, port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._server.serve())
        for _ in range(100):
            if self._server.started:
                return
            await asyncio.sleep(0.05)
        msg = "servidor local não subiu a tempo"
        raise RuntimeError(msg)

    async def stop(self) -> None:
        self._server.should_exit = True
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(self._task, timeout=5.0)


async def test_webhook_via_gateway_producao_dispara_background_task(
    tmp_path, monkeypatch
) -> None:
    if not _APP_SECRET:
        pytest.skip("VECTORA_APP_SECRET ausente")
    if not _gateway_reachable(_GATEWAY_HTTP_URL):
        pytest.skip("gateway.vectora.chat inacessível — sem rede ou serviço fora do ar")

    from backend.api.handlers import webhooks
    from backend.rbac import auth as rbac_auth
    from backend.scheduling import background_tasks as bg
    from backend.services.gateway import GatewayClient
    from backend.settings import settings

    # Banco isolado com o schema real aplicado — mesmo padrão de
    # `test_kanban_budget_same_db.py`: só troca `settings.db_dsn`, sem
    # monkeypatch de `_get_db`, pra provar convergência de verdade.
    db_path = tmp_path / "backend.db"
    conn = await __import__("aiosqlite").connect(str(db_path))
    await conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
    await conn.commit()
    await conn.close()
    monkeypatch.setattr(settings, "db_dsn", str(db_path))

    # `_persist_event` (recepção do webhook) escreve em `checkpoints.db` via
    # `backend.rbac.auth`, com conexão global cacheada — sem isolar isso,
    # o teste gravaria evento real no `~/.vectora/checkpoints.db` do usuário.
    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    monkeypatch.setattr(rbac_auth, "_db_conn", None)

    monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
    task = await bg.create_task(
        session_id="e2e-gateway",
        user_id="e2e-user",
        kind="routine",
        name="E2E webhook",
        instruction="responder 'ok' — tarefa de teste, não deve rodar de verdade",
        trigger_type="webhook",
        trigger_config={"provider": "github", "events": ["ping"]},
    )

    app = FastAPI()
    app.include_router(webhooks.router)
    port = _free_port()
    server = _BackgroundUvicorn(app, port)
    await server.start()

    token_path = tmp_path / "gateway_token"
    client = GatewayClient(
        gateway_url=settings.gateway_url,
        app_secret=_APP_SECRET,
        local_url=f"http://127.0.0.1:{port}",
        token_path=token_path,
        secret_path=tmp_path / "gateway_connector_secret",
    )

    try:
        client.start()
        # Handshake de registro + WS: dá tempo real de rede pra completar.
        for _ in range(50):
            if token_path.is_file():
                break
            await asyncio.sleep(0.1)
        else:
            pytest.skip("gateway não completou o registro a tempo — ambiente instável")

        token = token_path.read_text().strip()
        subdomain_url = settings.gateway_url.replace("wss://", "https://").replace(
            "ws://", "http://"
        )
        # Rota real é `/webhook/{provider}` (singular) — ver
        # `backend/api/handlers/webhooks.py:395` (`@router.post`).
        webhook_url = f"{subdomain_url.replace('gateway.', f'{token}.')}/webhook/github"

        # `token_path` existir só confirma que o handshake HTTP de registro
        # terminou — o WebSocket em si pode levar um instante a mais pra ser
        # marcado "conectado" no Durable Object do gateway. Nessa janela, o
        # webhook real retorna 202 ("backend offline, queued") em vez de 200.
        # Mesma classe de instabilidade de ambiente que o skip acima já trata
        # pro registro; aqui é curta o bastante pra só dar um retry.
        resp: httpx.Response | None = None
        async with httpx.AsyncClient(timeout=15.0) as external:
            for _attempt in range(5):
                resp = await external.post(
                    webhook_url,
                    headers={"X-GitHub-Event": "ping"},
                    content=json.dumps({"zen": "e2e"}).encode(),
                )
                if resp.status_code == 200:
                    break
                await asyncio.sleep(0.5)
            else:
                assert resp is not None  # range(5) sempre atribui ao menos 1x
                pytest.skip(
                    "gateway não marcou a conexão como online a tempo — "
                    f"ambiente instável (último status: {resp.status_code} {resp.text})"
                )

        # `dispatch_webhook_event` é fire-and-forget (asyncio.create_task) —
        # dá um instante pro agendamento rodar antes de checar o estado.
        atualizada: bg.BackgroundTask | None = None
        for _ in range(30):
            atualizada = await bg.get_task(task.id)
            if atualizada is not None and atualizada.status != "ready":
                break
            await asyncio.sleep(0.2)

        assert atualizada is not None
        assert atualizada.status != "ready", (
            "a task 'webhook' não foi disparada por dispatch_webhook_event "
            "depois do evento chegar via gateway real"
        )
    finally:
        await client.stop()
        await server.stop()
