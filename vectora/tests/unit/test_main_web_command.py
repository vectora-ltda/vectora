"""Testes — `vectora web` (webapp puro, sem Electron/bandeja).

Cobre o parser do subcomando `web`, o dispatch em `run()` e o comportamento
de `_run_start(force_web=True)`: registra VECTORA_HEADLESS, nunca chama
`run_server_with_tray` (só `asyncio.run(server.serve())` direto) — ao
contrário de `vectora start`, que sobe a bandeja quando há display.
"""

from __future__ import annotations

import argparse
import os
from unittest.mock import MagicMock, patch

import pytest


def test_parser_web_aceita_host_porta_tls() -> None:
    """`vectora web` tem os mesmos flags de rede/TLS que `start`, sem --headless."""
    from backend.main import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["web", "--port", "9000"])
    assert args.command == "web"
    assert args.port == 9000
    assert not hasattr(args, "headless")


def test_parser_web_rejeita_flag_invalida() -> None:
    """Flag desconhecida em `vectora web` levanta SystemExit (par de erro)."""
    from backend.main import _build_parser

    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["web", "--nao-existe"])


def test_run_despacha_web_com_force_web_true() -> None:
    """`run()` com `sys.argv=["vectora","web"]` chama `_run_start(force_web=True)`."""
    with (
        patch("sys.argv", ["vectora", "web"]),
        patch("backend.main._run_start") as mock_run_start,
    ):
        from backend.main import run

        run()

    mock_run_start.assert_called_once()
    _, kwargs = mock_run_start.call_args
    assert kwargs.get("force_web") is True


def test_force_web_seta_headless_e_pula_bandeja() -> None:
    """force_web=True registra VECTORA_HEADLESS e nunca chama run_server_with_tray."""
    args = argparse.Namespace(
        host="0.0.0.0",  # noqa: S104
        port=8080,
        ssl_certfile=None,
        ssl_keyfile=None,
    )

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("VECTORA_DESKTOP", "VECTORA_HEADLESS")
    }
    env_captured: list[tuple[str | None, str | None, str | None]] = []

    with (
        patch.dict(os.environ, env, clear=True),
        patch("uvicorn.Config", return_value=MagicMock()),
        patch("uvicorn.Server", return_value=MagicMock()),
        patch("backend.api.server.create_app", return_value=MagicMock()),
        patch("backend.main._start_vite_dev", return_value=None, create=True),
        patch("backend.main._kill_vite", create=True),
        # should_spawn_electron() é importado dentro de _run_start a cada
        # chamada — mockar aqui (não resolve_electron_launch, que fica em
        # outro módulo e vaza estado real entre testes) neutraliza de vez a
        # decisão de auto-eleição do Electron nesses testes.
        patch(
            "backend.services.electron_sidecar.should_spawn_electron",
            return_value=False,
        ),
        patch("backend.services.tray.run_server_with_tray") as mock_tray,
        patch("asyncio.run") as mock_asyncio_run,
        patch("os._exit"),
    ):
        from backend.main import _run_start

        _run_start(args, force_web=True)
        # Checar env DENTRO do patch.dict antes de ser revertido no __exit__.
        env_captured.append(
            (
                os.environ.get("VECTORA_HEADLESS"),
                os.environ.get("VECTORA_DESKTOP"),
                os.environ.get("VECTORA_SPAWN_ELECTRON"),
            )
        )

    assert env_captured[0] == ("1", None, None)
    mock_tray.assert_not_called()
    mock_asyncio_run.assert_called_once()


def test_start_sem_force_web_sobe_bandeja_quando_nao_headless() -> None:
    """Sem force_web (`vectora start`), o caminho normal chama run_server_with_tray."""
    args = argparse.Namespace(
        headless=False,
        host="0.0.0.0",  # noqa: S104
        port=8080,
        ssl_certfile=None,
        ssl_keyfile=None,
    )

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("VECTORA_DESKTOP", "VECTORA_HEADLESS")
    }

    with (
        patch.dict(os.environ, env, clear=True),
        patch("uvicorn.Config", return_value=MagicMock()),
        patch("uvicorn.Server", return_value=MagicMock()),
        patch("backend.api.server.create_app", return_value=MagicMock()),
        patch("backend.main._start_vite_dev", return_value=None, create=True),
        patch("backend.main._kill_vite", create=True),
        # should_spawn_electron() é importado dentro de _run_start a cada
        # chamada — mockar aqui (não resolve_electron_launch, que fica em
        # outro módulo e vaza estado real entre testes) neutraliza de vez a
        # decisão de auto-eleição do Electron nesses testes.
        patch(
            "backend.services.electron_sidecar.should_spawn_electron",
            return_value=False,
        ),
        patch("backend.services.tray.run_server_with_tray") as mock_tray,
        patch("asyncio.run") as mock_asyncio_run,
        patch("os._exit"),
    ):
        from backend.main import _run_start

        _run_start(args)

    mock_tray.assert_called_once()
    mock_asyncio_run.assert_not_called()
