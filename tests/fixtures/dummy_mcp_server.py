"""Servidor MCP stdio real (SDK oficial `mcp`) usado como fixture pelos
testes de `backend/tools/mcp.py`. Roda como subprocesso — não é mock.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server = MCPServer("dummy-mcp-server")


@server.tool()
def echo(text: str) -> str:
    """Devolve o texto recebido, prefixado."""
    return f"echo: {text}"


@server.tool()
def sum_numbers(a: int, b: int) -> str:
    """Soma dois inteiros."""
    return str(a + b)


@server.tool()
def read_env_var(name: str) -> str:
    """Devolve o valor de uma variável de ambiente do processo servidor,
    ou 'ausente' se não estiver setada — usado pra provar o filtro de env
    do subprocess stdio (allowlist, não herança total de os.environ)."""
    import os

    return os.environ.get(name, "ausente")


@server.tool()
def sleep(seconds: float) -> str:
    """Dorme `seconds` antes de responder — usado para exercitar timeout
    real do lado do cliente sem cancelar a conexão em pleno handshake."""
    import time

    time.sleep(seconds)
    return "acordou"


if __name__ == "__main__":
    server.run(transport="stdio")
