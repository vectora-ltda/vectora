"""FastAPI app factory do Vectora API.

Modos:
    chat     — API + StaticFiles da SPA (``chat/dist/``)
    headless — apenas API (sem catch-all SPA)

Schemas request/response vivem em ``src/api/schemas.py`` (Pydantic).

Endpoints registrados:
    POST /vectora.chat.v1.ChatService/StreamChat
    POST /vectora.chat.v1.ChatService/ResumeChat
    GET  /vectora.chat.v1.ChatService/GetTools
    POST /vectora.chat.v1.ThreadService/CreateThread
    POST /vectora.chat.v1.ThreadService/GetThread
    POST /vectora.chat.v1.ThreadService/ListThreads
    POST /vectora.chat.v1.ThreadService/DeleteThread
    POST /vectora.chat.v1.ThreadService/GetHistory
    GET  /health
    GET  /metrics
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import Response as FastAPIResponse
from fastapi.staticfiles import StaticFiles

from backend.api.handlers.admin import router as admin_router
from backend.api.handlers.agent_profiles import router as agent_profiles_router
from backend.api.handlers.artifacts import router as artifacts_router
from backend.api.handlers.auth import router as auth_router
from backend.api.handlers.background import router as background_router
from backend.api.handlers.backup import router as backup_router
from backend.api.handlers.boards import router as boards_router
from backend.api.handlers.chat import router as chat_router
from backend.api.handlers.connect import router as connect_router
from backend.api.handlers.context_graph import router as graph_router
from backend.api.handlers.feedback import router as feedback_router
from backend.api.handlers.flags import router as flags_router
from backend.api.handlers.gateway import router as gateway_router
from backend.api.handlers.license import router as license_router
from backend.api.handlers.mcp_marketplace import router as mcp_marketplace_router
from backend.api.handlers.memory import router as memory_router
from backend.api.handlers.memory_library import router as memory_library_router
from backend.api.handlers.models import router as models_router
from backend.api.handlers.oauth import router as oauth_router
from backend.api.handlers.oidc import router as oidc_router
from backend.api.handlers.plugins import router as plugins_router
from backend.api.handlers.provider_routing import router as provider_routing_router
from backend.api.handlers.rag import router as rag_router
from backend.api.handlers.share import router as share_router
from backend.api.handlers.skills import router as skills_router
from backend.api.handlers.terminal import router as terminal_router
from backend.api.handlers.threads import router as thread_router
from backend.api.handlers.tools import router as tools_router
from backend.api.handlers.usage import router as usage_router
from backend.api.handlers.webhooks import router as webhooks_router
from backend.api.handlers.workspaces import router as workspace_router
from backend.api.handlers.workspaces import view_router as workspace_view_router
from backend.api.handlers.workspaces import (
    workspace_scoped_router as workspace_scoped_view_router,
)

logger = logging.getLogger(__name__)

# Tempo máximo total para o shutdown — depois disso, `os._exit` em main.py
# encerra o processo de qualquer jeito. Configurável via env.
_SHUTDOWN_TIMEOUT_S = float(os.environ.get("VECTORA_SHUTDOWN_TIMEOUT_S", "10"))

# Pré-aquecer o grafo no startup. Reduz latência da 1ª request (~3-5s) em troca
# de inicialização mais lenta. Default desligado (dev-friendly).
_WARMUP_GRAPH = os.environ.get("VECTORA_WARMUP_GRAPH", "0").lower() in {
    "1",
    "true",
    "yes",
}


def _chat_static_root() -> Path | None:
    """Localiza o bundle estático da SPA do chat.

    Resolução em ordem:
      1. PyInstaller onefile — ``sys._MEIPASS/chat_static``.
      2. Nuitka onefile — ``__compiled__.containing_dir/chat_static``.
      3. Nuitka onefile — ``$NUITKA_ONEFILE_PARENT/chat_static``.
      4. Override via env ``VECTORA_CHAT_STATIC``.
      5. Dev — ``<repo_root>/frontend/dist`` (build Vite).

    Retorna ``None`` se nenhuma das localizações tiver um ``index.html``.
    ``VECTORA_SKIP_STATIC=1`` força proxy para o dev server (modo dev).
    """
    if os.environ.get("VECTORA_SKIP_STATIC"):
        return None

    candidates: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "chat_static")

    compiled = getattr(sys, "__compiled__", None)
    if compiled is not None and hasattr(compiled, "containing_dir"):
        candidates.append(Path(compiled.containing_dir) / "chat_static")

    nuitka_parent = os.environ.get("NUITKA_ONEFILE_PARENT")
    if nuitka_parent:
        candidates.append(Path(nuitka_parent) / "chat_static")

    override = os.environ.get("VECTORA_CHAT_STATIC")
    if override:
        candidates.append(Path(override))

    repo_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    candidates.append(repo_dist)

    for c in candidates:
        if (c / "index.html").is_file():
            return c
    return None


async def _stop_background_worker() -> None:
    """Para o background embedding worker se estiver rodando. Idempotente."""
    from backend.embedding import background as bg

    worker = bg._worker
    if worker is None:
        return
    bg._worker = None
    try:
        await worker.stop(timeout_seconds=5)
        logger.info("api/server: background worker parado")
    except Exception as exc:
        logger.warning("api/server: erro ao parar background worker: %s", exc)


_LICENSE_REVALIDATE_INTERVAL_S = 6 * 60 * 60  # 6h — espelha o TTL do cache


async def _license_revalidation_loop() -> None:
    """Valida a licença no boot e revalida a cada 6h.

    Nunca derruba o servidor: falha vira warning no log e o estado fica
    visível no banner do chat via ``GET /license/status`` (que lê o cache).
    """
    from backend.services.license import LicenseError, validate_license_async

    while True:
        try:
            info = await validate_license_async()
            logger.info(
                "license: tier=%s status=%s days_remaining=%d cached=%s",
                info.tier,
                info.status,
                info.days_remaining,
                info.cached,
            )
        except LicenseError as exc:
            logger.warning("license: %s", exc)
        except Exception as exc:
            logger.warning("license: falha inesperada na validação — %s", exc)
        await asyncio.sleep(_LICENSE_REVALIDATE_INTERVAL_S)


@asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[return]  # noqa: ANN202
    """Startup / shutdown da aplicação.

    **Startup**: opcionalmente pré-aquece o grafo (``VECTORA_WARMUP_GRAPH=1``).

    **Shutdown**: fecha recursos async-with longos (background worker primeiro,
    depois checkpointer SQLite) **em paralelo**, com timeout global. Recursos
    independentes não precisam esperar uns aos outros.

    O hard-exit final (``os._exit(0)`` em ``main.py``) cobre o caso de threads
    não-daemon de libs externas (httpx, aiosqlite) que ignoram cancel.
    """
    logger.info("api/server: startup")

    # Telemetria nativa de execução do agente (turnos, tool calls, erros).
    try:
        from backend.persistence.telemetry import telemetry

        telemetry.configure()
    except Exception as exc:
        logger.warning("api/server: falha ao configurar telemetria nativa: %s", exc)

    # Setup wizard: avisa o operador enquanto a instância não foi configurada
    # em nenhum dos modos. `setup_complete` (não `has_users`) porque o modo
    # local grava o perfil em app_settings sem criar linha em `users`.
    try:
        from backend.rbac.auth import setup_complete as _setup_complete

        if not await _setup_complete():
            logger.warning(
                "\n\n"
                "  ✨  Vectora aguardando setup inicial.\n"
                "      Abra o chat no browser — o wizard vai perguntar seu\n"
                "      nome e o modo de operação (local, sem conta, ou VPS\n"
                "      com VECTORA_TOKEN Pro).\n"
            )
    except Exception:
        pass  # Não bloqueia o startup se o DB ainda não estiver criado

    # Aplica migrations SQLite pendentes (idempotente via schema_migrations).
    try:
        import aiosqlite

        from backend.settings import settings as _db_settings
        from backend.storage.migrations import run_migrations

        _db_path = _db_settings.db_dsn or ":memory:"
        async with aiosqlite.connect(_db_path) as _conn:
            applied = await run_migrations(_conn)
            if applied:
                logger.info("api/server: schema SQLite aplicado (checksum mudou)")
    except Exception as exc:
        logger.warning("api/server: migrations SQLite falhou: %s", exc)

    # Garante que a tabela vectora_sessions existe antes do primeiro request.
    # Evita race com outro consumidor do mesmo arquivo .db criando tabela
    # concorrentemente, o que podia fazer get_thread/list_threads
    # retornarem 500 "no such table" até o servidor reiniciar.
    try:
        from backend.api.handlers.threads import ensure_sessions_table

        await ensure_sessions_table()
    except Exception as exc:
        logger.warning("api/server: falha ao criar vectora_sessions: %s", exc)

    if _WARMUP_GRAPH:
        from backend.api.handlers.chat import awarm_graph

        await awarm_graph()

    # Backend-primário em dev: sobe o Electron como sidecar (mesmo padrão do NATS) quando
    # backend/main.py::_run_start já decidiu, cedo (antes do uvicorn subir,
    # porque também define o transporte IPC), que este processo deve se
    # autoeleger — sinalizado via VECTORA_SPAWN_ELECTRON. O spawn em si só
    # roda aqui, dentro do event loop do FastAPI já rodando, não do
    # bootstrap síncrono da CLI — mantém `vectora start` leve pra quem só
    # quer a API REST (o sidecar nunca sobe se essa env não estiver setada).
    if os.environ.get("VECTORA_SPAWN_ELECTRON"):
        try:
            from backend.services.electron_sidecar import ensure_electron_sidecar

            await ensure_electron_sidecar()
        except Exception as exc:
            logger.warning("api/server: falha ao subir sidecar Electron: %s", exc)

    # Validação de licença: uma no boot (não-bloqueante) + revalidação a cada
    # 6h (TTL do cache). O launcher CLI valida antes do uvicorn, mas deploys
    # via docker/uvicorn direto não passam pelo launcher — sem este loop o
    # servidor nunca validava o VECTORA_TOKEN.
    license_task = asyncio.create_task(_license_revalidation_loop())

    # Popula o cache de detecção do WSL2 cedo — sem isso, parse_policy()
    # (síncrono, chamado no hot path de file_edit/file_write/terminal) só
    # vê o cache _UNSET e nunca auto-habilita o Sandbox na primeira leitura
    # real de um workspace sem vectora.toml.
    wsl2_warmup_task: asyncio.Task[None] | None = None
    try:
        from backend.sandbox.policy import warm_wsl2_cache

        wsl2_warmup_task = asyncio.create_task(warm_wsl2_cache())
    except Exception as exc:
        logger.warning("api/server: warmup de detecção WSL2 falhou: %s", exc)

    # Sincronização de caches entre réplicas (pub/sub via Redis quando
    # REDIS_URL configurado; no modo lite é local e inofensivo).
    try:
        from backend.embedding.cache_sync import start_cache_sync

        await start_cache_sync()
    except Exception as exc:
        logger.warning("api/server: cache_sync indisponível: %s", exc)

    # Worker que processa a fila de embeddings (RAG): lê os chunks PENDING e
    # grava os vetores no LanceDB. SEM ele, tudo que `ingest_docs`/
    # `ingest_directory` enfileiram fica "pending" para sempre e o RAG nunca
    # recupera nada (o `_lifespan` parava o worker no shutdown mas nunca o
    # iniciava no startup).
    try:
        from backend.embedding.background import get_background_worker

        embedding_worker = await get_background_worker()
        await embedding_worker.start()
    except Exception as exc:
        logger.warning("api/server: falha ao iniciar embedding worker: %s", exc)

    # Tarefas em segundo plano: scheduler das tasks 'interval' (cron) por session.
    try:
        from backend.scheduling.background_tasks import get_scheduler

        await get_scheduler().start()
    except Exception as exc:
        logger.warning("api/server: falha ao iniciar background scheduler: %s", exc)

    # Memory consolidation: sintetiza threads a cada 6h e atualiza AGENTS.md.
    consolidation_task: asyncio.Task[None] | None = None
    try:
        from backend.scheduling.memory_consolidation import (
            run_consolidation_for_all_users,
        )

        async def _consolidation_loop() -> None:
            while True:
                await asyncio.sleep(6 * 3600)
                await run_consolidation_for_all_users()

        consolidation_task = asyncio.create_task(_consolidation_loop())
    except Exception as exc:
        logger.warning("api/server: falha ao iniciar memory consolidation: %s", exc)

    # Hygiene: apaga threads sem mensagem (abandonadas antes do 1º envio,
    # ex. crash/cancelamento) a cada hora — sessões fantasma na sidebar.
    # Reconcilia junto (rede de segurança): repovoa vectora_sessions com
    # qualquer thread real que exista em sessions.db mas esteja ausente/
    # desatualizada ali — nunca deixa uma conversa real ficar invisível na
    # sidebar por muito mais de 1h, seja qual for a causa da divergência.
    thread_cleanup_task: asyncio.Task[None] | None = None
    try:
        from backend.api.handlers.threads import (
            cleanup_empty_threads,
            reconcile_vectora_sessions,
        )
        from backend.persistence.thread_activity import prune as prune_thread_activity

        async def _reconcile_safe() -> None:
            # Isolada do cleanup: se SessionStore não estiver disponível
            # (ex.: app headless sem motor nativo configurado) ou qualquer
            # outra falha, a reconciliação não pode derrubar a task e
            # impedir cleanup_empty_threads de rodar.
            try:
                await reconcile_vectora_sessions()
            except Exception as exc:
                logger.warning("api/server: falha ao reconciliar threads: %s", exc)

        async def _cleanup_safe() -> None:
            try:
                await cleanup_empty_threads()
            except Exception as exc:
                logger.warning("api/server: falha ao limpar threads vazias: %s", exc)

        async def _activity_cleanup_safe() -> None:
            try:
                await prune_thread_activity()
            except Exception as exc:
                logger.warning("api/server: falha ao limpar atividade remota: %s", exc)

        async def _thread_cleanup_loop() -> None:
            # Reconcilia ANTES de limpar: uma thread real com message_count
            # zerado por alguma falha (upsert perdido) e mais velha que o
            # cutoff seria apagada por cleanup_empty_threads antes de
            # reconcile_vectora_sessions ter a chance de corrigir a
            # contagem — nessa ordem, a reconciliação sempre roda primeiro.
            # Roda uma vez já no boot (não só após 1h de sleep) — sem isso,
            # threads fantasma de uma sessão anterior (crash/cancelamento
            # antes do 1º envio) ficavam visíveis por até 1h a cada restart.
            await _reconcile_safe()
            await _cleanup_safe()
            await _activity_cleanup_safe()
            while True:
                await asyncio.sleep(3600)
                await _reconcile_safe()
                await _cleanup_safe()
                await _activity_cleanup_safe()

        thread_cleanup_task = asyncio.create_task(_thread_cleanup_loop())
    except Exception as exc:
        logger.warning("api/server: falha ao iniciar cleanup de threads: %s", exc)

    # Gateway client (ex-relay) — WebSocket persistente para receber webhooks
    # e callbacks OAuth. Só inicia quando GATEWAY_ENABLED=true (padrão);
    # VECTORA_APP_SECRET é um secret fixo por produto (settings.py), igual
    # em toda instalação — sem ele o handshake de /register nunca autentica.
    _gateway_client = None
    try:
        from backend.settings import get_settings as _gs_gateway

        _cfg = _gs_gateway()
        if _cfg.gateway_enabled and _cfg.vectora_app_secret:
            from backend.services.gateway import GatewayClient

            _gateway_client = GatewayClient(
                gateway_url=_cfg.gateway_url,
                app_secret=_cfg.vectora_app_secret,
            )
            _gateway_client.start()
    except Exception as exc:
        logger.warning("api/server: falha ao iniciar gateway client: %s", exc)

    try:
        # Vectora Connect: sobe só as plataformas com credencial configurada.
        # Melhor esforço — `sync_adapters` já isola falha por plataforma, este
        # try cobre o caso de o módulo inteiro não carregar.
        from backend.services.connect.manager import sync_adapters

        await sync_adapters()
    except Exception as exc:
        logger.warning("api/server: falha ao iniciar adapters de Connect: %s", exc)

    try:
        yield
    finally:
        logger.info("api/server: shutdown — fechando recursos")
        if consolidation_task is not None:
            consolidation_task.cancel()
        if thread_cleanup_task is not None:
            thread_cleanup_task.cancel()
        if _gateway_client is not None:
            with suppress(Exception):
                await _gateway_client.stop()
        try:
            from backend.scheduling.background_tasks import get_scheduler as _gs

            await _gs().stop()
        except Exception:
            pass
        license_task.cancel()
        if wsl2_warmup_task is not None:
            wsl2_warmup_task.cancel()
        try:
            from backend.embedding.cache_sync import stop_cache_sync

            await stop_cache_sync()
        except Exception:
            logger.debug("api/server: erro ao encerrar cache_sync")

        # Fecha a message queue (Redis Streams / NATS JetStream) — usada por
        # background tasks/kanban, não exclusiva do worker de jobs removido.
        # Só se já inicializada nesta sessão: chamar get_mq() incondicional
        # aqui subiria um sidecar NATS do zero (sessão que nunca usou fila)
        # só para fechá-lo em seguida, atrasando o shutdown sem necessidade.
        try:
            from backend.scheduling.mq import get_mq, mq_initialized

            if mq_initialized():
                mq = await get_mq()
                await mq.close()
        except Exception:
            logger.warning("api/server: erro ao fechar message queue", exc_info=True)

        try:
            from backend.services.connect.manager import stop_all

            await stop_all()
        except Exception:
            logger.warning(
                "api/server: erro ao parar adapters de Connect", exc_info=True
            )

        try:
            from backend.scheduling.nats_sidecar import stop_nats_sidecar

            await stop_nats_sidecar()
        except Exception:
            logger.warning("api/server: erro ao encerrar sidecar NATS", exc_info=True)

        try:
            from backend.services.electron_sidecar import stop_electron_sidecar

            await stop_electron_sidecar()
        except Exception:
            # Nível WARNING de propósito: um Ctrl+C que não fecha a janela do
            # Electron (usuário precisa fechar pela bandeja) só é
            # diagnosticável se essa falha aparecer no log padrão — em DEBUG
            # ficava invisível no console normal, indistinguível de "não
            # aconteceu nada".
            logger.warning(
                "api/server: erro ao encerrar sidecar Electron", exc_info=True
            )
        from backend.api.handlers.chat import aclose_graph
        from backend.services.pty_registry import pty_registry

        # Encerra PTYs ANTES do hard-exit em main.py — evita processos órfãos.
        try:
            pty_registry.close_all()
        except Exception:
            logger.debug("api/server: erro ao encerrar PTYs")

        try:
            from backend.browser.session import close_all_browser_sessions

            await close_all_browser_sessions()
        except Exception:
            logger.debug("api/server: erro ao encerrar sessões de browser")

        try:
            from backend.browser.search_fallback import close_search_fallback_browser

            close_search_fallback_browser()
        except Exception:
            logger.debug("api/server: erro ao encerrar Chromium do fallback de busca")

        # Ordem lógica: parar workers que podem estar usando o grafo ANTES de
        # fechar o checkpointer. Mas como ambos têm `try/except` internos e são
        # independentes na prática, rodamos em paralelo via `gather` para
        # encurtar o tempo total de shutdown.
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _stop_background_worker(),
                    aclose_graph(),
                    return_exceptions=True,
                ),
                timeout=_SHUTDOWN_TIMEOUT_S,
            )
        except TimeoutError:
            logger.warning(
                "api/server: shutdown excedeu %.1fs — hard-exit cuidará do resto",
                _SHUTDOWN_TIMEOUT_S,
            )


def create_app(serve_static: bool = True) -> FastAPI:
    """Cria e configura a aplicação FastAPI do Vectora.

    ``serve_static=False`` desliga o catch-all que serve a SPA Vite — usado
    em testes para que rotas não-API retornem 404 reais em vez de devolver
    ``index.html`` ou cair no proxy dev.
    """
    from backend.version import __version__

    app = FastAPI(
        title="Vectora API",
        version=__version__,
        description=(
            "API de chat do Vectora — streaming via SSE, gerenciamento de threads, "
            "autodescoberta de ferramentas."
        ),
        lifespan=_lifespan,
        # Desabilita docs interativas em produção (opcional)
        docs_url="/docs",
        redoc_url=None,
    )

    # ── Exceção não tratada: loga antes do 500 genérico ────────────────────────
    # Sem isso, uma exceção em qualquer handler vira "500 Internal Server
    # Error" sem rastro nenhum no log — Starlette imprime via
    # `traceback.print_exc()` (stdout puro), que o `logging` do backend (e a
    # captura de log do pytest) nunca vê. Mesmo padrão do achado em
    # `native_stream.py`: erro real precisa deixar rastro, nunca só o
    # genérico.
    from fastapi.responses import JSONResponse

    async def _log_unhandled_exception(
        request: Request, exc: Exception
    ) -> FastAPIResponse:
        logging.getLogger(__name__).error(
            "api/server: exceção não tratada em %s %s",
            request.method,
            request.url.path,
            exc_info=exc,
        )
        return JSONResponse(
            status_code=500, content={"detail": "Internal Server Error"}
        )

    app.add_exception_handler(Exception, _log_unhandled_exception)

    # ── Auth middleware ───────────────────────────────────────────────────────
    from backend.api.middleware.auth import AuthMiddleware

    app.add_middleware(AuthMiddleware)

    # ── Rate limiting ─────────────────────────────────────────────────────────
    from backend.api.middleware.rate_limit import attach_limiter

    attach_limiter(app)

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Permite que o frontend Next.js (localhost:3000 em dev) chame a API.
    # Em produção, restringir CORS_ORIGINS ao domínio real do frontend.
    # Com allow_credentials=True, allow_origins não pode ser "*" — precisamos
    # de origens explícitas quando cookies httpOnly são usados.
    _cors_origins_env = os.environ.get("VECTORA_CORS_ORIGINS", "")
    _cors_origins = (
        [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
        if _cors_origins_env
        else [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(flags_router)
    app.include_router(feedback_router)
    app.include_router(auth_router)
    app.include_router(connect_router)
    app.include_router(chat_router)
    app.include_router(thread_router)
    app.include_router(share_router)
    app.include_router(memory_router)
    app.include_router(memory_library_router)
    app.include_router(oauth_router)
    app.include_router(oidc_router)
    app.include_router(gateway_router)
    app.include_router(webhooks_router)
    app.include_router(admin_router)
    app.include_router(backup_router)
    app.include_router(usage_router)
    app.include_router(workspace_router)
    app.include_router(workspace_view_router)
    app.include_router(workspace_scoped_view_router)
    app.include_router(artifacts_router)
    app.include_router(plugins_router)
    app.include_router(skills_router)
    app.include_router(license_router)
    app.include_router(tools_router)
    app.include_router(models_router)
    app.include_router(provider_routing_router)
    app.include_router(rag_router)
    app.include_router(terminal_router)
    app.include_router(background_router)
    app.include_router(boards_router)
    app.include_router(agent_profiles_router)
    app.include_router(graph_router)
    app.include_router(mcp_marketplace_router)

    # ── Discovery Layer — schema das tools ─────────────────────────────────
    @app.get("/api/tools/schema")
    async def tools_schema() -> dict:
        from backend.nodes.tools import ALL_TOOL_SPECS

        tools_data = []
        for spec in ALL_TOOL_SPECS:
            schema: dict = {}
            with suppress(Exception):
                schema = spec.args_model.model_json_schema()
            tools_data.append(
                {
                    "name": spec.name,
                    "description": (spec.description or "").split("\n")[0][:200],
                    "args_schema": schema,
                    "render_hint": spec.extras.render_hint,
                }
            )
        return {"version": "1", "tool_count": len(tools_data), "tools": tools_data}

    # ── Health + Metrics ──────────────────────────────────────────────────────

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/api/updates/changelog")
    async def update_changelog() -> dict[str, str]:
        """Return the latest packaged release notes for desktop updates."""
        candidates = (
            Path(__file__).resolve().parents[2] / "CHANGELOG.md",
            Path(sys.executable).resolve().parent / "CHANGELOG.md",
            Path(sys.executable).resolve().parent / "vectora-core" / "CHANGELOG.md",
            Path.cwd() / "CHANGELOG.md",
        )

        def read_latest() -> str:
            changelog_path = next(
                (candidate for candidate in candidates if candidate.is_file()),
                None,
            )
            if changelog_path is None:
                return ""
            lines = changelog_path.read_text(encoding="utf-8").splitlines()
            section: list[str] = []
            started = False
            for line in lines:
                if line.startswith("## "):
                    if started:
                        break
                    started = True
                if started:
                    section.append(line)
            return "\n".join(section).strip()

        return {"version": __version__, "notes": await asyncio.to_thread(read_latest)}

    @app.get("/metrics")
    async def metrics() -> list:
        try:
            from backend.persistence.tracer import tracer

            return await tracer.get_recent(n=50)
        except Exception:
            return []

    if not serve_static:
        return app

    # ── SPA Vite (preferido) ──────────────────────────────────────────────────
    # Em produção (binário Nuitka) ou após `pnpm --dir chat build`, servimos
    # o bundle estático diretamente. O catch-all devolve `index.html` para
    # rotas client-side (TanStack Router resolve no browser).
    chat_static = _chat_static_root()
    if chat_static is not None:
        logger.info("api/server: servindo SPA estática de %s", chat_static)

        # Mount em /assets/ (e diretórios afins gerados pelo Vite) — caminho
        # com extensão tem prioridade; rotas "limpas" caem no catch-all.
        app.mount(
            "/assets",
            StaticFiles(directory=str(chat_static / "assets")),
            name="vite-assets",
        )

        # Arquivos da raiz (favicon, manifest, ícones) servidos sob demanda.
        @app.get("/{filename:path}", include_in_schema=False)
        async def _spa_or_static(request: Request, filename: str) -> FastAPIResponse:
            # Se for arquivo real na raiz do bundle, serve direto.
            candidate = (chat_static / filename).resolve()
            try:
                candidate.relative_to(chat_static.resolve())
            except ValueError:
                # path traversal — bloqueia
                return FastAPIResponse(status_code=404)
            if candidate.is_file():
                return FileResponse(candidate)
            # Caso contrário, devolve index.html — o router client-side
            # processa a rota.
            return FileResponse(chat_static / "index.html")

        return app

    # ── Frontend proxy (fallback dev sem build) ───────────────────────────────
    # Quando `chat/dist/` não existe, encaminhamos qualquer rota não-API para
    # o servidor de dev externo (Vite em :5173 ou Next.js legado em :3000).
    # Configurável via VECTORA_FRONTEND_URL.
    _frontend_url = os.environ.get(
        "VECTORA_FRONTEND_URL", "http://localhost:5173"
    ).rstrip("/")

    proxy_skip_req_headers = frozenset(
        {"host", "connection", "transfer-encoding", "accept-encoding"}
    )
    proxy_skip_resp_headers = frozenset(
        {"transfer-encoding", "connection", "content-encoding", "content-length"}
    )

    @app.api_route("/{path:path}", methods=["GET", "HEAD"])
    async def _frontend_proxy(request: Request, path: str) -> FastAPIResponse:
        """Proxy reverso: encaminha requests não-API para o Next.js frontend.

        Notas importantes:
        - accept-encoding é suprimido para o upstream receber conteúdo plain
          (sem gzip), evitando mismatch de content-length na resposta.
        - content-length é omitido da resposta — FastAPI recalcula com base
          no conteúdo já descomprimido pelo httpx.
        - Endpoints SSE/streaming do Next.js dev (ex: /_next/webpack-hmr)
          são redirecionados direto para o frontend, sem buffer.
        """
        target = f"{_frontend_url}/{path}"
        if request.url.query:
            target = f"{target}?{request.url.query}"

        fwd_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in proxy_skip_req_headers
        }
        # Pede conteúdo sem compressão — httpx decodifica gzip mas não atualiza
        # content-length, causando leitura truncada no browser.
        fwd_headers["accept-encoding"] = "identity"

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                upstream = await client.request(
                    method=request.method,
                    url=target,
                    headers=fwd_headers,
                    content=await request.body(),
                )
            resp_headers = {
                k: v
                for k, v in upstream.headers.items()
                if k.lower() not in proxy_skip_resp_headers
            }
            return FastAPIResponse(
                content=upstream.content,
                status_code=upstream.status_code,
                headers=resp_headers,
                media_type=upstream.headers.get("content-type"),
            )
        except httpx.ConnectError:
            logger.warning("frontend_proxy: frontend indisponível em %s", _frontend_url)
            # Mensagem hardcoded em pt-BR — backend ainda não tem i18n
            # estruturado (ver `src/ui/strings/` futuro, espelhando o CSV
            # de `chat/lib/i18n/strings.csv.ts`). `.encode("utf-8")` evita
            # restrição ASCII-only dos bytes literals do Python.
            return FastAPIResponse(
                content=(
                    "<html><body><h2>Frontend indisponível</h2>"
                    "<p>Rode <code>pnpm --dir chat dev</code> ou faça o build "
                    "com <code>pnpm --dir chat build</code> para gerar "
                    "<code>chat/dist/</code>.</p></body></html>"
                ).encode(),
                status_code=502,
                media_type="text/html; charset=utf-8",
            )
        except Exception as exc:
            logger.warning(
                "frontend_proxy: erro ao encaminhar /%s [%s]: %s",
                path,
                type(exc).__name__,
                exc or repr(exc),
            )
            return FastAPIResponse(
                content=b'{"detail":"Erro no proxy do frontend"}',
                status_code=502,
                media_type="application/json",
            )

    return app
