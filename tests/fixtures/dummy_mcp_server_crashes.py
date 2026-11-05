"""Servidor MCP stdio que morre antes de inicializar a sessão — usado pelo
edge case "servidor cai durante o handshake" em test_mcp_client_real.py.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    sys.exit(1)
