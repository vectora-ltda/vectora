"""Vectora CLI — ponto de entrada unificado.

vectora                 imprime o help (descobre a CLI de configuração)
vectora start           sobe backend + SPA (fullstack)
vectora start --headless  sobe sem janela (bandeja + backend)
vectora web             sobe só como webapp — sem Electron, sem bandeja
vectora run --task "..."  roda uma tarefa uma vez e sai — sem servidor (CI)

Configuração (operacional, para VPS via SSH):
  vectora config                 mostra ~/.vectora/settings.json
  vectora config --set K=V       edita uma chave de settings
  vectora config keys            wizard de API keys + LLM provider
  vectora config docker [up|down|status]
  vectora config qdrant <url> [--api-key KEY]
  vectora config redis <url>
  vectora storage <ação>         migrations, diagnóstico, backup/restore
  vectora auth <ação>            signup | login | logout | whoami | refresh
  vectora sessions               lista as sessões salvas
"""

from __future__ import annotations

import os

# GitPython chama Git.refresh() na importação (`import git`) e, por padrão,
# levanta ImportError se não achar o executável git no PATH — sem isso, o
# processo inteiro crasha no boot em qualquer máquina sem git instalado.
# Precisa rodar antes do primeiro `import git` transitivo (tools/git.py,
# persistence/checkpoint.py); tools de git degradam de forma limpa quando
# chamadas sem o binário disponível.
os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")

import argparse
import asyncio
import contextlib
import logging
import signal
import socket
import sys
from pathlib import Path
from typing import Any


def _find_free_port(preferred: int | None = None) -> int:
    """Devolve uma porta TCP livre em 127.0.0.1.

    Se ``preferred`` for fornecida e estiver disponível, é retornada como está;
    caso contrário (ou se já estiver ocupada), o SO escolhe uma porta efêmera.
    """
    if preferred is not None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# Configure UTF-8 on Windows before any output
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Project root no sys.path (necessário ao rodar como script direto). Usa
# parent.parent (raiz), não parent (pacote src/), para não sombrear pacotes de
# terceiros com o mesmo nome (ex.: src/mcp/ sombrearia o pacote `mcp`).
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from backend.services.log_setup import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Terminal shutdown helpers
# ---------------------------------------------------------------------------


def _should_install_terminal_signals(env: dict[str, str]) -> bool:
    """Decide se o handler de sinal customizado (Ctrl+C/SIGTERM/SIGHUP →
    shutdown gracioso do uvicorn, que aciona o `finally` do lifespan e
    limpa sidecars como o NATS) deve ser instalado.

    `VECTORA_DESKTOP=1` sozinho não diz quem é dono do processo: é setado
    tanto quando o Electron spawna o backend (produção — o Electron já
    mata a árvore inteira via `taskkill /T /F`, um handler custom aqui
    disputaria esse shutdown) quanto quando é o PRÓPRIO backend quem se
    autoelege primário e sobe o Electron como seu sidecar (`_run_start`,
    modo backend-primário em dev) — nesse segundo caso não existe nenhum
    Electron dono cuidando da limpeza do lado de fora, e sem handler o
    processo nunca aciona o shutdown gracioso, deixando sidecars filhos
    (nats-server) órfãos. `VECTORA_SPAWN_ELECTRON=1` só é setado nesse
    segundo caso — é o sinal de "este processo é dono de si mesmo".
    """
    desktop = bool(env.get("VECTORA_DESKTOP"))
    owns_itself = bool(env.get("VECTORA_SPAWN_ELECTRON"))
    if desktop and not owns_itself:
        return False
    if owns_itself:
        # Backend-primário em dev: sempre instala, mesmo sem tty — é o único
        # dono do processo, e sys.stdin.isatty() pode vir False dependendo
        # de como `uv run`/o shell pai herdou os handles de stdin, gate que
        # antes calava o Ctrl+C sem nenhum sinal disso pro usuário (o único
        # jeito de fechar virava o Sair da bandeja do Electron).
        return True
    return sys.stdin.isatty()


def _install_terminal_signals(server: Any, icon_ref: list[Any]) -> None:
    """Instala handlers de SIGINT/SIGTERM/SIGHUP para shutdown limpo — ver
    `_should_install_terminal_signals` para quando isso é chamado."""

    def _shutdown(_signum: int, _frame: Any) -> None:
        server.should_exit = True
        if icon_ref[0] is not None:
            icon_ref[0].stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _shutdown)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    from backend.version import __version__

    parser = argparse.ArgumentParser(
        prog="vectora",
        description="Vectora — workspace de IA com RAG nativo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""exemplos:
  vectora start                        sobe backend + SPA (fullstack)
  vectora start --headless             servidor headless (VPS/systemd)
  vectora start --port 9000            porta customizada
  vectora web                          webapp puro — sem Electron, sem bandeja

  vectora config                       mostra configuração completa
  vectora config keys                  wizard: API keys + LLM provider
  vectora config --set active_model=gemini-2.5-pro
  vectora config --set storage_mode=complete
  vectora config --set postgres_dsn=postgresql+asyncpg://user:pass@host/db
  vectora config --set google_api_key=AIza...
  vectora config docker up             sobe Postgres + Redis + Qdrant local
  vectora config qdrant https://… --api-key KEY
  vectora config redis redis://…

  vectora auth login                   autentica no servidor Vectora
  vectora sessions                     lista as sessões salvas
  vectora storage info                 status dos backends de dados
  vectora storage migrate plan         mostra o plano sem alterar o banco
  vectora storage migrate upgrade      aplica migrations SQLite pendentes
""",
    )

    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"vectora {__version__}",
    )

    sub = parser.add_subparsers(dest="command", metavar="subcommand")

    # ── start — backend + SPA (fullstack/headless) ─────────────────────────────
    start_p = sub.add_parser(
        "start",
        help="Sobe o Vectora completo (backend + SPA)",
        description=(
            "Sobe o backend completo (FastAPI) servindo a SPA via uvicorn.\n\n"
            "  vectora start              -> fullstack (janela quando há desktop)\n"
            "  vectora start --headless   -> só bandeja + backend, sem janela\n\n"
            "Em host sem display (VPS/Docker), roda como servidor puro."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    start_p.add_argument(
        "--headless",
        action="store_true",
        help="Não abre janela — mantém backend + bandeja ativos.",
    )
    start_p.add_argument("--host", default="0.0.0.0", help="Host de escuta")  # noqa: S104  # nosec B104
    start_p.add_argument("--port", type=int, default=None, help="Porta (default: 8080)")
    start_p.add_argument(
        "--ssl-certfile",
        metavar="PEM",
        default=None,
        help=(
            "Certificado TLS (PEM fullchain) — sobe em https://. Também via env "
            "SSL_CERTFILE. Necessário para Secure Context ao acessar via IP de LAN."
        ),
    )
    start_p.add_argument(
        "--ssl-keyfile",
        metavar="PEM",
        default=None,
        help="Chave privada TLS correspondente. Também via env SSL_KEYFILE.",
    )

    # ── web — força modo webapp puro (sem Electron, sem bandeja) ──────────────
    web_p = sub.add_parser(
        "web",
        help="Sobe o Vectora como webapp puro (sem Electron, sem bandeja)",
        description=(
            "Sobe o backend completo (FastAPI) servindo a SPA via uvicorn,\n"
            "acessível só pelo browser — nunca abre janela Electron nem ícone de\n"
            "bandeja, mesmo numa máquina com display. Equivalente ao modo\n"
            "servidor/VPS, mas utilizável em qualquer máquina."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    web_p.add_argument("--host", default="0.0.0.0", help="Host de escuta")  # noqa: S104  # nosec B104
    web_p.add_argument("--port", type=int, default=None, help="Porta (default: 8080)")
    web_p.add_argument(
        "--ssl-certfile",
        metavar="PEM",
        default=None,
        help=(
            "Certificado TLS (PEM fullchain) — sobe em https://. Também via env "
            "SSL_CERTFILE. Necessário para Secure Context ao acessar via IP de LAN."
        ),
    )
    web_p.add_argument(
        "--ssl-keyfile",
        metavar="PEM",
        default=None,
        help="Chave privada TLS correspondente. Também via env SSL_KEYFILE.",
    )

    # ── run — one-shot, sem servidor (CI, Vectora Bot for GHA) ────────────────
    run_p = sub.add_parser(
        "run",
        help="Roda uma tarefa uma única vez e sai — sem servidor (CI)",
        description=(
            "Sobe o motor nativo, roda run_conversation uma vez com a tarefa\n"
            "dada e imprime a resposta final em stdout. Sem servidor FastAPI,\n"
            "sem HITL interativo (permission_mode=auto — não há UI de\n"
            "aprovação num runner de CI), sem estado persistente entre\n"
            "execuções a menos que VECTORA_HOME já esteja setado.\n\n"
            'Exemplo: vectora run --task "revise este PR" < diff.txt'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run_p.add_argument(
        "--task",
        default=None,
        help="Tarefa a executar. Se omitido, lê de stdin.",
    )
    run_p.add_argument(
        "--model",
        default=None,
        metavar="PROVIDER:MODEL_ID",
        help=(
            "Modelo a usar (ex.: google_genai:gemini-3-flash). Não existe "
            "resolução de 'modelo padrão' no servidor — obrigatório aqui ou "
            "via env var VECTORA_MODEL."
        ),
    )

    # ── config — settings + keys/docker/qdrant/redis ──────────────────────────
    config_p = sub.add_parser(
        "config",
        help="Configuração do aplicativo (settings, keys, docker, qdrant, redis)",
        description=(
            "Sem ação, mostra ou edita ~/.vectora/settings.json.\n"
            "Com ação:\n"
            "  keys                 wizard de API keys + LLM provider\n"
            "  docker [up|down|status]  infra local (Postgres, Redis, Qdrant)\n"
            "  qdrant <url> [--api-key KEY]  testa e persiste Qdrant\n"
            "  redis <url>          testa e persiste Redis\n"
            "  integrations|connect|preferences [--get KEY]... [--set KEY=VALUE]...\n"
            "                       schema declarativo (backend/config/registry.py) —\n"
            "                       mesmas categorias do frontend (Ambiente/Preferências)\n"
            "  provider-routing|memory|account --list [--user-id ID]\n"
            "                       recursos de coleção do registry (backend/config/\n"
            "                       collections.py) — memory/account são por usuário\n"
            "                       (default: 'local', o mesmo sentinela do desktop)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    config_p.add_argument(
        "config_action",
        nargs="?",
        default=None,
        choices=[
            "keys",
            "docker",
            "qdrant",
            "redis",
            "integrations",
            "connect",
            "preferences",
            "provider-routing",
            "memory",
            "account",
        ],
        help="Ação de configuração (sem ação = mostra/edita settings).",
    )
    config_p.add_argument(
        "config_arg",
        nargs="?",
        default=None,
        help="Argumento da ação: up|down|status (docker) ou URL (qdrant/redis).",
    )
    config_p.add_argument(
        "--api-key",
        dest="api_key",
        default=None,
        help="[qdrant] API key opcional.",
    )
    config_p.add_argument(
        "--set",
        metavar="KEY=VALUE",
        action="append",
        dest="set_values",
        help=(
            "Edita uma chave. Repetível. "
            "LLM: active_provider, active_model. "
            "Storage: storage_mode, postgres_dsn, redis_url, qdrant_url, qdrant_api_key. "
            "API keys: google_api_key, openai_api_key, anthropic_api_key, cohere_api_key, tavily_api_key. "
            "Categorias do registry (integrations/connect/preferences): mesmo formato KEY=VALUE."
        ),
    )
    config_p.add_argument(
        "--get",
        metavar="KEY",
        action="append",
        dest="get_values",
        help="[integrations|connect|preferences] Lê uma chave do registry. Repetível.",
    )
    config_p.add_argument(
        "--list",
        action="store_true",
        dest="list_collection",
        help="[provider-routing|memory|account] Lista os itens do recurso de coleção.",
    )
    config_p.add_argument(
        "--user-id",
        dest="user_id",
        default=None,
        help="[memory|account] Dono do recurso per-usuário (default: 'local').",
    )

    # ── sessions ──────────────────────────────────────────────────────────────
    sub.add_parser(
        "sessions",
        help="Lista todas as sessões salvas",
        description="Mostra sessões com ID, data, contagem de mensagens e diretório.",
    )

    # ── doctor ────────────────────────────────────────────────────────────────
    doctor_p = sub.add_parser(
        "doctor",
        help="Encontra e limpa sidecars órfãos (nats-server)",
        description=(
            "Varre o sistema por processos nats-server órfãos (por nome de "
            "imagem, não só os PIDs conhecidos) e oferece finalizá-los."
        ),
    )
    doctor_p.add_argument(
        "--yes",
        action="store_true",
        help="Finaliza sem pedir confirmação (uso não-interativo/scriptável).",
    )

    # ── storage ───────────────────────────────────────────────────────────────
    storage_p = sub.add_parser(
        "storage",
        help="Storage: migrations, diagnóstico, backup/restore, wizard BaaS",
        description=(
            "Comandos de storage:\n"
            "  info               status de saúde de todos os backends\n"
            "  up / down          sobe/para Postgres+pgvector, Redis e Qdrant\n"
            "  test <DSN>         testa conectividade a um banco\n"
            "  wizard             configura o backend interativamente (BaaS)\n"
            "  migrate status|upgrade\n"
            "  backup / restore <arquivo>"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    storage_p.add_argument(
        "action",
        nargs="?",
        choices=[
            "info",
            "up",
            "down",
            "test",
            "wizard",
            "migrate",
            "backup",
            "restore",
        ],
        default="info",
        help="Ação de storage (default: info)",
    )
    storage_p.add_argument(
        "subaction",
        nargs="?",
        default=None,
        help="Sub-ação: status/upgrade (migrate), DSN (test), arquivo (restore)",
    )
    storage_p.add_argument(
        "version", nargs="?", default=None, help="Parâmetro extra (migrações de dados)"
    )
    storage_p.add_argument(
        "--db", metavar="PATH", default=None, help="Caminho do SQLite (settings.db_dsn)"
    )
    storage_p.add_argument(
        "--output", metavar="FILE", default=None, help="Arquivo de saída do backup"
    )

    # ── auth ──────────────────────────────────────────────────────────────────
    auth_p = sub.add_parser(
        "auth",
        help="Autenticação no servidor Vectora",
        description=(
            "Comandos de autenticação:\n"
            "  signup | login | logout | whoami | refresh\n\n"
            "Sem login, o CLI opera como root local (acesso via filesystem)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    auth_p.add_argument(
        "action",
        nargs="?",
        choices=["signup", "login", "logout", "whoami", "refresh"],
        help="Ação de autenticação.",
    )

    return parser


# ---------------------------------------------------------------------------
# start — backend + SPA via uvicorn
# ---------------------------------------------------------------------------


def _run_start(args: argparse.Namespace, *, force_web: bool = False) -> None:
    """``vectora start`` — sobe FastAPI servindo a SPA via uvicorn.

    Em ``--headless`` registra ``VECTORA_HEADLESS=1`` para a bandeja/Electron
    decidirem não abrir janela; o servidor em si é idêntico.

    ``force_web=True`` (``vectora web``) vai além de ``--headless``: nem a
    bandeja do sistema sobe — só o servidor ASGI, para uso via browser em
    qualquer máquina, com ou sem display.
    """
    import uvicorn

    from backend.api.server import create_app

    headless = force_web or getattr(args, "headless", False)
    if headless:
        os.environ["VECTORA_HEADLESS"] = "1"

    # Precedência da porta: --port > VECTORA_PORT (env do Electron) > 8080.
    port = args.port or int(os.environ.get("VECTORA_PORT") or 0) or 8080
    os.environ["VECTORA_PORT"] = str(port)  # lido pelo sidecar Electron no lifespan
    uvicorn_log_level = os.environ.get("VECTORA_UVICORN_LOG_LEVEL", "warning")

    # Backend-primário mesmo em dev: em produção é o Electron quem spawna o
    # backend (VECTORA_DESKTOP=1 já vem setado por ele). Quando rodado direto
    # (`uv run vectora start`/binário fora do Electron) e nenhum dos dois já
    # está setado, este processo se autoelege "primário" — só a DECISÃO
    # acontece aqui (cedo, porque também define o transporte IPC logo
    # abaixo); o spawn em si roda depois, de dentro do lifespan async do
    # FastAPI (backend/services/electron_sidecar.py), como qualquer outro
    # sidecar (NATS) — não do bootstrap síncrono da CLI. Mantém `vectora
    # start` leve pra quem só quer a API REST: o processo ASGI decide
    # sozinho, no seu próprio startup, se faz sentido subir uma janela.
    from backend.services.electron_sidecar import should_spawn_electron

    if should_spawn_electron():
        os.environ["VECTORA_DESKTOP"] = "1"
        os.environ["VECTORA_SPAWN_ELECTRON"] = "1"
        logger.info(
            "Electron (dev) resolvido — sobe como sidecar no startup do FastAPI"
        )

    # TLS opcional — CLI tem prioridade; settings (env SSL_CERTFILE/SSL_KEYFILE
    # ou ~/.vectora/.env) é o fallback. Com cert+key o uvicorn serve https://,
    # necessário para Secure Context (crypto.randomUUID etc.) via IP de LAN.
    ssl_certfile = args.ssl_certfile
    ssl_keyfile = args.ssl_keyfile
    if not ssl_certfile or not ssl_keyfile:
        try:
            from backend.settings import settings as _settings

            ssl_certfile = ssl_certfile or _settings.ssl_certfile
            ssl_keyfile = ssl_keyfile or _settings.ssl_keyfile
        except Exception:
            pass
    if bool(ssl_certfile) != bool(ssl_keyfile):
        print(
            "❌ TLS requer certificado E chave: informe --ssl-certfile e "
            "--ssl-keyfile (ou SSL_CERTFILE/SSL_KEYFILE)."
        )
        sys.exit(1)
    use_tls = bool(ssl_certfile and ssl_keyfile)

    # Transporte desktop é IPC real: sob VECTORA_DESKTOP=1 (Electron) o backend
    # não expõe porta TCP ao SO. Em Linux/macOS usa unix socket; no Windows usa
    # named pipe (ipc_pipe_win). Web/VPS mantém TCP (servidor de rede, por design).
    uds_path: str | None = None
    if os.environ.get("VECTORA_DESKTOP") and sys.platform != "win32":
        from backend.settings import settings as _settings_ipc

        sock_dir = _settings_ipc.vectora_home
        sock_dir.mkdir(parents=True, exist_ok=True)
        uds_path = str(sock_dir / "vectora.sock")
        with contextlib.suppress(FileNotFoundError):
            Path(uds_path).unlink()

    from backend.version import __version__

    app = create_app()
    scheme = "https" if use_tls else "http"
    if uds_path:
        logger.info(
            "Iniciando Vectora v%s via unix socket %s (desktop IPC)",
            __version__,
            uds_path,
        )
        config = uvicorn.Config(
            app,
            uds=uds_path,
            log_level=uvicorn_log_level,
            access_log=False,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
        )
    else:
        logger.info(
            "Iniciando Vectora v%s em %s://%s:%d (%s)",
            __version__,
            scheme,
            args.host,
            port,
            "web" if force_web else ("headless" if headless else "fullstack"),
        )
        config = uvicorn.Config(
            app,
            host=args.host,
            port=port,
            log_level=uvicorn_log_level,
            access_log=False,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
        )
    server = uvicorn.Server(config)

    # Aplicado ANTES do branch Windows named-pipe abaixo — esse branch tem um
    # `return` próprio, e sem instalar o handler aqui primeiro ele nunca seria
    # alcançado nesse caminho (o mais comum em dev desktop no Windows).
    icon_ref: list[Any] = [None]
    if _should_install_terminal_signals(dict(os.environ)):
        _install_terminal_signals(server, icon_ref)

    # Windows + VECTORA_DESKTOP: named pipe em vez de TCP — nenhuma porta TCP é
    # exposta ao SO. O Electron conecta via \\.\pipe\vectora-<pid>, lido de stdout.
    if sys.platform == "win32" and os.environ.get("VECTORA_DESKTOP"):
        from backend.services.ipc_pipe_win import PIPE_ENV_VAR, pipe_name, serve_pipe

        _pipe = pipe_name()
        os.environ[PIPE_ENV_VAR] = _pipe
        print(f"{PIPE_ENV_VAR}={_pipe}", flush=True)

        async def _run_win() -> None:
            pipe_task = asyncio.create_task(serve_pipe(_pipe, "127.0.0.1", port))
            try:
                await server.serve()
            finally:
                pipe_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pipe_task

        asyncio.run(_run_win())
        # Mesmo `return` cedo do ramo Windows+desktop precisa do os._exit(0)
        # abaixo — sem ele, threads não-daemon (httpx/SQLite do tracer)
        # mantêm o interpreter vivo mesmo após o shutdown gracioso
        # do uvicorn (Ctrl+C nunca fechava o processo; só o kill via tray
        # do Electron encerrava, por caminho totalmente diferente).
        logger.info("Vectora: encerrando processo")
        os._exit(0)
        return  # pragma: no cover - os._exit não retorna fora de teste

    if force_web:
        # vectora web: nem a bandeja sobe — só o servidor ASGI puro, igual ao
        # fallback "sem display" de run_server_with_tray, mas explícito
        # mesmo em máquina com display (Ctrl+C encerra via os sinais acima).
        logger.warning(
            "\n\n  ✨  Vectora rodando em %s://localhost:%d — acesse pelo browser.\n",
            scheme,
            port,
        )
        asyncio.run(server.serve())
    else:
        # Sobe o servidor e, quando há display, a bandeja do sistema (Python).
        # Sem display (VPS/Docker) ou sem pystray, degrada para servidor puro.
        # A bandeja bloqueia a main thread até "Sair"; o servidor roda em
        # thread de fundo.
        from backend.services.tray import run_server_with_tray

        run_server_with_tray(
            server,
            f"{scheme}://localhost:{port}",
            headless=headless,
            icon_ref=icon_ref,
        )

    # Retorno do tray/servidor = shutdown concluído. No Windows, threads
    # não-daemon de libs externas (httpx, SQLite do tracer) mantêm o
    # interpreter vivo; os._exit ignora-as e libera o terminal. Os recursos
    # críticos já foram fechados no lifespan.
    logger.info("Vectora: encerrando processo")
    os._exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _run_config_command(args: argparse.Namespace) -> None:
    from backend.cli.config import run_config

    run_config(args)


def _run_sessions_command(_args: argparse.Namespace) -> None:
    from backend.cli.sessions import run_sessions

    run_sessions()


def _run_storage_command(args: argparse.Namespace) -> None:
    from backend.cli.storage import run_storage

    run_storage(args)


def _run_doctor_command(args: argparse.Namespace) -> None:
    from backend.cli.doctor import run_doctor

    run_doctor(args)


def _run_run_task_command(args: argparse.Namespace) -> None:
    from backend.cli.run_task import run_run_task

    run_run_task(args)


def _run_auth_command(args: argparse.Namespace) -> None:
    from backend.auth import (
        cmd_login,
        cmd_logout,
        cmd_refresh,
        cmd_signup,
        cmd_whoami,
    )

    action = getattr(args, "action", None)
    handlers = {
        "signup": cmd_signup,
        "login": cmd_login,
        "logout": cmd_logout,
        "whoami": cmd_whoami,
        "refresh": cmd_refresh,
    }
    handler = handlers.get(action or "")
    if handler is None:
        print(
            "Uso: vectora auth <ação>\n"
            "Ações: signup | login | logout | whoami | refresh\n"
            "Exemplo: vectora auth login"
        )
        sys.exit(1)
    sys.exit(handler(args))


# Subcomandos que só precisam de "importa o módulo, chama o handler com
# args" — start/web (têm variantes force_web) e auth (tratado à parte
# acima, mas despachado pela mesma tabela) ficam fora por clareza no
# restante de `run()`.
_COMMAND_HANDLERS: dict[str, Any] = {
    "config": _run_config_command,
    "sessions": _run_sessions_command,
    "storage": _run_storage_command,
    "doctor": _run_doctor_command,
    "auth": _run_auth_command,
    "run": _run_run_task_command,
}


def run() -> None:
    """Ponto de entrada síncrono — comando ``vectora``."""
    parser = _build_parser()
    args = parser.parse_args()

    command = getattr(args, "command", None)

    if command is None:
        parser.print_help()
        return

    if command == "start":
        _run_start(args)
        return

    if command == "web":
        _run_start(args, force_web=True)
        return

    handler = _COMMAND_HANDLERS.get(command)
    if handler is not None:
        handler(args)
        return

    parser.print_help()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        run()
