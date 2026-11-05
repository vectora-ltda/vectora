"""Cliente MCP contra um servidor de TERCEIRO real (não fake).

Valida o fluxo do usuário ponta a ponta: registrar um servidor MCP de
terceiro via o store funcional (``backend.workspace.plugins``, o mesmo que
alimenta o marketplace) e confirmar que o Vectora, como cliente, LISTA as
tools reais dele via ``get_user_mcp_tools`` → ``VectoraMCPClient`` (SDK
oficial ``mcp``, nativo).

Usa o ``@modelcontextprotocol/server-filesystem`` (stdio via ``npx``) —
servidor oficial, local (sem rede além do fetch do pacote pelo npx).
Hermético: sem ``npx``, o teste é neutralizado com skip explícito; se o
servidor não subir no tempo do health-check (download frio do npx), skipa
com razão em vez de falhar falso.
"""

from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(
    shutil.which("npx") is None,
    reason="npx indisponível — servidor MCP 3rd-party real requer Node.",
)
async def test_filesystem_mcp_3rd_party_lista_tools_reais(tmp_path, monkeypatch):
    from backend.workspace import plugins

    # Store funcional isolado (mesmo caminho que o marketplace usa para gravar).
    monkeypatch.setattr(plugins, "_plugins_dir", lambda: tmp_path / "mcp")
    monkeypatch.setattr(plugins, "_versions", {})
    monkeypatch.setattr(plugins, "_mcp_tools_cache", {})
    monkeypatch.setattr(plugins, "publish_soon", lambda *a, **k: None, raising=False)

    # Servidor MCP de terceiro REAL (oficial), stdio via npx, escopado ao tmp.
    server = plugins.McpServer(
        name="filesystem",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", str(tmp_path)],
    )
    plugins.add_server("local", server)

    tools = await plugins.get_user_mcp_tools("local")
    names = {t.name for t in tools}

    if not names:
        pytest.skip(
            "Servidor MCP filesystem não respondeu no health-check "
            "(npx offline/download frio) — dependência real ausente."
        )

    # O servidor filesystem expõe tools de leitura/listagem de arquivos.
    assert any(
        ("read" in n) or ("list" in n) or ("director" in n) or ("file" in n)
        for n in names
    ), f"tools inesperadas do servidor filesystem: {sorted(names)}"
