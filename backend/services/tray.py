"""Bandeja do sistema (tray) do Vectora — parte de ``vectora start``.

Roda a bandeja (pystray) na main thread e o servidor uvicorn numa thread de
fundo, de modo que o comportamento de bandeja (abrir o app, sair) exista mesmo
rodando o binário direto, sem Electron.

Degrada para **servidor puro** quando não há display/bandeja disponível (VPS,
Docker, SSH sem X, ou pystray/Pillow ausentes) — o servidor nunca deixa de
subir por causa da bandeja. Esse fallback é o caminho usado em produção
headless (o ENTRYPOINT do Docker é ``vectora start``).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Protocol


class _ServerLike(Protocol):
    should_exit: bool

    def run(self) -> None: ...


logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(__file__).parent.parent / "assets"
_ICON_PNG = _ASSETS_DIR / "vectora.png"
_ICON_ICO = _ASSETS_DIR / "vectora.ico"

_PURPLE = (124, 58, 237)
_WHITE = (255, 255, 255)


def has_display() -> bool:
    """Heurística: existe um ambiente gráfico capaz de exibir uma bandeja?

    Windows e macOS sempre têm; em Linux/Unix exige X11 ou Wayland (``DISPLAY``
    / ``WAYLAND_DISPLAY``). Em VPS/Docker isso é falso → servidor puro.
    """
    if sys.platform in {"win32", "darwin"}:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _enable_dark_mode_win32() -> None:
    """Ativa dark mode para menus Win32 via UxTheme ordinal 135 (Windows 10+).

    SetPreferredAppMode(2 = PreferDark) afeta o tema dos menus de contexto
    gerados pelo Win32, incluindo o menu do tray. FlushMenuThemes() aplica
    imediatamente para janelas existentes.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from typing import cast

        uxtheme = cast("Any", ctypes.WinDLL("uxtheme"))
        set_mode = uxtheme[135]  # SetPreferredAppMode — ordinal não documentado
        set_mode.restype = ctypes.c_int
        set_mode(2)  # PreferDark = 2
        uxtheme[136]()  # FlushMenuThemes
    except Exception as exc:
        logger.debug("tray: dark mode win32 indisponível: %s", exc)


def _build_icon_image() -> Any:
    """Carrega o ícone real do Vectora; fallback para quadrado roxo com 'V'."""
    from PIL import Image

    resample = getattr(getattr(Image, "Resampling", None), "LANCZOS", None) or getattr(
        Image, "LANCZOS", 1
    )

    for path in (_ICON_PNG, _ICON_ICO):
        if path.exists():
            try:
                return Image.open(path).convert("RGBA").resize((64, 64), resample)
            except Exception as exc:
                logger.debug("tray: falha ao carregar %s: %s", path, exc)

    # Fallback — quadrado roxo com 'V'
    from PIL import ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([4, 4, size - 4, size - 4], radius=12, fill=_PURPLE)
    draw.text((22, 18), "V", fill=_WHITE)
    return img


def run_server_with_tray(
    server: _ServerLike,
    url: str,
    *,
    headless: bool,
    icon_ref: list[Any] | None = None,
) -> None:
    """Sobe o ``uvicorn.Server`` e, quando possível, uma bandeja do sistema.

    Args:
        server: ``uvicorn.Server`` já configurado (config pronta).
        url: URL local da SPA para a ação "Abrir Vectora".
        headless: se ``True``, não abre o navegador no start (só a bandeja).
        icon_ref: lista mutável de tamanho 1; quando preenchida, recebe a
            referência ao ``pystray.Icon`` criado, permitindo que handlers
            de sinal externos chamem ``icon_ref[0].stop()``.

    Sem display ou sem pystray/Pillow, roda o servidor puro na main thread
    (bloqueante) e retorna ao encerrar.
    """
    # Sob Electron (VECTORA_DESKTOP=1) a janela e a bandeja são nativas do
    # Electron — não abrir uma segunda bandeja em Python.
    if os.environ.get("VECTORA_DESKTOP"):
        logger.info("tray: Electron presente — servidor puro (UI nativa do Electron)")
        server.run()
        return

    if not has_display():
        logger.info("tray: sem display — servidor puro")
        server.run()
        return

    try:
        import pystray
    except Exception as exc:
        logger.info("tray: pystray indisponível (%s) — servidor puro", exc)
        server.run()
        return

    try:
        image = _build_icon_image()
    except Exception as exc:
        logger.info("tray: Pillow indisponível (%s) — servidor puro", exc)
        server.run()
        return

    _enable_dark_mode_win32()

    server_thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    server_thread.start()

    def _open(_icon: object = None, _item: object = None) -> None:
        webbrowser.open(url)

    def _quit(icon: Any, _item: object = None) -> None:
        server.should_exit = True
        icon.stop()

    icon = pystray.Icon(
        "vectora",
        image,
        "Vectora",
        menu=pystray.Menu(
            pystray.MenuItem("Abrir Vectora", _open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", _quit),
        ),
    )

    if icon_ref is not None:
        icon_ref[0] = icon

    if not headless:
        # Abre a SPA assim que o servidor estiver de pé.
        threading.Timer(1.5, _open).start()

    logger.info(
        "tray: bandeja ativa (%s) — %s", "headless" if headless else "fullstack", url
    )
    # Bloqueia na main thread até "Sair"; ao sair, encerra o servidor.
    icon.run()
    server.should_exit = True
    server_thread.join(timeout=10)
