# Security helpers — URL validation, safe fetch, path guards, label sanitisation
from __future__ import annotations

import html
import ipaddress
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_FETCH_BYTES = 52_428_800  # 50 MB hard cap for binary downloads
_MAX_TEXT_BYTES = 10_485_760  # 10 MB hard cap for HTML / text

_MAX_GRAPH_FILE_BYTES = 512 * 1024 * 1024  # 512 MiB


def _max_graph_file_bytes() -> int:
    raw = os.environ.get("GRAPH_MAX_GRAPH_BYTES", "").strip()
    if not raw:
        return _MAX_GRAPH_FILE_BYTES
    text = raw.upper()
    multiplier = 1
    if text.endswith("GB"):
        multiplier = 1024 * 1024 * 1024
        text = text[:-2].strip()
    elif text.endswith("MB"):
        multiplier = 1024 * 1024
        text = text[:-2].strip()
    try:
        value = int(text)
    except ValueError:
        return _MAX_GRAPH_FILE_BYTES
    if value <= 0:
        return _MAX_GRAPH_FILE_BYTES
    return value * multiplier


_BLOCKED_HOSTS = {"metadata.google.internal", "metadata.google.com"}
_CGN_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_NAT64_WKP = ipaddress.ip_network("64:ff9b::/96")


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_WKP:
        ip = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
    return (
        ip.is_private
        or ip.is_reserved
        or ip.is_loopback
        or ip.is_link_local
        or ip in _CGN_NETWORK
    )


def validate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"Blocked URL scheme '{parsed.scheme}' - only http and https are allowed. "
            f"Got: {url!r}"
        )
    hostname = parsed.hostname
    if hostname:
        if hostname.lower() in _BLOCKED_HOSTS:
            raise ValueError(
                f"Blocked cloud metadata endpoint '{hostname}'. Got: {url!r}"
            )
        try:
            infos = socket.getaddrinfo(
                hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
            for info in infos:
                addr = info[4][0]
                ip = ipaddress.ip_address(addr)
                if _ip_is_blocked(ip):
                    raise ValueError(
                        f"Blocked private/internal IP {addr} (resolved from '{hostname}'). "
                        f"Got: {url!r}"
                    )
        except socket.gaierror as exc:
            raise ValueError(
                f"DNS resolution failed for '{hostname}': {exc}. Got: {url!r}"
            ) from exc
    return url


def check_graph_file_size_cap(path: Path) -> None:
    cap = _max_graph_file_bytes()
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size > cap:
        raise ValueError(
            f"graph file {path} is {size:_d} bytes, exceeds {cap:_d}-byte cap\n"
            f"(set GRAPH_MAX_GRAPH_BYTES=<bytes> or "
            f"GRAPH_MAX_GRAPH_BYTES=<N>GB to raise the limit)"
        )


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_LABEL_LEN = 256


def sanitize_label(text: str | None) -> str:
    if text is None:
        return ""
    text = _CONTROL_CHAR_RE.sub("", str(text))
    if len(text) > _MAX_LABEL_LEN:
        text = text[:_MAX_LABEL_LEN]
    return text


_METADATA_MAX_VALUE_LEN = 512
_METADATA_MAX_LIST_ITEMS = 50


def _sanitize_metadata_string(value: object) -> str:
    text = _CONTROL_CHAR_RE.sub("", str(value))
    text = html.escape(text, quote=True)
    if len(text) > _METADATA_MAX_VALUE_LEN:
        text = text[:_METADATA_MAX_VALUE_LEN]
    return text


def _sanitize_metadata_value(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _sanitize_metadata_string(value)
    if isinstance(value, dict):
        return sanitize_metadata(value)
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_metadata_value(item) for item in value[:_METADATA_MAX_LIST_ITEMS]
        ]
    if isinstance(value, (int, float)) or value is None:
        return value
    return _sanitize_metadata_string(value)


def sanitize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, object]:
    if metadata is None:
        return {}
    result: dict[str, object] = {}
    for key, value in metadata.items():
        clean_key = _sanitize_metadata_string(key)
        if not clean_key:
            continue
        result[clean_key] = _sanitize_metadata_value(value)
    return result
