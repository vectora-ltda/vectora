"""Serviço unificado de governança de tráfego de saída (egress) e proteção contra SSRF.

Aplica regras de rede sobre requisições HTTP de ferramentas nativas,
transportes MCP e navegação do browser, além de mapear conformidade
de isolamento de rede por backend de sandbox.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from backend.browser.ssrf_guard import _is_blocked_ip

logger = logging.getLogger(__name__)


class BackendEgressCapability(StrEnum):
    """Nível de capacidade de enforcement de rede por backend de sandbox."""

    APPLIES = "applies"
    PARTIAL = "partial"
    DOES_NOT_APPLY = "does_not_apply"


#: Mapeamento canônico de capacidade de egress por backend de execução
BACKEND_EGRESS_CAPABILITIES: dict[str, BackendEgressCapability] = {
    "docker": BackendEgressCapability.APPLIES,
    "bubblewrap": BackendEgressCapability.APPLIES,
    "bwrap": BackendEgressCapability.APPLIES,
    "macos": BackendEgressCapability.APPLIES,
    "landlock": BackendEgressCapability.PARTIAL,
    "singularity": BackendEgressCapability.PARTIAL,
    "windows": BackendEgressCapability.DOES_NOT_APPLY,
    "ssh": BackendEgressCapability.DOES_NOT_APPLY,
    "modal": BackendEgressCapability.DOES_NOT_APPLY,
}


class EgressPolicy(BaseModel):
    """Política de restrição de tráfego de saída para um workspace ou execução."""

    workspace_id: str | None = None
    block_private_ips: bool = True
    allowed_hosts: list[str] = Field(default_factory=list)
    denied_hosts: list[str] = Field(default_factory=list)
    allowed_ports: list[int] = Field(default_factory=lambda: [80, 443])
    allow_localhost_dev_ports: list[int] = Field(default_factory=list)

    def requires_network_restriction(self) -> bool:
        """Indica se a política impõe restrições ativas de saída."""
        return (
            self.block_private_ips
            or bool(self.allowed_hosts)
            or bool(self.denied_hosts)
            or bool(self.allowed_ports)
        )


def _normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def _matches_pattern(host: str, pattern: str) -> bool:
    norm_host = _normalize_host(host)
    norm_pat = _normalize_host(pattern)
    if norm_pat.startswith("*."):
        suffix = norm_pat[2:]
        return norm_host == suffix or norm_host.endswith("." + suffix)
    return norm_host == norm_pat


def _validate_addr_infos_and_port(
    host: str,
    port: int,
    pol: EgressPolicy,
    addr_infos: list[tuple[Any, ...]],
) -> tuple[bool, str, str | None]:
    is_loopback_target = False
    first_ip: str | None = None
    for info in addr_infos:
        ip_str = str(info[4][0])
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, f"Endereço IP resolvido inválido: {ip_str}", None

        if ip_obj.is_loopback:
            is_loopback_target = True

        if pol.block_private_ips and (_is_blocked_ip(ip_str) or not ip_obj.is_global):
            # Se for loopback mas a porta estiver explicitamente autorizada para dev server local
            if ip_obj.is_loopback and port in pol.allow_localhost_dev_ports:
                if first_ip is None:
                    first_ip = ip_str
                continue
            return (
                False,
                f"Destino '{host}' resolve para IP privado/restrito ({ip_str}) não autorizado",
                None,
            )
        if first_ip is None:
            first_ip = ip_str

    # 4. Checagem de porta: se allowed_ports estiver vazia, não há restrição de porta
    if pol.allowed_ports:
        valid_ports = set(pol.allowed_ports)
        if is_loopback_target:
            valid_ports.update(pol.allow_localhost_dev_ports)

        if port not in valid_ports:
            return (
                False,
                f"Porta de conexão {port} não autorizada pela política de egress",
                None,
            )

    return True, "", first_ip


def is_url_allowed(  # noqa: PLR0911
    url: str, policy: EgressPolicy | None = None
) -> tuple[bool, str]:
    """Verifica se a URL é autorizada pela política de egress e segura contra SSRF.

    Retorna uma tupla (permitido, motivo).
    """
    if not url or not isinstance(url, str):
        return False, "URL vazia ou inválida"

    try:
        parsed = urlparse(url)
    except Exception as exc:
        return False, f"Falha ao processar URL: {exc}"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return (
            False,
            f"Esquema de URL '{scheme}' não permitido; utilize apenas HTTP ou HTTPS",
        )

    host = parsed.hostname
    if not host:
        return False, "Hostname ausente na URL"

    port = parsed.port or (443 if scheme == "https" else 80)
    pol = policy or EgressPolicy()

    # 1. Checagem contra denylist explícita de hosts
    for denied in pol.denied_hosts:
        if _matches_pattern(host, denied):
            return (
                False,
                f"Host '{host}' explicitamente bloqueado pela política de egress",
            )

    # 2. Checagem contra allowlist de hosts (se configurada)
    if pol.allowed_hosts:
        allowed = any(_matches_pattern(host, pat) for pat in pol.allowed_hosts)
        if not allowed:
            return (
                False,
                f"Host '{host}' não consta na allowlist de destinos autorizados",
            )

    # 3. Resolução DNS para proteção contra SSRF e DNS rebinding
    try:
        addr_infos = socket.getaddrinfo(host, port)
    except Exception as exc:
        logger.warning("egress_policy: falha ao resolver DNS de %s: %s", host, exc)
        return False, f"Falha na resolução de DNS para '{host}'"

    ok, reason, _ = _validate_addr_infos_and_port(host, port, pol, addr_infos)
    return ok, reason


async def async_is_url_allowed(  # noqa: PLR0911
    url: str, policy: EgressPolicy | None = None
) -> tuple[bool, str, str | None]:
    """Valida assincronamente a URL e retorna (permitido, motivo, ip_validado)."""
    if not url or not isinstance(url, str):
        return False, "URL vazia ou inválida", None

    try:
        parsed = urlparse(url)
    except Exception as exc:
        return False, f"Falha ao processar URL: {exc}", None

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return (
            False,
            f"Esquema de URL '{scheme}' não permitido; utilize apenas HTTP ou HTTPS",
            None,
        )

    host = parsed.hostname
    if not host:
        return False, "Hostname ausente na URL", None

    port = parsed.port or (443 if scheme == "https" else 80)
    pol = policy or EgressPolicy()

    for denied in pol.denied_hosts:
        if _matches_pattern(host, denied):
            return (
                False,
                f"Host '{host}' explicitamente bloqueado pela política de egress",
                None,
            )

    if pol.allowed_hosts:
        allowed = any(_matches_pattern(host, pat) for pat in pol.allowed_hosts)
        if not allowed:
            return (
                False,
                f"Host '{host}' não consta na allowlist de destinos autorizados",
                None,
            )

    loop = asyncio.get_running_loop()
    try:
        addr_infos = await loop.getaddrinfo(host, port)
    except Exception as exc:
        logger.warning("egress_policy: falha ao resolver DNS de %s: %s", host, exc)
        return False, f"Falha na resolução de DNS para '{host}'", None

    return _validate_addr_infos_and_port(host, port, pol, addr_infos)


def validate_backend_egress_support(
    backend_name: str, policy: EgressPolicy
) -> tuple[bool, str]:
    """Verifica se o backend de sandbox é capaz de aplicar as restrições exigidas.

    Aplica fail-closed: se a política exigir restrição de rede e o backend não
    oferecer suporte, recusa a execução.
    """
    if not policy.requires_network_restriction():
        return True, ""

    cap = BACKEND_EGRESS_CAPABILITIES.get(
        backend_name.lower(), BackendEgressCapability.DOES_NOT_APPLY
    )
    if cap == BackendEgressCapability.DOES_NOT_APPLY:
        return (
            False,
            f"Backend de isolamento '{backend_name}' não aplica restrições de rede exigidas (fail-closed)",
        )

    return True, ""
