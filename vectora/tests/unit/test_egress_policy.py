"""Testes unitários para o módulo unificado de política de saída de rede (egress_policy)."""

from __future__ import annotations

import socket

import pytest

from backend.services.egress_policy import (
    BackendEgressCapability,
    EgressPolicy,
    async_is_url_allowed,
    is_url_allowed,
    validate_backend_egress_support,
)


@pytest.fixture(autouse=True)
def mock_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Garante resolução simulada e rápida de DNS sem depender de rede externa."""

    def _mock_getaddrinfo(
        host: str, port: int, *args: object, **kwargs: object
    ) -> list[
        tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]
    ]:
        if host in ("localhost", "127.0.0.1"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        if "internal" in host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", port))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", _mock_getaddrinfo)


def test_egress_policy_default() -> None:
    policy = EgressPolicy()
    assert policy.block_private_ips is True
    assert 80 in policy.allowed_ports
    assert 443 in policy.allowed_ports
    assert policy.requires_network_restriction() is True


def test_is_url_allowed_invalid_url() -> None:
    ok, reason = is_url_allowed("")
    assert ok is False
    assert "vazia ou inválida" in reason

    ok, reason = is_url_allowed("ftp://example.com/file")
    assert ok is False
    assert "não permitido" in reason


def test_is_url_allowed_denied_host() -> None:
    policy = EgressPolicy(denied_hosts=["malicious.com", "*.evil.org"])

    ok, reason = is_url_allowed("https://malicious.com/api", policy)
    assert ok is False
    assert "explicitamente bloqueado" in reason

    ok, reason = is_url_allowed("https://sub.evil.org/payload", policy)
    assert ok is False
    assert "explicitamente bloqueado" in reason


def test_is_url_allowed_allowlist_enforcement() -> None:
    policy = EgressPolicy(allowed_hosts=["api.github.com", "*.openai.com"])

    # Destino permitido
    ok, _ = is_url_allowed("https://api.github.com/repos", policy)
    assert ok is True

    # Destino não listado
    ok, reason = is_url_allowed("https://gitlab.com/repo", policy)
    assert ok is False
    assert "não consta na allowlist" in reason


def test_is_url_allowed_private_ip_blocking() -> None:
    policy = EgressPolicy(block_private_ips=True)
    ok, reason = is_url_allowed("http://internal-router.local", policy)
    assert ok is False
    assert "resolve para IP privado/restrito" in reason


def test_is_url_allowed_loopback_with_dev_port() -> None:
    # Sem porta liberada -> bloqueia
    policy = EgressPolicy(block_private_ips=True, allow_localhost_dev_ports=[])
    ok, _ = is_url_allowed("http://localhost:3000", policy)
    assert ok is False

    # Com porta 3000 explicitamente liberada para dev local -> autoriza
    policy_dev = EgressPolicy(block_private_ips=True, allow_localhost_dev_ports=[3000])
    ok, _ = is_url_allowed("http://localhost:3000", policy_dev)
    assert ok is True


def test_validate_backend_egress_support() -> None:
    policy_restricted = EgressPolicy(block_private_ips=True)
    policy_open = EgressPolicy(
        block_private_ips=False,
        allowed_hosts=[],
        denied_hosts=[],
        allowed_ports=[],
    )

    # Política sem restrição passa sempre
    ok, _ = validate_backend_egress_support("windows", policy_open)
    assert ok is True

    # Docker suporta isolamento
    ok, _ = validate_backend_egress_support("docker", policy_restricted)
    assert ok is True

    # Windows sandbox nativo não aplica isolamento de rede avançado -> fail-closed
    ok, reason = validate_backend_egress_support("windows", policy_restricted)
    assert ok is False
    assert "fail-closed" in reason


def test_is_url_allowed_non_global_ip_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    def _mock_cgnat(
        host: str, port: int, *args: object, **kwargs: object
    ) -> list[
        tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]
    ]:
        # 100.64.0.1 é CGNAT (Carrier-grade NAT) -> is_global é False
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", _mock_cgnat)
    policy = EgressPolicy(block_private_ips=True)
    ok, reason = is_url_allowed("http://cgnat-dest.example", policy)
    assert ok is False
    assert "privado/restrito" in reason


def test_is_url_allowed_empty_allowed_ports_permits_custom_port() -> None:
    policy = EgressPolicy(allowed_ports=[], block_private_ips=False)
    ok, _ = is_url_allowed("http://example.com:8080/path", policy)
    assert ok is True


def test_is_url_allowed_no_resolve_dns_blocks_literal_private_ip() -> None:
    policy = EgressPolicy(block_private_ips=True)
    # AWS metadata IP
    ok, reason = is_url_allowed(
        "http://169.254.169.254/latest/meta-data/", policy, resolve_dns=False
    )
    assert ok is False
    assert "privado/restrito" in reason

    # Localhost IPv4
    ok_lh, reason_lh = is_url_allowed(
        "http://127.0.0.1:8080", policy, resolve_dns=False
    )
    assert ok_lh is False
    assert "privado/restrito" in reason_lh

    # Private RFC 1918
    ok_priv, reason_priv = is_url_allowed("http://10.0.0.1/", policy, resolve_dns=False)
    assert ok_priv is False
    assert "privado/restrito" in reason_priv


def test_is_url_allowed_no_resolve_dns_allows_literal_public_ip() -> None:
    policy = EgressPolicy(block_private_ips=True)
    ok, reason = is_url_allowed("https://93.184.216.34/", policy, resolve_dns=False)
    assert ok is True
    assert reason == ""


@pytest.mark.parametrize("host", ["localhost", "service.internal", "127.1"])
def test_is_url_allowed_no_resolve_dns_rejects_non_public_aliases(host: str) -> None:
    ok, reason = is_url_allowed(
        f"https://{host}", EgressPolicy(block_private_ips=False), resolve_dns=False
    )
    assert ok is False
    assert "destino DNS público" in reason


@pytest.mark.asyncio
async def test_async_is_url_allowed_returns_pinned_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    async def _mock_getaddrinfo(
        host: str, port: int
    ) -> list[
        tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]
    ]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _mock_getaddrinfo)

    policy = EgressPolicy()
    ok, reason, pinned_ip = await async_is_url_allowed(
        "https://example.com/api", policy
    )
    assert ok is True
    assert reason == ""
    assert pinned_ip == "93.184.216.34"
