"""Testes para backend/tools/mcp.py — client MCP nativo (SDK oficial `mcp`).
Servidor real via stdio (tests/fixtures/dummy_mcp_server.py), não mock de
protocolo.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from anyio import BrokenResourceError
from mcp import StdioServerParameters

from backend.tools import mcp as mcp_tool_module
from backend.tools.mcp import VectoraMCPClient, _safe_subprocess_env, call_mcp_tool

_DUMMY_SERVER = str(
    Path(__file__).resolve().parent.parent / "fixtures" / "dummy_mcp_server.py"
)
_DUMMY_SERVER_CRASHES = str(
    Path(__file__).resolve().parent.parent / "fixtures" / "dummy_mcp_server_crashes.py"
)


@pytest.fixture(autouse=True)
async def _reset_global_client(monkeypatch):
    """Cada teste começa sem client global cacheado — evita vazamento de
    estado entre testes (o módulo usa um singleton lazy).

    `_mcp_lock` também precisa de um `asyncio.Lock()` novo a cada teste: em
    produção o processo tem um único event loop pela vida inteira, mas
    pytest-asyncio cria um loop novo por teste (escopo função) — reusar o
    mesmo `Lock` entre loops arrisca associá-lo a um loop já fechado.

    Fechar de verdade o client anterior (não só zerar a referência) é o
    que importa: sem isso, cada teste que passa por `_get_mcp_client()`
    deixa um subprocess real do servidor dummy órfão (sem `__del__`
    nenhum fecha o `AsyncExitStack` sozinho) — vários deles acumulados
    esgotam handles/IOCP no Windows e travam o teardown de um teste
    completamente sem relação, bem mais adiante no arquivo (achado ao
    vivo: suíte travava sempre no teste de timeout, o último a rodar
    depois de todos os outros subprocessos ficarem pendurados)."""
    monkeypatch.setattr(mcp_tool_module, "_mcp_client", None)
    monkeypatch.setattr(mcp_tool_module, "_mcp_lock", asyncio.Lock())
    yield
    stale_client = mcp_tool_module._mcp_client
    monkeypatch.setattr(mcp_tool_module, "_mcp_client", None)
    if stale_client is not None:
        with contextlib.suppress(Exception):
            await stale_client.aclose()


@pytest.fixture
def _enable_mcp(monkeypatch):
    monkeypatch.setattr(mcp_tool_module.settings, "enable_mcp", True)


@pytest.fixture
def _dummy_server_settings(monkeypatch):
    monkeypatch.setattr(mcp_tool_module.settings, "mcp_command", sys.executable)
    monkeypatch.setattr(mcp_tool_module.settings, "mcp_command_args", [_DUMMY_SERVER])
    monkeypatch.setattr(mcp_tool_module.settings, "mcp_server_url", None)


class TestSafeSubprocessEnv:
    def test_allowlist_nunca_inclui_chave_sensivel(self, monkeypatch):
        monkeypatch.setenv("VECTORA_ANTHROPIC_API_KEY", "sk-segredo")
        monkeypatch.setenv("PATH", "/usr/bin")
        env = _safe_subprocess_env()
        assert "VECTORA_ANTHROPIC_API_KEY" not in env
        assert "PATH" in env

    def test_extra_keys_declaradas_passam_mas_sensivel_nao_declarada_nao_vaza(
        self, monkeypatch
    ):
        monkeypatch.setenv("MEU_TOKEN_DE_SERVICO", "valor-necessario")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-nao-deveria-vazar")
        monkeypatch.setenv("PATH", "/usr/bin")
        env = _safe_subprocess_env(frozenset({"MEU_TOKEN_DE_SERVICO"}))
        assert env["MEU_TOKEN_DE_SERVICO"] == "valor-necessario"
        assert "PATH" in env
        assert "OPENAI_API_KEY" not in env


class TestVectoraMCPClientReal:
    """Conecta de verdade num subprocesso stdio (dummy_mcp_server.py)."""

    async def test_conecta_lista_tools_e_chama_tool_com_sucesso(self):
        client = VectoraMCPClient()
        try:
            await client.connect(
                {
                    "dummy": {
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [_DUMMY_SERVER],
                    }
                }
            )
            tools = client.tools()
            assert "echo" in tools
            assert "sum_numbers" in tools

            result = await client.call_tool("echo", {"text": "ola"})
            assert result == "echo: ola"

            result_sum = await client.call_tool("sum_numbers", {"a": 2, "b": 3})
            assert result_sum == "5"
        finally:
            await client.aclose()

    async def test_launcher_de_ambiente_e_configuracao_usam_o_mesmo_proxy(
        self, monkeypatch
    ):
        captured: list[StdioServerParameters] = []

        class FakeSession:
            async def initialize(self) -> None:
                return None

        @asynccontextmanager
        async def fake_stdio(params):
            captured.append(params)
            yield object(), object()

        @asynccontextmanager
        async def fake_session(_read, _write):
            yield FakeSession()

        monkeypatch.setenv("VECTORA_MCP_STDIO_SANDBOX", "1")
        monkeypatch.setenv("VECTORA_MCP_STDIO_SANDBOX_LAUNCHER", sys.executable)
        monkeypatch.setattr(mcp_tool_module, "stdio_client", fake_stdio)
        monkeypatch.setattr(mcp_tool_module, "ClientSession", fake_session)
        client = VectoraMCPClient()
        try:
            await client._connect_one(
                "env",
                {
                    "transport": "stdio",
                    "command": "server-command",
                    "args": ["--stdio"],
                    "require_sandbox": True,
                },
            )
            await client._connect_one(
                "config",
                {
                    "transport": "stdio",
                    "command": "server-command",
                    "args": ["--stdio"],
                    "sandbox_launcher": sys.executable,
                    "require_sandbox": True,
                },
            )
        finally:
            await client.aclose()
        assert [params.command for params in captured] == [
            sys.executable,
            sys.executable,
        ]
        assert [params.args for params in captured] == [
            ["server-command", "--stdio"],
            ["server-command", "--stdio"],
        ]

    async def test_env_do_subprocesso_e_allowlist_nao_variavel_sensivel(
        self, monkeypatch
    ):
        monkeypatch.setenv("VECTORA_SEGREDO_DE_TESTE", "nao-deveria-vazar")
        client = VectoraMCPClient()
        try:
            await client.connect(
                {
                    "dummy": {
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [_DUMMY_SERVER],
                    }
                }
            )
            result = await client.call_tool(
                "read_env_var", {"name": "VECTORA_SEGREDO_DE_TESTE"}
            )
            assert result == "ausente"
        finally:
            await client.aclose()

    async def test_env_vars_declaradas_atravessam_mas_outra_sensivel_nao(
        self, monkeypatch
    ):
        monkeypatch.setenv("MEU_TOKEN_DE_SERVICO", "valor-necessario")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-nao-deveria-vazar")
        client = VectoraMCPClient()
        try:
            await client.connect(
                {
                    "dummy": {
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [_DUMMY_SERVER],
                        "env_vars": ["MEU_TOKEN_DE_SERVICO"],
                    }
                }
            )
            result_declarada = await client.call_tool(
                "read_env_var", {"name": "MEU_TOKEN_DE_SERVICO"}
            )
            assert result_declarada == "valor-necessario"

            result_sensivel = await client.call_tool(
                "read_env_var", {"name": "OPENAI_API_KEY"}
            )
            assert result_sensivel == "ausente"
        finally:
            await client.aclose()

    async def test_servidor_que_cai_no_handshake_nao_quebra_connect(self):
        """Um servidor configurado que falha ao iniciar não deve impedir
        outros servidores (aqui, só ele mesmo) — connect() nunca propaga."""
        client = VectoraMCPClient()
        try:
            await client.connect(
                {
                    "quebrado": {
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [_DUMMY_SERVER_CRASHES],
                    }
                }
            )
            assert client.tools() == {}
        finally:
            await client.aclose()

    async def test_tool_desconhecida_levanta_keyerror(self):
        client = VectoraMCPClient()
        try:
            await client.connect(
                {
                    "dummy": {
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [_DUMMY_SERVER],
                    }
                }
            )
            with pytest.raises(KeyError):
                await client.call_tool("tool_que_nao_existe", {})
        finally:
            await client.aclose()

    async def test_transporte_desconhecido_nao_derruba_connect(self):
        """`connect()` captura falha por servidor (inclui transporte
        desconhecido) — nunca propaga, o cliente segue usável sem essa
        conexão específica."""
        client = VectoraMCPClient()
        try:
            await client.connect({"x": {"transport": "carrier-pigeon"}})
            assert client.tools() == {}
        finally:
            await client.aclose()


class TestVectoraMCPClientAcloseNuncaPropaga:
    """Regressão (CI real, 2026-08-19): o SDK oficial `mcp` tem uma race
    conhecida no teardown de `stdio_client`/`ClientSession` dentro do mesmo
    `AsyncExitStack` — o subprocess encerra enquanto a task de leitura ainda
    tenta escrever no stream já fechado, e isso vira `ExceptionGroup`
    (`anyio.BrokenResourceError`) no `__aexit__`. A conexão já tinha
    cumprido seu propósito antes disso (tools listadas com sucesso) —
    `aclose()` precisa absorver esse ruído de encerramento, nunca propagar,
    senão uma chamada que já teve sucesso é reportada como falha."""

    async def test_erro_no_teardown_do_stack_nao_propaga_e_limpa_estado(self):
        client = VectoraMCPClient()
        try:
            await client.connect(
                {
                    "dummy": {
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [_DUMMY_SERVER],
                    }
                }
            )
            assert client.tools()  # conexão real funcionou antes do teardown

            # Fecha o subprocess de verdade (senão ele fica órfão e trava o
            # teardown de testes seguintes) e SÓ DEPOIS simula a race —
            # nunca substituir aclose() sem chamar o original, ou o
            # subprocess real do servidor dummy nunca é encerrado.
            real_aclose = client._stack.aclose

            async def _stack_aclose_quebrado() -> None:
                with contextlib.suppress(Exception):
                    await real_aclose()
                raise ExceptionGroup(
                    "unhandled errors in a TaskGroup", [BrokenResourceError()]
                )

            client._stack.aclose = _stack_aclose_quebrado  # ty: ignore[invalid-assignment]

            await client.aclose()  # não deve levantar

            assert client.tools() == {}
            assert client._sessions == {}
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()


class TestCallMcpToolIntegration:
    """Fluxo ponta a ponta da tool exposta ao agente."""

    async def test_mcp_desabilitado_retorna_aviso(self, monkeypatch):
        monkeypatch.setattr(mcp_tool_module.settings, "enable_mcp", False)
        result = await call_mcp_tool(tool_name="echo", arguments="{}")
        assert "desabilitado" in result.lower()

    async def test_arguments_json_invalido_retorna_erro_tipado(self, _enable_mcp):
        result = await call_mcp_tool(tool_name="echo", arguments="{invalido")
        assert "JSON válido" in result

    async def test_nenhum_servidor_configurado_retorna_aviso(
        self, _enable_mcp, monkeypatch
    ):
        monkeypatch.setattr(mcp_tool_module.settings, "mcp_server_url", None)
        monkeypatch.setattr(mcp_tool_module.settings, "mcp_command", None)
        result = await call_mcp_tool(tool_name="echo", arguments="{}")
        assert "Nenhuma tool MCP disponível" in result

    async def test_happy_path_chama_tool_do_servidor_real(
        self, _enable_mcp, _dummy_server_settings
    ):
        result = await call_mcp_tool(tool_name="echo", arguments='{"text": "mundo"}')
        assert result == "echo: mundo"

    async def test_tool_inexistente_lista_disponiveis(
        self, _enable_mcp, _dummy_server_settings
    ):
        result = await call_mcp_tool(tool_name="nao_existe", arguments="{}")
        assert "não encontrada" in result
        assert "echo" in result

    async def test_timeout_retorna_erro_tipado(
        self, _enable_mcp, _dummy_server_settings, monkeypatch
    ):
        # `mcp_timeout=1` + tool que dorme 5s (não `mcp_timeout=0`): cancelar
        # bem no meio do handshake dispara uma race real no teardown do
        # stdio_client do SDK oficial no Windows (ProactorEventLoop) — trava
        # o loop de teste inteiro em vez de só cancelar a chamada. Aqui a
        # conexão já está de pé quando o timeout dispara, exercitando o
        # mesmo código (`asyncio.timeout`/mensagem "excedeu") sem a race.
        monkeypatch.setattr(mcp_tool_module.settings, "mcp_timeout", 1)
        result = await call_mcp_tool(tool_name="sleep", arguments='{"seconds": 2}')
        assert "excedeu" in result
