"""Testes unitários para o módulo unificado de política de saída de rede (egress_policy)."""

from __future__ import annotations

import socket

import pytest

from backend.services.egress_policy import (
    BackendEgressCapability,
    EgressPolicy,
    is_url_allowed,
    validate_backend_egress_support,
)


@pytest.fixture(autouse=True)
def mock_dns(monkeypatch):
    """Garante resolução simulada e rápida de DNS sem depender de rede externa."""

    def _mock_getaddrinfo(host, port, *args, **kwargs):
        if host in ("localhost", "127.0.0.1"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        if "internal" in host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", port))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", _mock_getaddrinfo)


def test_egress_policy_default():
    policy = EgressPolicy()
    assert policy.block_private_ips is True
    assert 80 in policy.allowed_ports
    assert 443 in policy.allowed_ports
    assert policy.requires_network_restriction() is True


def test_is_url_allowed_invalid_url():
    ok, reason = is_url_allowed("")
    assert ok is False
    assert "vazia ou inválida" in reason

    ok, reason = is_url_allowed("ftp://example.com/file")
    assert ok is False
    assert "não permitido" in reason


def test_is_url_allowed_denied_host():
    policy = EgressPolicy(denied_hosts=["malicious.com", "*.evil.org"])

    ok, reason = is_url_allowed("https://malicious.com/api", policy)
    assert ok is False
    assert "explicitamente bloqueado" in reason

    ok, reason = is_url_allowed("https://sub.evil.org/payload", policy)
    assert ok is False
    assert "explicitamente bloqueado" in reason


def test_is_url_allowed_allowlist_enforcement():
    policy = EgressPolicy(allowed_hosts=["api.github.com", "*.openai.com"])

    # Destino permitido
    ok, _ = is_url_allowed("https://api.github.com/repos", policy)
    assert ok is True

    # Destino não listado
    ok, reason = is_url_allowed("https://gitlab.com/repo", policy)
    assert ok is False
    assert "não consta na allowlist" in reason


def test_is_url_allowed_private_ip_blocking():
    policy = EgressPolicy(block_private_ips=True)
    ok, reason = is_url_allowed("http://internal-router.local", policy)
    assert ok is False
    assert "resolve para IP privado/restrito" in reason


def test_is_url_allowed_loopback_with_dev_port():
    # Sem porta liberada -> bloqueia
    policy = EgressPolicy(block_private_ips=True, allow_localhost_dev_ports=[])
    ok, reason = is_url_allowed("http://localhost:3000", policy)
    assert ok is False

    # Com porta 3000 explicitamente liberada para dev local -> autoriza
    policy_dev = EgressPolicy(block_private_ips=True, allow_localhost_dev_ports=[3000])
    ok, _ = is_url_allowed("http://localhost:3000", policy_dev)
    assert ok is True


def test_validate_backend_egress_support():
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
