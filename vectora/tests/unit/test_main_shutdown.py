"""Shutdown correto ao fechar terminal ou receber SIGINT/SIGTERM/SIGHUP.

Garante que _install_terminal_signals seta server.should_exit=True e chama
icon.stop() quando o sinal chega. `_should_install_terminal_signals` decide
QUANDO instalar — distingue "processo dono de si mesmo" (terminal puro, ou
o próprio backend que se autoelegeu primário e subiu o Electron como seu
sidecar) de "Electron de produção me possui e já cuida da árvore via
taskkill /T /F" (onde o handler custom não deve rodar)."""

from __future__ import annotations

import os
import signal
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest


class _FakeServer:
    def __init__(self) -> None:
        self.should_exit = False

    def run(self) -> None:
        pass

    def install_signal_handlers(self) -> None:
        pass


def _get_handler(signum: int) -> Any:
    return signal.getsignal(signum)


def test_shutdown_sets_should_exit_on_sigint() -> None:
    """SIGINT → server.should_exit = True."""
    from backend.main import _install_terminal_signals

    server = _FakeServer()
    icon_ref: list[Any] = [None]

    orig = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        orig[sig] = signal.getsignal(sig)

    try:
        _install_terminal_signals(server, icon_ref)
        handler = _get_handler(signal.SIGINT)
        assert callable(handler)
        handler(signal.SIGINT, None)
        assert server.should_exit is True
    finally:
        for sig, h in orig.items():
            signal.signal(sig, h)


def test_shutdown_calls_icon_stop_when_present() -> None:
    """Quando icon_ref[0] está preenchido, handler chama icon.stop()."""
    from backend.main import _install_terminal_signals

    server = _FakeServer()
    icon = MagicMock()
    icon_ref: list[Any] = [icon]

    orig_int = signal.getsignal(signal.SIGINT)
    orig_term = signal.getsignal(signal.SIGTERM)
    try:
        _install_terminal_signals(server, icon_ref)
        handler = _get_handler(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        assert server.should_exit is True
        icon.stop.assert_called_once()
    finally:
        signal.signal(signal.SIGINT, orig_int)
        signal.signal(signal.SIGTERM, orig_term)


def test_shutdown_handler_is_idempotent_when_console_repeats_signal() -> None:
    from backend.main import _install_terminal_signals

    server = _FakeServer()
    icon = MagicMock()
    icon_ref: list[Any] = [icon]
    original = signal.getsignal(signal.SIGINT)
    try:
        _install_terminal_signals(server, icon_ref)
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        cast("Any", handler)(signal.SIGINT, None)
        cast("Any", handler)(signal.SIGINT, None)
        icon.stop.assert_called_once()
    finally:
        signal.signal(signal.SIGINT, original)


def test_shutdown_no_error_when_icon_is_none() -> None:
    """Handler não falha quando icon_ref[0] é None (modo servidor puro)."""
    from backend.main import _install_terminal_signals

    server = _FakeServer()
    icon_ref: list[Any] = [None]

    orig_int = signal.getsignal(signal.SIGINT)
    orig_term = signal.getsignal(signal.SIGTERM)
    try:
        _install_terminal_signals(server, icon_ref)
        handler = _get_handler(signal.SIGINT)
        handler(signal.SIGINT, None)
        assert server.should_exit is True
    finally:
        signal.signal(signal.SIGINT, orig_int)
        signal.signal(signal.SIGTERM, orig_term)


_SIGHUP: int | None = getattr(signal, "SIGHUP", None)


def test_shutdown_sighup_handling() -> None:
    """SIGHUP registrado em Unix; em Windows verifica que SIGINT/SIGTERM continuam ok."""
    from backend.main import _install_terminal_signals

    server = _FakeServer()
    icon_ref: list[Any] = [None]

    orig_int = signal.getsignal(signal.SIGINT)
    orig_term = signal.getsignal(signal.SIGTERM)
    try:
        _install_terminal_signals(server, icon_ref)
        if _SIGHUP is not None:
            orig_hup = signal.getsignal(_SIGHUP)
            try:
                handler = _get_handler(_SIGHUP)
                assert callable(handler)
                handler(_SIGHUP, None)
                assert server.should_exit is True
            finally:
                signal.signal(_SIGHUP, orig_hup)
        else:
            handler = _get_handler(signal.SIGINT)
            assert callable(handler)
    finally:
        signal.signal(signal.SIGINT, orig_int)
        signal.signal(signal.SIGTERM, orig_term)


def test_shutdown_sigbreak_handling_when_available() -> None:
    """Windows CTRL+BREAK follows the same cleanup path as CTRL+C."""
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is None:
        pytest.skip("SIGBREAK só existe no Windows")

    from backend.main import _install_terminal_signals

    server = _FakeServer()
    icon_ref: list[Any] = [None]
    original = signal.getsignal(sigbreak)
    try:
        _install_terminal_signals(server, icon_ref)
        handler = signal.getsignal(sigbreak)
        assert callable(handler)
        cast("Any", handler)(sigbreak, None)
        assert server.should_exit is True
    finally:
        signal.signal(sigbreak, original)


def test_uvicorn_does_not_replace_process_owner_signal_handler() -> None:
    """Uvicorn's internal capture must not swallow the backend Ctrl+C path."""
    from backend.main import _disable_uvicorn_signal_capture

    server = _FakeServer()
    original = MagicMock()
    server.install_signal_handlers = original

    _disable_uvicorn_signal_capture(server)
    server.install_signal_handlers()

    original.assert_not_called()


def test_tray_exposes_icon_via_icon_ref() -> None:
    """run_server_with_tray preenche icon_ref[0] com o pystray.Icon criado."""
    pystray_mock = MagicMock()
    icon_instance = MagicMock()
    pystray_mock.Icon.return_value = icon_instance
    pystray_mock.Menu.return_value = MagicMock()
    pystray_mock.MenuItem = MagicMock()

    server = _FakeServer()
    icon_ref: list[Any] = [None]

    with (
        patch.dict("sys.modules", {"pystray": pystray_mock}),
        patch("backend.services.tray.has_display", return_value=True),
        patch("backend.services.tray._build_icon_image", return_value=MagicMock()),
        patch("backend.services.tray._enable_dark_mode_win32"),
        patch("backend.services.tray.threading.Thread") as mock_thread,
        patch("backend.services.tray.threading.Timer") as mock_timer,
        patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop("VECTORA_DESKTOP", None)

        mock_thread.return_value = MagicMock()
        mock_timer.return_value = MagicMock()

        import backend.services.tray as tray_mod

        tray_mod.run_server_with_tray(
            server,  # type: ignore[arg-type]
            "http://localhost:8080",
            headless=True,
            icon_ref=icon_ref,
        )

    assert icon_ref[0] is icon_instance


class TestShouldInstallTerminalSignals:
    """`VECTORA_DESKTOP=1` sozinho não diz quem é dono do processo — só
    `VECTORA_SPAWN_ELECTRON` (setado só quando o próprio backend sobe o
    Electron como seu sidecar, `_run_start`) distingue o modo
    backend-primário em dev (sem dono externo, precisa se auto-limpar) de
    produção real (Electron possui o backend e mata a árvore sozinho)."""

    def test_terminal_interativo_puro_instala(self, monkeypatch):
        from backend.main import _should_install_terminal_signals

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        assert _should_install_terminal_signals({}) is True

    def test_backend_primario_dev_instala_mesmo_com_desktop_setado(self, monkeypatch):
        # Caso central desta correção: o próprio backend se autoelegeu
        # primário e subiu o Electron — sem handler, Ctrl+C/fechar o
        # terminal nunca aciona o shutdown gracioso, deixando o nats-server
        # filho órfão.
        from backend.main import _should_install_terminal_signals

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        env = {"VECTORA_DESKTOP": "1", "VECTORA_SPAWN_ELECTRON": "1"}
        assert _should_install_terminal_signals(env) is True

    def test_backend_primario_dev_instala_mesmo_sem_tty(self, monkeypatch):
        # Regressão ao vivo: o gate antigo caía em `sys.stdin.isatty()` mesmo
        # nesse caso — se `uv run`/o shell pai entregasse stdin como não-tty,
        # o handler nunca era instalado e Ctrl+C não fechava nada, só o
        # "Sair" da bandeja do Electron (que passa por outro caminho,
        # `_watch_for_unexpected_exit` → SIGTERM). Backend-primário precisa
        # sempre instalar, com ou sem tty — não há dono externo alternativo.
        from backend.main import _should_install_terminal_signals

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        env = {"VECTORA_DESKTOP": "1", "VECTORA_SPAWN_ELECTRON": "1"}
        assert _should_install_terminal_signals(env) is True

    def test_producao_com_electron_dono_nao_instala(self, monkeypatch):
        # Erro/borda: VECTORA_DESKTOP setado SEM VECTORA_SPAWN_ELECTRON é o
        # caso de produção (Electron spawnou o backend) — o Electron já
        # mata a árvore via taskkill /T /F; um handler custom aqui
        # disputaria esse shutdown.
        from backend.main import _should_install_terminal_signals

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        env = {"VECTORA_DESKTOP": "1"}
        assert _should_install_terminal_signals(env) is False

    def test_vps_docker_sem_tty_nao_instala(self, monkeypatch):
        from backend.main import _should_install_terminal_signals

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        assert _should_install_terminal_signals({}) is False

    def test_web_instala_mesmo_sem_tty(self, monkeypatch):
        """The web owner must receive Ctrl+C when launched by a VPS runner."""
        from backend.main import _should_install_terminal_signals

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        assert _should_install_terminal_signals({}, force_web=True) is True
