"""Servidores MCP stdio dentro do jail, e o lançador de seccomp que os inicia."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from backend.sandbox import jail_exec, mcp_launcher
from backend.sandbox.egress_proxy import egress_registry
from backend.sandbox.mcp_launcher import (
    McpSandboxUnavailableError,
    build_jailed_launch,
    mcp_policy,
    scratch_dir,
)
from backend.tools.mcp import VectoraMCPClient, stdio_sandbox_available
from tests.unit.test_sandbox_hardening import _bwrap_works

real_bwrap = pytest.mark.skipif(
    not _bwrap_works(), reason="bwrap sem user namespaces neste ambiente"
)

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "dummy_mcp_server.py"


@pytest.fixture(autouse=True)
def _pasta_de_trabalho_isolada(monkeypatch, tmp_path):
    """Nenhum teste grava no `~/.vectora` de quem roda."""
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path / "home")


# ── lançador de seccomp ─────────────────────────────────────────────────────


def test_with_seccomp_poe_o_descritor_herdavel_depois_do_bwrap(monkeypatch):
    monkeypatch.setattr(jail_exec, "build_seccomp_filter", lambda: b"BPF!")

    final = jail_exec.with_seccomp(["bwrap", "--unshare-pid", "--", "true"])

    assert final[:2] == ["bwrap", "--seccomp"]
    fd = int(final[2])
    assert os.get_inheritable(fd) is True
    assert os.pread(fd, 10, 0) == b"BPF!"
    assert final[3:] == ["--unshare-pid", "--", "true"]
    os.close(fd)


def test_sem_seccomp_segue_parcial_e_avisa(monkeypatch, capsys):
    monkeypatch.setattr(jail_exec, "build_seccomp_filter", lambda: None)
    argv = ["bwrap", "--", "true"]

    assert jail_exec.with_seccomp(argv) == argv
    assert "parcial" in capsys.readouterr().err


def test_sem_seccomp_com_exigencia_recusa_iniciar(monkeypatch, capsys):
    monkeypatch.setattr(jail_exec, "build_seccomp_filter", lambda: None)

    with pytest.raises(SystemExit) as saida:
        jail_exec.with_seccomp(["bwrap", "--", "true"], require=True)

    assert saida.value.code == 70
    assert "seccomp" in capsys.readouterr().err


# ── política e pasta do servidor ────────────────────────────────────────────


def test_politica_do_mcp_e_rede_filtrada_sem_nada_gravavel_alem_da_pasta():
    policy = mcp_policy(("api.exemplo.com",))

    assert policy.enabled and policy.network == "filtered"
    assert policy.allow_hosts == ("api.exemplo.com",)
    assert policy.rw_paths == () and policy.git_writable is False


def test_pasta_do_servidor_fica_em_vectora_home_e_o_nome_e_sanitizado(tmp_path):
    pasta = scratch_dir("../../etc/passwd")

    assert pasta.is_relative_to(tmp_path / "home" / "mcp-sandbox")
    assert pasta.name == ".._.._etc_passwd"
    assert oct(pasta.stat().st_mode & 0o777) == "0o700"


# ── comando montado ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_comando_roda_o_bwrap_pelo_lancador_com_ambiente_herdado():
    try:
        launch = await build_jailed_launch(
            "srv", sys.executable, ["-m", "algo"], allow_hosts=("x.com",)
        )
    finally:
        await egress_registry.close_all()

    assert launch.command == sys.executable
    assert launch.args[:3] == ["-m", "backend.sandbox.jail_exec", "--"]
    bwrap = launch.args[3:]
    assert bwrap[0] == "bwrap"
    # O ambiente que o cliente MCP montou (variáveis aprovadas) passa pelo
    # processo, não pela linha de comando: nada de --clearenv nem --setenv delas.
    assert "--clearenv" not in bwrap
    assert "--unshare-net" in bwrap
    assert "--harden" in bwrap
    assert bwrap[-3:] == [sys.executable, "-m", "algo"]
    assert launch.egress_key == "mcp:srv"


@pytest.mark.asyncio
async def test_programa_fora_do_sistema_tem_a_pasta_de_instalacao_somente_leitura(
    tmp_path,
):
    instalacao = tmp_path / "ferramentas" / "node-20"
    (instalacao / "bin").mkdir(parents=True)
    programa = instalacao / "bin" / "meu-servidor"
    programa.write_text("#!/bin/sh\n")
    programa.chmod(0o755)

    try:
        launch = await build_jailed_launch("srv", str(programa), [])
    finally:
        await egress_registry.close_all()

    bwrap = launch.args[3:]
    i = bwrap.index(str(instalacao))
    assert bwrap[i - 1 - 0 : i + 1][0] == "--ro-bind"
    assert bwrap[i + 1] == str(instalacao)


@pytest.mark.asyncio
async def test_programa_inexistente_e_recusado_com_mensagem_clara():
    with pytest.raises(McpSandboxUnavailableError, match="não encontrado"):
        await build_jailed_launch("srv", "programa-que-nao-existe-xyz", [])


@pytest.mark.asyncio
async def test_sem_bwrap_falha_antes_de_iniciar(monkeypatch):
    monkeypatch.setattr(mcp_launcher, "jail_available", lambda: False)

    with pytest.raises(McpSandboxUnavailableError, match="bwrap"):
        await build_jailed_launch("srv", sys.executable, [])


@pytest.mark.asyncio
async def test_release_encerra_o_proxy_do_servidor():
    launch = await build_jailed_launch("srv", sys.executable, [])
    assert egress_registry.get("mcp:srv") is not None

    await mcp_launcher.release(launch)

    assert egress_registry.get("mcp:srv") is None


def test_sandbox_embutido_conta_como_disponivel(monkeypatch):
    monkeypatch.delenv("VECTORA_MCP_STDIO_SANDBOX", raising=False)
    monkeypatch.delenv("VECTORA_MCP_STDIO_SANDBOX_LAUNCHER", raising=False)
    monkeypatch.setattr(mcp_launcher, "jail_available", lambda: True)
    assert stdio_sandbox_available() is True

    monkeypatch.setattr(mcp_launcher, "jail_available", lambda: False)
    assert stdio_sandbox_available() is False


# ── servidor MCP de verdade, dentro do jail ─────────────────────────────────


@real_bwrap
@pytest.mark.asyncio
async def test_servidor_mcp_nao_verificado_roda_isolado_de_ponta_a_ponta(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("VECTORA_SEGREDO_DO_BACKEND", "nao-deveria-vazar")
    monkeypatch.delenv("VECTORA_MCP_STDIO_SANDBOX_LAUNCHER", raising=False)
    script = scratch_dir("verdadeiro") / "server.py"
    shutil.copy(_FIXTURE, script)
    client = VectoraMCPClient()
    try:
        await client.connect(
            {
                "verdadeiro": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(script)],
                    "env": {"DECLARADA": "valor-aprovado"},
                    "require_sandbox": True,
                }
            },
            strict=True,
        )
        eco = await client.call_tool("echo", {"text": "ola"})
        declarada = await client.call_tool("read_env_var", {"name": "DECLARADA"})
        segredo = await client.call_tool(
            "read_env_var", {"name": "VECTORA_SEGREDO_DO_BACKEND"}
        )
        home = await client.call_tool("read_env_var", {"name": "HOME"})
    finally:
        await client.aclose()

    assert eco == "echo: ola"
    # A variável que o usuário aprovou chega; a do backend não.
    assert "valor-aprovado" in declarada
    assert "nao-deveria-vazar" not in segredo
    assert "/home/vectora-worker" in home
    assert egress_registry.get("mcp:verdadeiro") is None  # encerrado no aclose


@real_bwrap
@pytest.mark.asyncio
async def test_servidor_mcp_isolado_nao_alcanca_a_rede_pelo_caminho_direto(
    monkeypatch,
):
    monkeypatch.delenv("VECTORA_MCP_STDIO_SANDBOX_LAUNCHER", raising=False)
    script = scratch_dir("rede") / "server.py"
    script.write_text(
        "import socket\n"
        "from mcp.server.mcpserver import MCPServer\n"
        "app = MCPServer('rede')\n"
        "@app.tool()\n"
        "def conecta() -> str:\n"
        "    try:\n"
        "        socket.create_connection(('1.1.1.1', 443), timeout=3)\n"
        "        return 'saiu'\n"
        "    except OSError:\n"
        "        return 'sem-rota'\n"
        "app.run(transport='stdio')\n"
    )
    client = VectoraMCPClient()
    try:
        await client.connect(
            {
                "rede": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(script)],
                    "require_sandbox": True,
                }
            },
            strict=True,
        )
        resultado = await client.call_tool("conecta", {})
    finally:
        await client.aclose()

    assert resultado == "sem-rota"


# ── destinos do servidor ────────────────────────────────────────────────────


def test_servidor_mcp_declara_destinos_validos_e_eles_chegam_a_conexao():
    from backend.workspace.plugins import McpServer, build_connection

    server = McpServer(
        name="s", command="npx", allow_hosts=["api.exemplo.com", "*.cdn.exemplo.com"]
    )

    assert build_connection(server)["allow_hosts"] == [
        "api.exemplo.com",
        "*.cdn.exemplo.com",
    ]


@pytest.mark.parametrize(
    "host", ["http://a.com", "a.com/x", "*", "sem-ponto", "a..com"]
)
def test_servidor_mcp_recusa_destino_invalido(host):
    from pydantic import ValidationError

    from backend.workspace.plugins import McpServer

    with pytest.raises(ValidationError, match="allow_hosts"):
        McpServer(name="s", command="npx", allow_hosts=[host])


def test_servidor_antigo_sem_allow_hosts_continua_valido():
    from backend.workspace.plugins import McpServer

    assert McpServer(name="s", command="npx").allow_hosts == []


@pytest.mark.asyncio
async def test_cliente_passa_os_destinos_do_servidor_ao_jail(monkeypatch):
    from contextlib import asynccontextmanager

    capturado = {}

    async def falso(server_name, command, args, *, allow_hosts=()):
        capturado["allow_hosts"] = allow_hosts
        return mcp_launcher.JailedLaunch(sys.executable, [], None)

    @asynccontextmanager
    async def stdio(_params):
        yield object(), object()

    @asynccontextmanager
    async def sessao(_r, _w):
        class _S:
            async def initialize(self):
                return None

        yield _S()

    from backend.tools import mcp as mcp_module

    monkeypatch.delenv("VECTORA_MCP_STDIO_SANDBOX_LAUNCHER", raising=False)
    monkeypatch.setattr(mcp_launcher, "jail_available", lambda: True)
    monkeypatch.setattr(mcp_launcher, "build_jailed_launch", falso)
    monkeypatch.setattr(mcp_module, "stdio_client", stdio)
    monkeypatch.setattr(mcp_module, "ClientSession", sessao)
    client = VectoraMCPClient()
    try:
        await client._connect_one(
            "s",
            {
                "transport": "stdio",
                "command": "x",
                "args": [],
                "require_sandbox": True,
                "allow_hosts": ["api.exemplo.com"],
            },
        )
    finally:
        await client.aclose()

    assert capturado["allow_hosts"] == ("api.exemplo.com",)
