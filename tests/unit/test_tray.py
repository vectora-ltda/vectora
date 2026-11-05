"""Testes para backend/services/tray.py.

Cobre: has_display, _build_icon_image (ícone real + fallback), _enable_dark_mode_win32,
e run_server_with_tray (caminhos de degradação sem display/pystray/Pillow e sob Electron).
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# has_display
# ---------------------------------------------------------------------------


def test_has_display_windows_sempre_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    from backend.services.tray import has_display

    assert has_display() is True


def test_has_display_darwin_sempre_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    from backend.services.tray import has_display

    assert has_display() is True


def test_has_display_linux_sem_env_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    from backend.services.tray import has_display

    assert has_display() is False


def test_has_display_linux_com_display_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    from backend.services.tray import has_display

    assert has_display() is True


def test_has_display_linux_com_wayland_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    from backend.services.tray import has_display

    assert has_display() is True


# ---------------------------------------------------------------------------
# _build_icon_image
# ---------------------------------------------------------------------------


def test_build_icon_image_carrega_png_real(tmp_path: Path) -> None:
    """Quando o PNG existe, carrega e redimensiona para 64x64."""
    from PIL import Image

    png = tmp_path / "vectora.png"
    img_src = Image.new("RGBA", (600, 600), (124, 58, 237, 255))
    img_src.save(png)

    with (
        patch("backend.services.tray._ICON_PNG", png),
        patch("backend.services.tray._ICON_ICO", tmp_path / "vectora.ico"),
    ):
        from backend.services.tray import _build_icon_image

        result = _build_icon_image()

    assert result.size == (64, 64)
    assert result.mode == "RGBA"


def test_build_icon_image_fallback_sem_arquivo(tmp_path: Path) -> None:
    """Sem arquivos de ícone, retorna o quadrado roxo de fallback 64x64."""
    with (
        patch("backend.services.tray._ICON_PNG", tmp_path / "nope.png"),
        patch("backend.services.tray._ICON_ICO", tmp_path / "nope.ico"),
    ):
        from backend.services.tray import _build_icon_image

        result = _build_icon_image()

    assert result.size == (64, 64)
    assert result.mode == "RGBA"
    # Canal alpha não-zero no centro (pixel roxo)
    assert result.getpixel((32, 32))[3] > 0


def test_build_icon_image_fallback_arquivo_corrompido(tmp_path: Path) -> None:
    """Arquivo existente mas corrompido → fallback sem exceção."""
    bad = tmp_path / "vectora.png"
    bad.write_bytes(b"isto nao e png")

    with (
        patch("backend.services.tray._ICON_PNG", bad),
        patch("backend.services.tray._ICON_ICO", tmp_path / "nope.ico"),
    ):
        from backend.services.tray import _build_icon_image

        result = _build_icon_image()

    assert result.size == (64, 64)


# ---------------------------------------------------------------------------
# _enable_dark_mode_win32
# ---------------------------------------------------------------------------


def test_enable_dark_mode_noop_em_nao_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Em plataformas não-Windows, _enable_dark_mode_win32 não toca o ctypes."""
    monkeypatch.setattr(sys, "platform", "linux")

    with patch(
        "ctypes.WinDLL", side_effect=AssertionError("não deve chamar"), create=True
    ):
        from backend.services.tray import _enable_dark_mode_win32

        _enable_dark_mode_win32()  # não deve levantar


def test_enable_dark_mode_swallows_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mesmo que ctypes falhe, _enable_dark_mode_win32 não propaga exceção."""
    monkeypatch.setattr(sys, "platform", "win32")

    with patch(
        "ctypes.WinDLL",
        side_effect=OSError("uxtheme.dll não encontrado"),
        create=True,
    ):
        from backend.services.tray import _enable_dark_mode_win32

        _enable_dark_mode_win32()  # deve engolir o OSError


# ---------------------------------------------------------------------------
# run_server_with_tray — caminhos de degradação
# ---------------------------------------------------------------------------


def _fake_server() -> Any:
    srv = MagicMock()
    srv.should_exit = False
    srv.run = MagicMock()
    return srv


def test_run_server_modo_electron(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sob VECTORA_DESKTOP=1 sobe servidor puro, sem pystray."""
    monkeypatch.setenv("VECTORA_DESKTOP", "1")
    srv = _fake_server()

    from backend.services.tray import run_server_with_tray

    run_server_with_tray(srv, "http://localhost:8080", headless=False)

    srv.run.assert_called_once()


def test_run_server_sem_display(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem display (VPS/Docker) → servidor puro."""
    monkeypatch.delenv("VECTORA_DESKTOP", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    srv = _fake_server()

    from backend.services.tray import run_server_with_tray

    run_server_with_tray(srv, "http://localhost:8080", headless=False)

    srv.run.assert_called_once()


def test_run_server_sem_pystray(monkeypatch: pytest.MonkeyPatch) -> None:
    """pystray ausente → servidor puro sem exceção."""
    monkeypatch.delenv("VECTORA_DESKTOP", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    srv = _fake_server()

    with patch.dict("sys.modules", {"pystray": None}):
        from importlib import reload

        import backend.services.tray as tray_mod

        reload(tray_mod)
        tray_mod.run_server_with_tray(srv, "http://localhost:8080", headless=False)

    srv.run.assert_called_once()


def test_run_server_sem_pillow(monkeypatch: pytest.MonkeyPatch) -> None:
    """PIL/Pillow ausente → servidor puro sem exceção."""
    monkeypatch.delenv("VECTORA_DESKTOP", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    srv = _fake_server()

    fake_pystray = MagicMock()
    with patch.dict(
        "sys.modules", {"pystray": fake_pystray, "PIL": None, "PIL.Image": None}
    ):
        from importlib import reload

        import backend.services.tray as tray_mod

        reload(tray_mod)
        tray_mod.run_server_with_tray(srv, "http://localhost:8080", headless=False)

    srv.run.assert_called_once()


def test_run_server_com_tray_fullstack(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fullstack: inicia thread de servidor, cria ícone, chama icon.run()."""
    monkeypatch.delenv("VECTORA_DESKTOP", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")

    srv = _fake_server()
    # Faz server.run() retornar imediatamente (thread daemon)
    server_started = threading.Event()

    def _fake_run() -> None:
        server_started.set()

    srv.run = _fake_run

    fake_icon = MagicMock()
    fake_icon.run = MagicMock()

    fake_pystray = MagicMock()
    fake_pystray.Icon.return_value = fake_icon
    fake_pystray.Menu = MagicMock()
    fake_pystray.MenuItem = MagicMock()

    from PIL import Image

    fake_img = Image.new("RGBA", (64, 64))

    with (
        patch("backend.services.tray._build_icon_image", return_value=fake_img),
        patch("backend.services.tray._enable_dark_mode_win32"),
        patch.dict("sys.modules", {"pystray": fake_pystray}),
    ):
        from importlib import reload

        import backend.services.tray as tray_mod

        reload(tray_mod)

        with (
            patch.object(tray_mod, "_build_icon_image", return_value=fake_img),
            patch.object(tray_mod, "_enable_dark_mode_win32"),
        ):
            tray_mod.run_server_with_tray(srv, "http://localhost:8080", headless=True)

    fake_icon.run.assert_called_once()
