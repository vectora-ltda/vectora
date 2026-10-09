"""Automação de browser do agente — Playwright via CDP do Electron, sessão
persistente por workspace, navegação livre (`browser_navigate`) ou dev server local.
"""

from __future__ import annotations

from backend.browser.dev_server import resolve_dev_server_url
from backend.browser.session import close_all_browser_sessions, get_browser_page

__all__ = ["close_all_browser_sessions", "get_browser_page", "resolve_dev_server_url"]
