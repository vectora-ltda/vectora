"""Testes do transporte IPC desktop (Named Pipe / UDS).

Verifica que em modo VECTORA_DESKTOP=1:
  - Linux/macOS: uvicorn.Config recebe uds=<path> e não host=<ip>
  - Windows: VECTORA_IPC_PIPE é setado em os.environ e impresso em stdout

Os testes NÃO sobem servidor real — todos os componentes bloqueantes são
substituídos por mocks.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# ipc_pipe_win — pipe_name e PIPE_ENV_VAR
# ---------------------------------------------------------------------------


def test_pipe_name_contains_pid() -> None:
    """pipe_name() inclui PID do processo — garante unicidade por execução."""
    from backend.services.ipc_pipe_win import pipe_name

    name = pipe_name()
    assert str(os.getpid()) in name


def test_pipe_name_windows_format() -> None:
    """pipe_name() retorna caminho no formato UNC do Windows."""
    from backend.services.ipc_pipe_win import pipe_name

    name = pipe_name()
    assert name.startswith("\\\\.\\pipe\\vectora-")


def test_pipe_env_var_constant() -> None:
    """PIPE_ENV_VAR é o valor que o Electron procura em stdout."""
    from backend.services.ipc_pipe_win import PIPE_ENV_VAR

    assert PIPE_ENV_VAR == "VECTORA_IPC_PIPE"


# ---------------------------------------------------------------------------
# UDS — Linux/macOS: uvicorn.Config recebe uds=, não host=
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="UDS só em Linux/macOS")
def test_desktop_uds_config_not_tcp() -> None:
    """Com VECTORA_DESKTOP=1 em Linux/macOS, uvicorn.Config recebe uds=<path>."""
    import argparse

    captured_config_kwargs: dict[str, Any] = {}

    class _FakeConfig:
        def __init__(self, *_: Any, **kwargs: Any) -> None:
            captured_config_kwargs.update(kwargs)

    class _FakeServer:
        should_exit = False

        def __init__(self, *_: Any) -> None:
            pass

    args = argparse.Namespace(
        headless=False,
        host="0.0.0.0",  # noqa: S104
        port=None,
        ssl_certfile=None,
        ssl_keyfile=None,
    )

    with (
        patch.dict(os.environ, {"VECTORA_DESKTOP": "1"}, clear=False),
        patch("uvicorn.Config", side_effect=_FakeConfig),
        patch("uvicorn.Server", return_value=_FakeServer()),
        patch("backend.api.server.create_app", return_value=MagicMock()),
        patch("backend.main._start_vite_dev", return_value=None, create=True),
        patch("backend.main._kill_vite", create=True),
        patch("backend.services.tray.run_server_with_tray", return_value=None),
        patch("os._exit"),
    ):
        from backend.main import _run_start

        _run_start(args)

    assert "uds" in captured_config_kwargs, "uvicorn.Config deveria receber uds="
    assert captured_config_kwargs["uds"].endswith("vectora.sock")
    assert "host" not in captured_config_kwargs, "Não deve abrir porta TCP no desktop"


@pytest.mark.skipif(sys.platform == "win32", reason="UDS só em Linux/macOS")
def test_desktop_uds_path_under_home_vectora() -> None:
    """O socket UDS fica em <vectora_home>/vectora.sock — ``~/.vectora`` só
    quando ``VECTORA_HOME`` não está setado; a suíte inteira roda com
    ``VECTORA_HOME`` isolado num diretório temp (`tests/conftest.py`), então
    o caminho esperado precisa refletir isso, não o home real do usuário."""
    from backend.settings import _default_vectora_home

    expected = str(_default_vectora_home() / "vectora.sock")

    import argparse

    captured: dict[str, str] = {}

    class _FakeConfig:
        def __init__(self, *_: Any, **kwargs: Any) -> None:
            if "uds" in kwargs:
                captured["uds"] = kwargs["uds"]

    args = argparse.Namespace(
        headless=False,
        host="0.0.0.0",  # noqa: S104
        port=None,
        ssl_certfile=None,
        ssl_keyfile=None,
    )

    with (
        patch.dict(os.environ, {"VECTORA_DESKTOP": "1"}, clear=False),
        patch("uvicorn.Config", side_effect=_FakeConfig),
        patch("uvicorn.Server", return_value=MagicMock()),
        patch("backend.api.server.create_app", return_value=MagicMock()),
        patch("backend.main._start_vite_dev", return_value=None, create=True),
        patch("backend.main._kill_vite", create=True),
        patch("backend.services.tray.run_server_with_tray"),
        patch("os._exit"),
    ):
        from backend.main import _run_start

        _run_start(args)

    assert captured.get("uds") == expected


@pytest.mark.skipif(sys.platform == "win32", reason="UDS só em Linux/macOS")
def test_non_desktop_uses_tcp_host() -> None:
    """Sem VECTORA_DESKTOP, uvicorn.Config recebe host= (modo servidor/VPS)."""
    import argparse

    captured_config_kwargs: dict[str, Any] = {}

    class _FakeConfig:
        def __init__(self, *_: Any, **kwargs: Any) -> None:
            captured_config_kwargs.update(kwargs)

    args = argparse.Namespace(
        headless=False,
        host="0.0.0.0",  # noqa: S104
        port=8080,
        ssl_certfile=None,
        ssl_keyfile=None,
    )

    env = {k: v for k, v in os.environ.items() if k != "VECTORA_DESKTOP"}

    with (
        patch.dict(os.environ, env, clear=True),
        patch("uvicorn.Config", side_effect=_FakeConfig),
        patch("uvicorn.Server", return_value=MagicMock()),
        patch("backend.api.server.create_app", return_value=MagicMock()),
        patch("backend.main._start_vite_dev", return_value=None, create=True),
        patch("backend.main._kill_vite", create=True),
        patch("backend.services.tray.run_server_with_tray"),
        patch("os._exit"),
        # Neutraliza a auto-eleição do modo backend-primário — este teste
        # cobre especificamente o caminho servidor/VPS sem Electron; não deve
        # depender de um build de dev do Electron estar (ou não) presente no
        # disco de quem roda o teste (padrão: nunca contar com ausência de
        # binário/config opcional, sempre neutralizar explicitamente).
        patch(
            "backend.services.electron_launcher.resolve_electron_launch",
            return_value=None,
        ),
    ):
        from backend.main import _run_start

        _run_start(args)

    assert captured_config_kwargs["host"] == "0.0.0.0"  # noqa: S104
    assert "uds" not in captured_config_kwargs
    assert "VECTORA_DESKTOP" not in os.environ


# ---------------------------------------------------------------------------
# Windows named pipe — VECTORA_IPC_PIPE setado em os.environ
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Named pipe só no Windows")
def test_desktop_windows_sets_pipe_env(capsys: pytest.CaptureFixture[str]) -> None:
    """Com VECTORA_DESKTOP=1 no Windows, VECTORA_IPC_PIPE é setado e impresso."""
    import argparse

    args = argparse.Namespace(
        headless=False,
        host="0.0.0.0",  # noqa: S104
        port=8080,
        ssl_certfile=None,
        ssl_keyfile=None,
    )

    pipe_val_captured: list[str] = []

    with (
        patch.dict(os.environ, {"VECTORA_DESKTOP": "1"}, clear=False),
        patch("backend.api.server.create_app", return_value=MagicMock()),
        patch("backend.main._start_vite_dev", return_value=None, create=True),
        patch(
            "backend.services.ipc_pipe_win.serve_pipe",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
        patch("uvicorn.Config", return_value=MagicMock()),
        patch(
            "uvicorn.Server", return_value=MagicMock(serve=AsyncMock(return_value=None))
        ),
        patch("asyncio.run", side_effect=asyncio.CancelledError),
    ):
        from backend.main import _run_start

        with contextlib.suppress(asyncio.CancelledError, SystemExit, Exception):
            _run_start(args)

        # Checar env DENTRO do patch.dict antes de ser revertido
        assert "VECTORA_IPC_PIPE" in os.environ
        pipe_val_captured.append(os.environ["VECTORA_IPC_PIPE"])

    pipe_val = pipe_val_captured[0]
    assert pipe_val.startswith("\\\\.\\pipe\\vectora-")
    assert str(os.getpid()) in pipe_val

    captured = capsys.readouterr()
    assert "VECTORA_IPC_PIPE=" in captured.out


@pytest.mark.skipif(sys.platform != "win32", reason="Named pipe só no Windows")
def test_desktop_windows_no_tcp_host() -> None:
    """No modo Windows desktop, uvicorn.Config recebe port= efêmera, não host fixo."""
    import argparse

    captured_config_kwargs: dict[str, Any] = {}

    class _FakeConfig:
        def __init__(self, *_: Any, **kwargs: Any) -> None:
            captured_config_kwargs.update(kwargs)

    args = argparse.Namespace(
        headless=False,
        host="0.0.0.0",  # noqa: S104
        port=8080,
        ssl_certfile=None,
        ssl_keyfile=None,
    )

    with (
        patch.dict(os.environ, {"VECTORA_DESKTOP": "1"}, clear=False),
        patch("backend.api.server.create_app", return_value=MagicMock()),
        patch("backend.main._start_vite_dev", return_value=None, create=True),
        patch("backend.services.ipc_pipe_win.serve_pipe", AsyncMock()),
        patch("uvicorn.Config", side_effect=_FakeConfig),
        patch(
            "uvicorn.Server",
            return_value=MagicMock(serve=AsyncMock(return_value=None)),
        ),
        patch("asyncio.run"),
        patch("os._exit"),
    ):
        from backend.main import _run_start

        _run_start(args)

    assert "uds" not in captured_config_kwargs
    assert captured_config_kwargs["host"] == "127.0.0.1", "desktop não expõe a LAN"


@pytest.mark.skipif(sys.platform != "win32", reason="Named pipe só no Windows")
def test_desktop_windows_shutdown_calls_os_exit() -> None:
    """Regressão ao vivo: o ramo Windows+desktop dava `return` logo após
    `asyncio.run(_run_win())` completar, pulando o `os._exit(0)` que mata
    threads não-daemon (httpx/SQLite do tracer) — Ctrl+C acionava
    o shutdown gracioso do uvicorn/NATS normalmente, mas o processo Python
    continuava vivo até o usuário fechar o Electron manualmente pela
    bandeja. `os._exit` precisa ser chamado mesmo nesse ramo."""
    import argparse

    args = argparse.Namespace(
        headless=False,
        host="0.0.0.0",  # noqa: S104
        port=8080,
        ssl_certfile=None,
        ssl_keyfile=None,
    )

    with (
        patch.dict(os.environ, {"VECTORA_DESKTOP": "1"}, clear=False),
        patch("backend.api.server.create_app", return_value=MagicMock()),
        patch("backend.main._start_vite_dev", return_value=None, create=True),
        patch("backend.services.ipc_pipe_win.serve_pipe", AsyncMock()),
        patch("uvicorn.Config", return_value=MagicMock()),
        patch(
            "uvicorn.Server",
            return_value=MagicMock(serve=AsyncMock(return_value=None)),
        ),
        # asyncio.run completa normalmente (shutdown gracioso via Ctrl+C) —
        # nenhuma exceção propaga, é exatamente o caso que escapava do exit.
        patch("asyncio.run"),
        patch("os._exit") as mock_exit,
    ):
        from backend.main import _run_start

        _run_start(args)

    mock_exit.assert_called_once_with(0)


# ---------------------------------------------------------------------------
# Modo backend-primário em dev — auto-eleição. O SPAWN em si não mora mais
# aqui — saiu de `_run_start` (bootstrap síncrono da CLI) pra dentro do
# lifespan async do FastAPI (`backend/services/electron_sidecar.py`, mesmo
# padrão do NATS — ver test_electron_sidecar.py). `_run_start` só decide
# (via `should_spawn_electron()`) e sinaliza pro sidecar via env vars —
# esses testes cobrem só essa decisão + sinalização.
# ---------------------------------------------------------------------------


def _base_args(*, headless: bool = False) -> Any:
    import argparse

    return argparse.Namespace(
        headless=headless,
        host="0.0.0.0",  # noqa: S104
        port=8080,
        ssl_certfile=None,
        ssl_keyfile=None,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="branch exercitado é o do Windows")
def test_autoeleicao_sinaliza_env_vars_pro_sidecar_quando_deve_spawnar() -> None:
    """Quando `should_spawn_electron()` diz sim, `_run_start` seta
    VECTORA_DESKTOP + VECTORA_SPAWN_ELECTRON + VECTORA_PORT — o sinal que o
    lifespan do FastAPI usa pra decidir subir o sidecar. `_run_start` em si
    não spawna nada (não há mock de subprocess/asyncio.create_subprocess_exec
    aqui — se ele tentasse, o teste falharia por chamada não mockada)."""
    env = {k: v for k, v in os.environ.items() if k != "VECTORA_DESKTOP"}

    with (
        patch.dict(os.environ, env, clear=True),
        patch(
            "backend.services.electron_sidecar.should_spawn_electron",
            return_value=True,
        ),
        patch("backend.api.server.create_app", return_value=MagicMock()),
        patch("backend.main._start_vite_dev", return_value=None, create=True),
        patch("backend.services.ipc_pipe_win.serve_pipe", AsyncMock()),
        patch("uvicorn.Config", return_value=MagicMock()),
        patch(
            "uvicorn.Server", return_value=MagicMock(serve=AsyncMock(return_value=None))
        ),
        patch("asyncio.run"),
        patch("os._exit"),
    ):
        from backend.main import _run_start

        _run_start(_base_args())

        assert os.environ.get("VECTORA_DESKTOP") == "1"
        assert os.environ.get("VECTORA_SPAWN_ELECTRON") == "1"
        assert os.environ.get("VECTORA_PORT") == "8080"


def test_autoeleicao_nao_sinaliza_quando_should_spawn_electron_diz_nao() -> None:
    """Par de erro/edge case: `should_spawn_electron()` retornando False
    (headless, sem display, ou sem build resolvível — cobertos nos testes
    dedicados de `electron_sidecar.py`) não seta nenhum dos dois env vars —
    `_run_start` cai no caminho de servidor puro/bandeja de sempre."""
    env = {k: v for k, v in os.environ.items() if k != "VECTORA_DESKTOP"}

    with (
        patch.dict(os.environ, env, clear=True),
        patch(
            "backend.services.electron_sidecar.should_spawn_electron",
            return_value=False,
        ) as mock_should_spawn,
        patch("backend.api.server.create_app", return_value=MagicMock()),
        patch("backend.main._start_vite_dev", return_value=None, create=True),
        patch("backend.services.tray.run_server_with_tray"),
        patch("uvicorn.Config", return_value=MagicMock()),
        patch("uvicorn.Server", return_value=MagicMock()),
        patch("os._exit"),
    ):
        from backend.main import _run_start

        _run_start(_base_args())

    mock_should_spawn.assert_called_once()
    assert "VECTORA_DESKTOP" not in os.environ
    assert "VECTORA_SPAWN_ELECTRON" not in os.environ
