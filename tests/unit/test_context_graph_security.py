"""Testes para backend/services/context_graph/security.py.

Cobre: _ip_is_blocked, validate_url, validate_graph_path,
check_graph_file_size_cap, sanitize_label, sanitize_metadata,
_max_graph_file_bytes.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from unittest.mock import patch

import pytest


class TestIpIsBlocked:
    def test_loopback_is_blocked(self):
        from backend.context_graph.security import _ip_is_blocked

        assert _ip_is_blocked(ipaddress.ip_address("127.0.0.1"))

    def test_private_is_blocked(self):
        from backend.context_graph.security import _ip_is_blocked

        assert _ip_is_blocked(ipaddress.ip_address("192.168.1.1"))
        assert _ip_is_blocked(ipaddress.ip_address("10.0.0.1"))

    def test_cgn_is_blocked(self):
        from backend.context_graph.security import _ip_is_blocked

        assert _ip_is_blocked(ipaddress.ip_address("100.64.0.1"))

    def test_nat64_wkp_is_blocked(self):
        from backend.context_graph.security import _ip_is_blocked

        # 64:ff9b::/96 — NAT64 well-known prefix (maps to 0.0.0.0 → block)
        assert _ip_is_blocked(ipaddress.ip_address("64:ff9b::7f00:1"))

    def test_public_is_not_blocked(self):
        from backend.context_graph.security import _ip_is_blocked

        assert not _ip_is_blocked(ipaddress.ip_address("8.8.8.8"))


class TestValidateUrl:
    def test_http_allowed(self):
        from backend.context_graph.security import validate_url

        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 80))]
            result = validate_url("http://example.com/path")
        assert result == "http://example.com/path"

    def test_https_allowed(self):
        from backend.context_graph.security import validate_url

        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
            result = validate_url("https://example.com/")
        assert result.startswith("https://")

    def test_file_scheme_blocked(self):
        from backend.context_graph.security import validate_url

        with pytest.raises(ValueError, match="Blocked URL scheme"):
            validate_url("file:///etc/passwd")

    def test_blocked_cloud_metadata_host(self):
        from backend.context_graph.security import validate_url

        with pytest.raises(ValueError, match="Blocked cloud metadata"):
            validate_url("http://metadata.google.internal/computeMetadata")

    def test_private_ip_blocked(self):
        from backend.context_graph.security import validate_url

        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("192.168.1.1", 80))]
            with pytest.raises(ValueError, match="Blocked private"):
                validate_url("http://internal-host.example.com/")

    def test_dns_failure_raises(self):
        import socket as _socket

        from backend.context_graph.security import validate_url

        with patch("socket.getaddrinfo", side_effect=_socket.gaierror("DNS failure")):
            with pytest.raises(ValueError, match="DNS resolution failed"):
                validate_url("http://nonexistent-hostname-xyz.example.com/")


class TestValidateGraphPath:
    def test_valid_path_inside_base(self, tmp_path: Path):
        from backend.context_graph.security import validate_graph_path

        base = tmp_path / ".vectora/context-graph"
        base.mkdir(parents=True)
        target = base / "graph.json"
        target.write_text("{}", encoding="utf-8")

        result = validate_graph_path(target, base=base)
        assert result == target.resolve()

    def test_path_escape_raises_value_error(self, tmp_path: Path):
        from backend.context_graph.security import validate_graph_path

        base = tmp_path / ".vectora/context-graph"
        base.mkdir(parents=True)
        escaped = tmp_path / "secret.json"
        escaped.write_text("data", encoding="utf-8")

        with pytest.raises(ValueError, match="escapes"):
            validate_graph_path(escaped, base=base)

    def test_missing_file_raises_file_not_found(self, tmp_path: Path):
        from backend.context_graph.security import validate_graph_path

        base = tmp_path / ".vectora/context-graph"
        base.mkdir(parents=True)
        target = base / "nonexistent.json"

        with pytest.raises(FileNotFoundError):
            validate_graph_path(target, base=base)

    def test_nonexistent_base_raises_value_error(self, tmp_path: Path):
        from backend.context_graph.security import validate_graph_path

        base = tmp_path / "does_not_exist"
        with pytest.raises(ValueError, match="does not exist"):
            validate_graph_path(tmp_path / "something.json", base=base)


class TestCheckGraphFileSizeCap:
    def test_small_file_passes(self, tmp_path: Path):
        from backend.context_graph.security import check_graph_file_size_cap

        f = tmp_path / "small.json"
        f.write_bytes(b"x" * 100)
        check_graph_file_size_cap(f)  # no exception

    def test_oversized_file_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from backend.context_graph import security

        monkeypatch.setattr(security, "_MAX_GRAPH_FILE_BYTES", 10)
        from backend.context_graph.security import check_graph_file_size_cap

        f = tmp_path / "big.json"
        f.write_bytes(b"x" * 20)
        with pytest.raises(ValueError, match="exceeds"):
            check_graph_file_size_cap(f)

    def test_missing_file_is_ignored(self, tmp_path: Path):
        from backend.context_graph.security import check_graph_file_size_cap

        check_graph_file_size_cap(tmp_path / "ghost.json")  # no exception


class TestMaxGraphFileBytes:
    def test_default_returns_512mb(self):
        from backend.context_graph.security import _max_graph_file_bytes

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GRAPH_MAX_GRAPH_BYTES", None)
            result = _max_graph_file_bytes()
        assert result == 512 * 1024 * 1024

    def test_mb_suffix(self):
        from backend.context_graph.security import _max_graph_file_bytes

        with patch.dict(os.environ, {"GRAPH_MAX_GRAPH_BYTES": "100MB"}):
            result = _max_graph_file_bytes()
        assert result == 100 * 1024 * 1024

    def test_gb_suffix(self):
        from backend.context_graph.security import _max_graph_file_bytes

        with patch.dict(os.environ, {"GRAPH_MAX_GRAPH_BYTES": "2GB"}):
            result = _max_graph_file_bytes()
        assert result == 2 * 1024 * 1024 * 1024

    def test_invalid_value_returns_default(self):
        from backend.context_graph.security import _max_graph_file_bytes

        with patch.dict(os.environ, {"GRAPH_MAX_GRAPH_BYTES": "notanumber"}):
            result = _max_graph_file_bytes()
        assert result == 512 * 1024 * 1024

    def test_zero_returns_default(self):
        from backend.context_graph.security import _max_graph_file_bytes

        with patch.dict(os.environ, {"GRAPH_MAX_GRAPH_BYTES": "0"}):
            result = _max_graph_file_bytes()
        assert result == 512 * 1024 * 1024


class TestSanitizeLabel:
    def test_none_returns_empty(self):
        from backend.context_graph.security import sanitize_label

        assert sanitize_label(None) == ""

    def test_strips_control_chars(self):
        from backend.context_graph.security import sanitize_label

        assert sanitize_label("hello\x00world") == "helloworld"
        assert sanitize_label("tab\there") == "tabhere"

    def test_truncates_to_max_length(self):
        from backend.context_graph.security import sanitize_label

        assert len(sanitize_label("x" * 300)) == 256

    def test_normal_string_unchanged(self):
        from backend.context_graph.security import sanitize_label

        assert sanitize_label("AuthService") == "AuthService"


class TestSanitizeMetadata:
    def test_none_returns_empty(self):
        from backend.context_graph.security import sanitize_metadata

        assert sanitize_metadata(None) == {}

    def test_string_values_sanitized(self):
        from backend.context_graph.security import sanitize_metadata

        result = sanitize_metadata({"key": "value"})
        assert result["key"] == "value"

    def test_empty_key_dropped(self):
        from backend.context_graph.security import sanitize_metadata

        result = sanitize_metadata({"\x00": "value"})
        assert result == {}

    def test_nested_dict_sanitized(self):
        from backend.context_graph.security import sanitize_metadata

        result = sanitize_metadata({"meta": {"inner": "data"}})
        assert isinstance(result["meta"], dict)

    def test_list_values_sanitized(self):
        from backend.context_graph.security import sanitize_metadata

        result = sanitize_metadata({"items": ["a", "b", "c"]})
        assert result["items"] == ["a", "b", "c"]

    def test_bool_passthrough(self):
        from backend.context_graph.security import sanitize_metadata

        result = sanitize_metadata({"flag": True})
        assert result["flag"] is True

    def test_int_passthrough(self):
        from backend.context_graph.security import sanitize_metadata

        result = sanitize_metadata({"count": 42})
        assert result["count"] == 42

    def test_none_value_passthrough(self):
        from backend.context_graph.security import sanitize_metadata

        result = sanitize_metadata({"empty": None})
        assert result["empty"] is None

    def test_non_string_value_converted(self):
        from backend.context_graph.security import sanitize_metadata

        class Obj:
            def __str__(self) -> str:
                return "custom"

        result = sanitize_metadata({"obj": Obj()})
        assert "custom" in str(result["obj"])
