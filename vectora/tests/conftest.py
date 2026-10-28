"""Configuração compartilhada por toda a suíte Python (escopo ``tests/``).

⚠️ Rede de segurança contra travamento no shutdown — a CI não finalizava.

Sintoma: o pytest reportava o resultado final ("N passed") e gravava o
``coverage.xml`` normalmente, mas o **processo nunca saía**. O job da CI ficava
horas parado na tela de resultado até ser cancelado manualmente.

Causa: alguns recursos exercitados nos testes deixam **threads não-daemon**
vivas (ex.: o ``Observer`` do watchdog usado no SSE de eventos de workspace,
quando o stream de um teste não é drenado e o ``finally`` que faz
``observer.stop()`` nunca roda). Quando o pytest termina, o CPython chama
``threading._shutdown()``, que faz ``join`` em todas as threads não-daemon —
e fica bloqueado para sempre numa thread que nunca encerra. O resultado já foi
impresso; o interpretador apenas não consegue sair.

Correção: em ``pytest_unconfigure`` — que roda DEPOIS do summary e da gravação
do coverage — registramos (diagnóstico) as threads remanescentes e encerramos
o processo com ``os._exit(code)``, preemptando o ``join`` travado. O código de
saída é preservado, então a CI continua detectando falhas corretamente.

O ideal é também não vazar a thread (ver ``src/api/handlers/workspaces.py``,
onde o observer agora é ``daemon``), mas esta rede garante que NENHUM vazamento
futuro volte a travar o pipeline.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import socket
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

if TYPE_CHECKING:
    import git as gitpython

# Capturado em pytest_sessionfinish e usado no pytest_unconfigure: o
# unconfigure não recebe o exitstatus, então guardamos aqui.
_exit_status: int = 0

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Diretório isolado criado por `pytest_configure` (abaixo), removido em
# `pytest_unconfigure` — `None` quando o processo não é o responsável por
# essa limpeza (VECTORA_HOME já veio setado de fora, ver docstring).
_isolated_vectora_home: str | None = None
_previous_vectora_home: str | None = None


def pytest_configure(config: Any) -> None:
    """Isola `VECTORA_HOME` para a sessão inteira ANTES de qualquer módulo
    de teste ser coletado/importado — rede de segurança contra teste
    vazando pro `~/.vectora` real do usuário.

    Achado ao vivo (não hipotético): duas threads de teste (`thread-dedup-
    e2e`, `tid`) apareceram no `~/.vectora/checkpoints.db` REAL do usuário,
    poluindo a sidebar do app instalado. Causa raiz: `backend.settings.
    settings` (Pydantic Settings) e `backend.workspace.runtime_settings.
    _DB_PATH` são singletons de PROCESSO computados a partir de
    `VECTORA_HOME` no momento do import — `spawned_backend` (fixture
    abaixo) já isola isso corretamente, mas só porque roda o backend num
    SUBPROCESSO com o env var setado antes do import. Qualquer teste que
    importe `backend.*` diretamente no MESMO processo do pytest (sem
    subprocess) — comum, é como a maioria dos testes unitários funciona —
    herda o `VECTORA_HOME` que estava no ambiente no instante do primeiro
    import, que é o real `~/.vectora` se ninguém tiver setado nada antes.

    `pytest_configure` roda antes da coleta de qualquer módulo de teste —
    é o ÚNICO ponto cedo o bastante pra garantir que o PRIMEIRO import de
    `backend.settings` em todo o processo já veja um `VECTORA_HOME`
    isolado, não importa qual teste dispara esse import primeiro.

    Para depuração intencional contra um diretório externo, defina
    `VECTORA_TEST_ALLOW_EXTERNAL_HOME=1`. Por padrão, mesmo que o shell já
    tenha `VECTORA_HOME`, cada processo de pytest recebe um diretório novo;
    isso mantém execuções paralelas e repetidas independentes e impede que
    dados do runtime do desenvolvedor contaminem os testes.
    """
    global _isolated_vectora_home, _previous_vectora_home
    if os.environ.get("VECTORA_TEST_ALLOW_EXTERNAL_HOME") == "1":
        return
    _previous_vectora_home = os.environ.get("VECTORA_HOME")
    home_dir = tempfile.mkdtemp(prefix="vectora-test-home-")
    os.environ["VECTORA_HOME"] = home_dir
    _isolated_vectora_home = home_dir


# DIAGNÓSTICO — hang intermitente e reproduzível só em CI (ubuntu-latest),
# sempre no meio da suíte, sempre dentro do próprio select()/epoll_wait()
# de um `asyncio.Runner` novo (pytest-asyncio cria um por fixture função-
# escopada). Hipótese em teste: esgotamento de file descriptors — cada
# Runner novo monta seu próprio self-pipe, e a thread de fundo do LanceDB
# não expõe shutdown limpo (github.com/lancedb/lancedb/issues/2133),
# candidata a vazar fd/thread ao longo de ~4700 testes. sys.__stderr__ (não
# print comum) de propósito: bypassa o capture do pytest, que bufferiza
# por teste e nunca libera se o processo for morto por os._exit() no
# timeout. Mantido — custo desprezível (1 syscall a cada 20 testes), e a
# curva de fds/threads é o único jeito de confirmar ou descartar a
# hipótese numa run que trava de verdade.
_diag_test_count = 0


def pytest_runtest_teardown(item: Any) -> None:
    """Executa a operação pytest runtest teardown."""
    global _diag_test_count
    _diag_test_count += 1
    if _diag_test_count % 20 != 0:
        return
    stream = sys.__stderr__ or sys.stderr
    import threading

    try:
        fd_count = sum(1 for _ in Path("/proc/self/fd").iterdir())
    except OSError:
        fd_count = -1  # não-Linux (Windows local) — sem /proc
    try:
        stream.write(
            f"[diag fds] teste #{_diag_test_count} ({item.nodeid}): "
            f"fds_abertos={fd_count} threads_vivas={threading.active_count()}\n"
        )
        stream.flush()
    except OSError:
        # Windows runners can detach the pytest console while a test is
        # tearing down (WinError 6).  Diagnostics are best-effort and must
        # never turn a successful test into a teardown error.
        pass


def pytest_sessionfinish(exitstatus: int) -> None:
    """Memoriza o código de saída final para o pytest_unconfigure.

    O pytest injeta apenas os argumentos do hook que declaramos pelo nome —
    por isso pedimos só ``exitstatus`` (o ``session`` não é necessário aqui).
    """
    global _exit_status
    _exit_status = int(exitstatus)


def pytest_unconfigure(config: Any) -> None:
    """Força o término do processo após o pytest concluir todo o relatório.

    Roda no fim do ciclo do pytest, depois que o summary e o coverage já foram
    emitidos — antes do ``threading._shutdown()`` que travaria no join.
    """
    # Remove o VECTORA_HOME isolado criado por `pytest_configure` — precisa
    # rodar ANTES do `os._exit()` abaixo, que pula qualquer cleanup
    # registrado via atexit.
    if _isolated_vectora_home is not None:
        shutil.rmtree(_isolated_vectora_home, ignore_errors=True)
        if _previous_vectora_home is None:
            os.environ.pop("VECTORA_HOME", None)
        else:
            os.environ["VECTORA_HOME"] = _previous_vectora_home

    # Em workers do pytest-xdist NÃO forçamos o exit: quebraria o protocolo de
    # coleta de resultados do processo controlador. Só o principal encerra.
    if hasattr(config, "workerinput"):
        return

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_exit_status)


# ============================================================================
# Backend real spawnado como subprocesso — fica na raiz (não em
# tests/integration/conftest.py) porque também é usado por tests/e2e/
# (test_agent_live_runs.py), que não é subárvore de tests/integration/.
# ============================================================================


def _free_port() -> int:
    """Executa a operação free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_port_open(port: int, timeout_s: float) -> None:
    """Executa a operação wait port open."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    last_error: OSError | None = None
    while loop.time() < deadline:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError as exc:
            last_error = exc
            await asyncio.sleep(0.2)
            continue
        writer.close()
        await writer.wait_closed()
        return
    raise TimeoutError(
        f"porta {port} não abriu em {timeout_s}s (último erro: {last_error})"
    )


async def _drain(stream: asyncio.StreamReader | None) -> None:
    """Consome stdout/stderr do processo pra não travar o pipe do SO."""
    if stream is None:
        return
    while not stream.at_eof():
        await stream.read(4096)


_background_tasks: set[asyncio.Task[None]] = set()


def _track(task: asyncio.Task[None]) -> None:
    """Executa a operação track."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@pytest.fixture(autouse=True)
def _isolate_native_tool_registry() -> Generator[None]:
    """``backend.tools.registry.TOOL_REGISTRY`` é um singleton de processo —
    vários testes definem tools locais via ``@vtool`` com o mesmo nome (ex.
    ``buscar``, repetido em test_*_chat_client.py e test_engine_conversation_
    loop.py). Sem isolamento, rodar a suíte inteira num processo só colide
    ("tool já registrada") mesmo com cada teste passando isolado.

    Não é um snapshot+restore ingênuo: várias tools de produção (`backend.
    tools.context_graph`, etc.) só são importadas — e portanto só registradas
    via `@vtool`, que roda no import — dentro do corpo de algum teste
    (import local, padrão comum no repo), não na coleção. Restaurar cegamente
    pro snapshot pré-teste apagaria esse registro de produção genuíno assim
    que o primeiro teste que o importa terminasse — o próximo teste que
    dependesse dele (ex. `backend.nodes.tools`, que resolve pelo nome no
    TOOL_REGISTRY) quebraria com "tool não registrada", mesmo a tool sendo
    real e o módulo já estar em `sys.modules`.

    Discriminador: uma tool definida DENTRO de um teste (`@vtool` no corpo
    de uma função de teste) tem `__qualname__` contendo `.<locals>.` — só
    essas são removidas no teardown. Tool de módulo de produção (top-level,
    sem `<locals>` no qualname) fica registrada pro resto da sessão, como
    qualquer singleton de import real."""
    from backend.tools.registry import TOOL_REGISTRY

    snapshot_names = set(TOOL_REGISTRY._tools)
    yield
    for name in set(TOOL_REGISTRY._tools) - snapshot_names:
        spec = TOOL_REGISTRY._tools.get(name)
        qualname = getattr(spec.handler, "__qualname__", "") if spec else ""
        if "<locals>" in qualname:
            TOOL_REGISTRY._tools.pop(name, None)


@pytest.fixture(autouse=True)
def _reset_async_singleton_locks() -> None:
    """``asyncio.Lock()`` criado no import de um módulo é um singleton de
    processo — mas ``asyncio_default_fixture_loop_scope = "function"``
    (pyproject.toml) dá a cada teste um event loop novo. Se o lock ficar
    travado (`_locked=True`) quando o loop que o segurava morre — uma task
    cancelada no teardown do teste, por exemplo, nunca chega ao `release()`
    do `__aexit__` — o objeto Lock fica preso nesse estado pra sempre: o
    próximo teste que tentar `async with lock:` (em QUALQUER arquivo, é o
    mesmo singleton de módulo) espera por um `release()` que nunca vai
    acontecer, até o timeout de 120s do pytest-timeout.

    Isso já tinha sido corrigido para `backend.tools.mcp._mcp_lock` (ver
    `_reset_global_client` em `test_tools_mcp.py`) mas não para os outros
    dois locks de singleton do processo — `get_background_worker()`/
    `get_embedding_queue()` — que são exatamente os que travavam em CI
    (sempre o mesmo conjunto de ~13 testes, a partir do primeiro lock
    "envenenado" na sessão). Trocar por um Lock novo a cada teste elimina
    o problema na raiz: nenhum teste herda o estado de lock de outro.

    `threads._db_conn_lock` (guarda o init do singleton de conexão SQLite,
    ver `_get_db()`) é o mesmo tipo de objeto e precisa do mesmo reset —
    o fechamento da conexão em si é responsabilidade de
    `_close_stale_db_conn` (fixture separada, assíncrona, logo abaixo)."""
    import backend.api.handlers.threads as _threads_mod
    import backend.embedding.background as _bg_mod
    import backend.embedding.queue as _queue_mod

    _bg_mod._worker_lock = asyncio.Lock()
    _queue_mod._queue_lock = asyncio.Lock()
    _threads_mod._db_conn_lock = asyncio.Lock()


@pytest_asyncio.fixture(autouse=True)
async def _close_stale_db_conn() -> None:
    """Fecha e descarta ``threads._db_conn`` antes de cada teste.

    ``_get_db()`` só reconecta quando o singleton está ``None`` — testes que
    chamam `_get_db()`/`_ensure_*_table` fora do fixture isolado de
    `test_provider_routing_handler.py` (ex. `test_models_handler.py`,
    `test_share_handler.py`, `test_embedding_dimension_guard.py`,
    `test_background_worker.py`) reaproveitam a conexão real deixada por
    QUALQUER teste anterior na mesma sessão. Se essa conexão nunca foi
    fechada, sua thread de fundo (`aiosqlite.Connection`) e o
    `sqlite3.Connection` real por trás dela continuam vivos e abertos —
    uma segunda conexão real órfã disputando lock de arquivo com a nova,
    mesmo com WAL/busy_timeout configurados. A ordem de coleta de testes
    do pytest não é garantida entre sistemas de arquivo (Windows vs Linux
    enumeram diretórios em ordens diferentes), então esse encadeamento não
    reproduz de forma confiável fora do runner original — fechar
    incondicionalmente antes de cada teste elimina a classe inteira do
    problema, independente de qual teste rodou antes.

    O ``close()`` em si nunca pode travar a suíte inteira: ele enfileira
    um job na fila da thread de fundo da conexão e espera a resposta
    (``aiosqlite/core.py::_execute``) sem timeout próprio — se essa thread
    ficou presa esperando algo de um teste anterior cujo event loop já
    morreu (task fire-and-forget nunca awaited, o mesmo padrão descrito em
    `_no_thread_persistence` em `tests/unit/conftest.py`), o `close()`
    nunca retorna. Por isso a referência é descartada ANTES da tentativa
    de fechar (nenhum teste seguinte pode voltar a tocar essa conexão
    mesmo que o close trave) e o close roda sob `wait_for` com timeout
    curto, best-effort — uma conexão real órfã que nunca fecha é um risco
    aceitável (no pior caso, volta a colidir com a próxima), travar a
    suíte inteira não é."""
    import backend.api.handlers.threads as _threads_mod

    conn = _threads_mod._db_conn
    _threads_mod._db_conn = None
    if conn is not None:
        with contextlib.suppress(Exception, asyncio.TimeoutError):
            await asyncio.wait_for(conn.close(), timeout=5.0)


@pytest_asyncio.fixture
async def spawned_backend(tmp_path: Path):
    """Backend real (``python -m backend.main start``) rodando num
    ``VECTORA_HOME`` isolado. Não exige ``frontend/dist`` (diferente de
    ``_spawned_backend`` em ``test_frontend_served_lifecycle.py``, que serve
    estático — este fixture é pra exercitar a API real via HTTP).

    Sem ``VECTORA_DESKTOP=1``: em Linux/macOS esse modo troca o transporte
    inteiro para Unix Domain Socket (nenhuma porta TCP é aberta — desktop
    fala por IPC, nunca TCP), o que este fixture não consegue exercitar
    via HTTP simples. No Windows o modo desktop mantém a porta TCP aberta
    em paralelo ao named pipe, então a lacuna só aparece em CI Linux.

    Yields ``(base_url, port)``.
    """
    port = _free_port()
    env = dict(os.environ)
    env["VECTORA_HOME"] = str(tmp_path)
    env["VECTORA_UVICORN_LOG_LEVEL"] = "warning"
    env["VECTORA_SKIP_STATIC"] = "1"

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "backend.main",
        "start",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _track(asyncio.create_task(_drain(proc.stdout)))
    _track(asyncio.create_task(_drain(proc.stderr)))
    try:
        await _wait_port_open(port, timeout_s=60.0)
        yield f"http://127.0.0.1:{port}", port
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()


# ============================================================================
# Git repo + workspace reais — reutilizável por qualquer teste que precise
# de um repositório git de verdade (evita `git.Repo.init` inline repetido).
# ============================================================================


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> gitpython.Repo:
    """Repositório git real, com um commit inicial (`README.md`), pronto
    para os testes exercitarem status/diff/branch/worktree de verdade."""
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Vectora Test")
        cfg.set_value("user", "email", "test@vectora.chat")
    readme = tmp_path / "README.md"
    readme.write_text("# test repo\n", encoding="utf-8")
    repo.index.add([str(readme)])
    repo.index.commit("initial commit")
    return repo


@pytest.fixture
def real_workspace(tmp_git_repo: gitpython.Repo, tmp_path: Path) -> Generator[str]:
    """Registra `tmp_git_repo` no `workspace_registry` real e devolve o
    `workspace_id` — usado por testes que exercitam handlers/tools que
    resolvem workspace_id -> path via o registry de verdade."""
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get_or_create(str(tmp_path))
    try:
        yield ws.id
    finally:
        workspace_registry.delete(ws.id)
