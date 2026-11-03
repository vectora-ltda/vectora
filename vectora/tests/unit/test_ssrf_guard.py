"""Testes para backend/browser/ssrf_guard.py.

Bloqueia navegação de fetch_url (tool + fallback local via Chromium) para
IPs privados/loopback/link-local/metadata — inclui `169.254.169.254`
(metadata de nuvem) e `localhost:<porta>` (serviços internos, incluindo o
próprio backend Vectora).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.browser.ssrf_guard import _is_blocked_ip, is_url_ssrf_safe


class TestIsBlockedIp:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",  # loopback
            "169.254.169.254",  # link-local / metadata de nuvem
            "10.0.0.1",  # RFC 1918
            "172.16.0.1",  # RFC 1918
            "192.168.1.1",  # RFC 1918
            "0.0.0.0",  # unspecified  # noqa: S104
            "::1",  # loopback IPv6
            "fe80::1",  # link-local IPv6
            "fc00::1",  # unique local IPv6 (is_private)
            "100.64.0.1",  # CGNAT (RFC 6598)
            "64:ff9b::c000:0201",  # NAT64 embedding 192.0.2.1
        ],
    )
    def test_blocks_private_and_special_ranges(self, ip):
        assert _is_blocked_ip(ip) is True

    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34"])
    def test_allows_public_ips(self, ip):
        assert _is_blocked_ip(ip) is False

    def test_unparseable_string_is_blocked(self):
        assert _is_blocked_ip("not-an-ip") is True


class TestIsUrlSsrfSafe:
    def test_blocks_metadata_ip(self):
        with patch(
            "socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("169.254.169.254", 80))],
        ):
            assert is_url_ssrf_safe("http://169.254.169.254/latest/meta-data/") is False

    def test_blocks_localhost(self):
        with patch(
            "socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 8080))]
        ):
            assert is_url_ssrf_safe("http://localhost:8080/health") is False

    def test_allows_public_domain(self):
        with patch(
            "socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 80))]
        ):
            assert is_url_ssrf_safe("https://example.com/") is True

    def test_dns_rebinding_domain_resolving_to_private_ip_is_blocked(self):
        """Domínio público que resolve pra IP interno (DNS rebinding) — o
        ponto de checar o IP resolvido, não só a string do host."""
        with patch(
            "socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 80))]
        ):
            assert is_url_ssrf_safe("http://attacker-controlled.example/") is False

    def test_dns_rebinding_domain_resolving_to_cgnat_is_blocked(self):
        """CGNAT não é público só porque ``ipaddress`` não o chama privado."""
        with patch(
            "socket.getaddrinfo", return_value=[(2, 1, 6, "", ("100.64.0.1", 80))]
        ):
            assert is_url_ssrf_safe("http://internal-carrier.example/") is False

    def test_url_without_hostname_is_blocked(self):
        assert is_url_ssrf_safe("not-a-valid-url") is False

    def test_dns_resolution_failure_is_blocked(self):
        with patch("socket.getaddrinfo", side_effect=OSError("no such host")):
            assert is_url_ssrf_safe("http://nonexistent.invalid/") is False
