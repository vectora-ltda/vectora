"""Rede do jail: sem rota própria, só o proxy de saída decide o que sai."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
from pathlib import Path

import pytest

from backend.sandbox import egress_bridge, egress_proxy, workspace_jail
from backend.sandbox.dry_run import build_bwrap_command
from backend.sandbox.egress_proxy import (
    JAIL_PROXY_PORT,
    JAIL_SOCKET_DIR,
    SOCKET_NAME,
    EgressProxy,
    EgressRegistry,
    _split_authority,
    egress_dir_for,
)
from backend.sandbox.policy import (
    REGISTRY_HOSTS,
    SandboxPolicy,
    effective_allow_hosts,
    effective_network,
    parse_policy,
    resolve_policy,
    trusted_policy_path,
)
from tests.unit.test_sandbox_hardening import _bwrap_works, _exec

real_bwrap = pytest.mark.skipif(
    not _bwrap_works(), reason="bwrap sem user namespaces neste ambiente"
)


@pytest.fixture(autouse=True)
def _sem_deteccao_do_ambiente(monkeypatch):
    from backend.sandbox import policy as policy_module

    monkeypatch.setattr(policy_module, "_native_sandbox_available_sync", lambda: False)


def _setenv(argv: list[str]) -> dict[str, str]:
    return {
        argv[i + 1]: argv[i + 2] for i, item in enumerate(argv) if item == "--setenv"
    }


# ── política de rede ────────────────────────────────────────────────────────


def test_o_padrao_e_rede_filtrada():
    assert SandboxPolicy(enabled=True).network == "filtered"


def test_lockdown_sempre_corta_a_rede():
    assert effective_network(SandboxPolicy(enabled=True, lockdown=True)) == "off"
    assert (
        effective_network(SandboxPolicy(enabled=True, lockdown=True, network="open"))
        == "off"
    )


def test_destinos_permitidos_somam_registries_e_extras_sem_repetir():
    policy = SandboxPolicy(enabled=True, allow_hosts=("api.exemplo.com", "pypi.org"))

    hosts = effective_allow_hosts(policy)

    assert set(REGISTRY_HOSTS) <= set(hosts)
    assert "api.exemplo.com" in hosts
    assert hosts.count("pypi.org") == 1


def test_sem_registries_so_os_destinos_declarados():
    policy = SandboxPolicy(enabled=True, registry_hosts=False, allow_hosts=("a.com",))

    assert effective_allow_hosts(policy) == ("a.com",)


def test_parse_le_rede_e_destinos(tmp_path):
    arquivo = tmp_path / "p.toml"
    arquivo.write_text(
        '[sandbox]\nnetwork = "open"\nallow_hosts = ["a.com"]\nregistry_hosts = false\n'
    )

    policy = parse_policy(arquivo)

    assert (policy.network, policy.allow_hosts, policy.registry_hosts) == (
        "open",
        ("a.com",),
        False,
    )


def test_modo_de_rede_desconhecido_falha_fechado(tmp_path):
    arquivo = tmp_path / "p.toml"
    arquivo.write_text('[sandbox]\nnetwork = "tudo"\n')

    policy = parse_policy(arquivo)

    assert policy.lockdown is True


def test_repositorio_nao_abre_a_rede(tmp_path):
    (tmp_path / "vectora.toml").write_text('[sandbox]\nnetwork = "open"\n')

    policy = resolve_policy(tmp_path)

    assert policy.network == "filtered"
    assert "network" in policy.ignored_repo_keys


def test_repositorio_pode_cortar_a_rede(tmp_path):
    (tmp_path / "vectora.toml").write_text('[sandbox]\nnetwork = "off"\n')

    assert resolve_policy(tmp_path).network == "off"


def test_repositorio_nao_amplia_destinos(tmp_path):
    trusted_policy_path(tmp_path).write_text('[sandbox]\nallow_hosts = ["a.com"]\n')
    (tmp_path / "vectora.toml").write_text(
        '[sandbox]\nallow_hosts = ["a.com", "evil.com"]\nregistry_hosts = true\n'
    )

    policy = resolve_policy(tmp_path)

    assert policy.allow_hosts == ("a.com",)
    assert "allow_hosts" in policy.ignored_repo_keys


def test_repositorio_pode_tirar_os_registries(tmp_path):
    trusted_policy_path(tmp_path).write_text("[sandbox]\nregistry_hosts = true\n")
    (tmp_path / "vectora.toml").write_text("[sandbox]\nregistry_hosts = false\n")

    assert resolve_policy(tmp_path).registry_hosts is False


# ── comando do bwrap ────────────────────────────────────────────────────────


def test_rede_aberta_herda_a_do_host():
    argv = build_bwrap_command(
        SandboxPolicy(enabled=True, network="open"), "/ws", ["true"]
    )

    assert "--unshare-net" not in argv
    assert "HTTPS_PROXY" not in _setenv(argv)


def test_sem_rede_corta_e_nao_liga_ponte():
    argv = build_bwrap_command(
        SandboxPolicy(enabled=True, network="off"), "/ws", ["true"]
    )

    assert "--unshare-net" in argv
    assert "backend.sandbox.egress_bridge" not in argv


def test_filtrada_sem_proxy_nunca_abre_por_omissao():
    argv = build_bwrap_command(SandboxPolicy(enabled=True), "/ws", ["true"])

    assert "--unshare-net" in argv
    assert "backend.sandbox.egress_bridge" not in argv


def test_filtrada_com_proxy_monta_o_socket_aponta_o_ambiente_e_liga_a_ponte(tmp_path):
    argv = build_bwrap_command(
        SandboxPolicy(enabled=True), "/ws", ["echo", "oi"], egress_dir=str(tmp_path)
    )
    env = _setenv(argv)

    assert "--unshare-net" in argv
    bind = argv.index(JAIL_SOCKET_DIR)
    assert argv[bind - 2 : bind + 1] == ["--bind", str(tmp_path), JAIL_SOCKET_DIR]
    assert env["HTTPS_PROXY"] == f"http://127.0.0.1:{JAIL_PROXY_PORT}"
    assert env["https_proxy"] == env["HTTPS_PROXY"]
    assert "127.0.0.1" in env["NO_PROXY"]
    tail = argv[argv.index("--", argv.index("--chdir")) + 1 :]
    assert tail[:3] == [sys.executable, "-m", "backend.sandbox.egress_bridge"]
    assert tail[-3:] == ["--", "echo", "oi"]
    assert f"{JAIL_SOCKET_DIR}/{SOCKET_NAME}" in tail


# ── proxy ───────────────────────────────────────────────────────────────────


async def _pedir(directory: str, request: bytes) -> bytes:
    reader, writer = await asyncio.open_unix_connection(
        str(Path(directory) / SOCKET_NAME)
    )
    writer.write(request)
    await writer.drain()
    try:
        return await asyncio.wait_for(reader.read(65536), timeout=10)
    finally:
        writer.close()


@pytest.mark.asyncio
async def test_connect_para_destino_fora_da_allowlist_e_bloqueado_e_registrado():
    proxy = EgressProxy(SandboxPolicy(enabled=True))
    directory = await proxy.start()
    try:
        resposta = await _pedir(
            directory, b"CONNECT evil.example.org:443 HTTP/1.1\r\n\r\n"
        )
    finally:
        await proxy.stop()

    assert resposta.startswith(b"HTTP/1.1 403")
    assert b"X-Vectora-Egress: blocked" in resposta
    (decisao,) = proxy.decisions
    assert (decisao.host, decisao.port, decisao.allowed) == (
        "evil.example.org",
        443,
        False,
    )
    assert "allowlist" in decisao.reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "destino", ["127.0.0.1:443", "169.254.169.254:80", "10.0.0.5:443", "[::1]:443"]
)
async def test_connect_para_ip_privado_ou_de_metadados_e_bloqueado(destino):
    proxy = EgressProxy(SandboxPolicy(enabled=True, allow_hosts=("*",)))
    # Mesmo com o destino na allowlist, o IP privado é recusado.
    proxy._egress.allowed_hosts = []
    directory = await proxy.start()
    try:
        resposta = await _pedir(
            directory, f"CONNECT {destino} HTTP/1.1\r\n\r\n".encode()
        )
    finally:
        await proxy.stop()

    assert resposta.startswith(b"HTTP/1.1 403")
    assert not proxy.decisions[0].allowed


@pytest.mark.asyncio
async def test_porta_fora_de_80_e_443_e_bloqueada():
    proxy = EgressProxy(SandboxPolicy(enabled=True))
    directory = await proxy.start()
    try:
        resposta = await _pedir(directory, b"CONNECT pypi.org:22 HTTP/1.1\r\n\r\n")
    finally:
        await proxy.stop()

    assert resposta.startswith(b"HTTP/1.1 403")
    assert "Porta" in proxy.decisions[0].reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requisicao",
    [
        b"GET http://pypi.org/ HTTP/1.1\r\n\r\n",
        b"POST / HTTP/1.1\r\n\r\n",
        b"CONNECT sem-porta HTTP/1.1\r\n\r\n",
        b"lixo\r\n\r\n",
    ],
)
async def test_so_aceita_connect_bem_formado(requisicao):
    proxy = EgressProxy(SandboxPolicy(enabled=True))
    directory = await proxy.start()
    try:
        resposta = await _pedir(directory, requisicao)
    finally:
        await proxy.stop()

    assert resposta.split()[1] in {b"400", b"403"}
    assert not proxy.decisions or not proxy.decisions[0].allowed


@pytest.mark.asyncio
async def test_destino_permitido_abre_o_tunel_ate_o_ip_validado(monkeypatch):
    async def eco(reader, writer):
        writer.write(b"eco:" + await reader.read(100))
        await writer.drain()
        writer.close()

    servidor = await asyncio.start_server(eco, "127.0.0.1", 0)
    porta = servidor.sockets[0].getsockname()[1]

    async def liberado(url, policy=None):
        # O proxy conecta ao IP que a validação devolveu, não ao nome.
        return True, "", "127.0.0.1"

    monkeypatch.setattr(egress_proxy, "async_is_url_allowed", liberado)
    proxy = EgressProxy(SandboxPolicy(enabled=True))
    directory = await proxy.start()
    try:
        reader, writer = await asyncio.open_unix_connection(
            str(Path(directory) / SOCKET_NAME)
        )
        writer.write(f"CONNECT pypi.org:{porta} HTTP/1.1\r\n\r\n".encode())
        await writer.drain()
        cabecalho = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        writer.write(b"ola")
        await writer.drain()
        dados = await asyncio.wait_for(reader.read(100), 10)
        writer.close()
    finally:
        await proxy.stop()
        servidor.close()

    assert cabecalho.startswith(b"HTTP/1.1 200")
    assert dados == b"eco:ola"
    assert proxy.decisions[0].allowed is True


@pytest.mark.asyncio
async def test_decisoes_chegam_ao_callback_de_auditoria():
    vistas = []
    proxy = EgressProxy(SandboxPolicy(enabled=True), on_decision=vistas.append)
    directory = await proxy.start()
    try:
        await _pedir(directory, b"CONNECT evil.example.org:443 HTTP/1.1\r\n\r\n")
    finally:
        await proxy.stop()

    assert [d.host for d in vistas] == ["evil.example.org"]


@pytest.mark.asyncio
async def test_pasta_do_socket_e_privada_e_some_ao_parar():
    proxy = EgressProxy(SandboxPolicy(enabled=True))
    directory = await proxy.start()

    assert oct(Path(directory).stat().st_mode & 0o777) == "0o700"
    assert oct((Path(directory) / SOCKET_NAME).stat().st_mode & 0o777) == "0o600"
    await proxy.stop()
    assert not Path(directory).exists()


@pytest.mark.asyncio
async def test_registry_reusa_o_proxy_e_recria_quando_a_rede_muda():
    registry = EgressRegistry()
    politica = SandboxPolicy(enabled=True)
    try:
        a = await registry.ensure("ws", politica)
        b = await registry.ensure("ws", politica)
        c = await registry.ensure(
            "ws", SandboxPolicy(enabled=True, allow_hosts=("x.com",))
        )
    finally:
        await registry.close_all()

    assert a is b
    assert c is not a
    assert registry.get("ws") is None


@pytest.mark.asyncio
async def test_egress_dir_for_so_para_jail_local_com_rede_filtrada():
    try:
        assert await egress_dir_for("ws-a", SandboxPolicy(enabled=False)) is None
        assert (
            await egress_dir_for("ws-a", SandboxPolicy(enabled=True, backend="docker"))
            is None
        )
        assert (
            await egress_dir_for("ws-a", SandboxPolicy(enabled=True, network="open"))
            is None
        )
        assert (
            await egress_dir_for("ws-a", SandboxPolicy(enabled=True, lockdown=True))
            is None
        )
        assert await egress_dir_for("ws-a", SandboxPolicy(enabled=True))
    finally:
        await egress_proxy.egress_registry.close_all()


def test_authority_de_connect():
    assert _split_authority("pypi.org:443") == ("pypi.org", 443)
    assert _split_authority("[::1]:8443") == ("::1", 8443)
    assert _split_authority("sem-porta") == (None, None)
    assert _split_authority("a:0") == (None, None)
    assert _split_authority("a:99999") == (None, None)


# ── ponte ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ponte_liga_o_loopback_ao_socket_unix(tmp_path):
    async def eco(reader, writer):
        writer.write(b"unix:" + await reader.read(100))
        await writer.drain()
        writer.close()

    caminho = str(tmp_path / "s.sock")
    servidor = await asyncio.start_unix_server(eco, path=caminho)
    with socket.socket() as livre:
        livre.bind(("127.0.0.1", 0))
        porta = livre.getsockname()[1]
    pronta = asyncio.Event()
    tarefa = asyncio.create_task(
        egress_bridge.serve(caminho, porta, on_ready=pronta.set)
    )
    try:
        await asyncio.wait_for(pronta.wait(), 5)
        reader, writer = await asyncio.open_connection("127.0.0.1", porta)
        writer.write(b"oi")
        await writer.drain()
        dados = await asyncio.wait_for(reader.read(100), 5)
        writer.close()
    finally:
        tarefa.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarefa
        servidor.close()

    assert dados == b"unix:oi"


# ── jail real ───────────────────────────────────────────────────────────────

_TUNEL = (
    "import socket;"
    "s=socket.create_connection(('127.0.0.1',{porta}),timeout=10);"
    "s.sendall(b'CONNECT {destino} HTTP/1.1\\r\\n\\r\\n');"
    "print(s.recv(4096).decode('latin-1').splitlines()[0]);"
    "{depois}"
)


@real_bwrap
@pytest.mark.asyncio
async def test_jail_real_so_alcanca_a_rede_pelo_proxy(tmp_path, monkeypatch):
    async def eco(reader, writer):
        writer.write(b"eco:" + await reader.read(100))
        await writer.drain()
        writer.close()

    servidor = await asyncio.start_server(eco, "127.0.0.1", 0)
    porta_eco = servidor.sockets[0].getsockname()[1]
    original = egress_proxy.async_is_url_allowed

    async def decide(url, policy=None):
        # Só um nome de teste vira o eco local; o resto segue a regra real.
        if "liberado.test" in url:
            return True, "", "127.0.0.1"
        return await original(url, policy)

    monkeypatch.setattr(egress_proxy, "async_is_url_allowed", decide)
    manager = workspace_jail.WorkspaceJailManager()
    politica = SandboxPolicy(enabled=True, allow_hosts=("liberado.test",))
    try:
        worker = await manager.get_or_spawn("ws", str(tmp_path), politica)
        bloqueado = await _exec(
            worker,
            f'{sys.executable} -c "'
            + _TUNEL.format(
                porta=JAIL_PROXY_PORT,
                destino="evil.example.org:443",
                depois="",
            )
            + '"',
        )
        direto = await _exec(
            worker,
            f'{sys.executable} -c "import socket;'
            "socket.create_connection(('1.1.1.1',443),timeout=3)\"",
        )
        tunel = await _exec(
            worker,
            f'{sys.executable} -c "'
            + _TUNEL.format(
                porta=JAIL_PROXY_PORT,
                destino=f"liberado.test:{porta_eco}",
                depois="s.sendall(b'oi');print(s.recv(100).decode())",
            )
            + '"',
        )
    finally:
        await manager.close("ws")
        await egress_proxy.egress_registry.close_all()
        servidor.close()

    assert bloqueado["stdout"].startswith("HTTP/1.1 403")
    assert (
        direto["exit_code"] != 0
    )  # sem rota: não existe caminho que não passe pelo proxy
    assert tunel["stdout"].splitlines() == [
        "HTTP/1.1 200 Connection Established",
        "eco:oi",
    ]
