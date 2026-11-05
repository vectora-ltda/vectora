"""Testes para backend/services/context_graph/mcp_ingest.py.

Cobre: is_mcp_config_path, extract_mcp_config (happy path, mcpServers nested,
file too large, decode error, json error, root not object, no mcpServers).
"""

from __future__ import annotations

import json
from pathlib import Path


class TestIsMcpConfigPath:
    def test_mcp_json(self):
        from backend.context_graph.mcp_ingest import is_mcp_config_path

        assert is_mcp_config_path(Path(".mcp.json")) is True

    def test_claude_desktop_config(self):
        from backend.context_graph.mcp_ingest import is_mcp_config_path

        assert is_mcp_config_path(Path("claude_desktop_config.json")) is True

    def test_mcp_servers_json(self):
        from backend.context_graph.mcp_ingest import is_mcp_config_path

        assert is_mcp_config_path(Path("mcp_servers.json")) is True

    def test_mcp_plain(self):
        from backend.context_graph.mcp_ingest import is_mcp_config_path

        assert is_mcp_config_path(Path("mcp.json")) is True

    def test_random_json_not_mcp(self):
        from backend.context_graph.mcp_ingest import is_mcp_config_path

        assert is_mcp_config_path(Path("config.json")) is False

    def test_non_json_not_mcp(self):
        from backend.context_graph.mcp_ingest import is_mcp_config_path

        assert is_mcp_config_path(Path("settings.yaml")) is False


class TestExtractMcpConfig:
    def _write(self, tmp_path: Path, data: dict, filename: str = ".mcp.json") -> Path:
        p = tmp_path / filename
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_basic_mcp_servers(self, tmp_path: Path):
        from backend.context_graph.mcp_ingest import extract_mcp_config

        p = self._write(
            tmp_path,
            {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                    },
                    "github": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-github"],
                    },
                }
            },
        )
        result = extract_mcp_config(p)
        assert len(result["nodes"]) >= 2
        labels = [n["label"] for n in result["nodes"]]
        assert "filesystem" in labels
        assert "github" in labels

    def test_nested_mcp_servers(self, tmp_path: Path):
        from backend.context_graph.mcp_ingest import extract_mcp_config

        p = self._write(
            tmp_path,
            {
                "mcp": {
                    "servers": {
                        "rag": {"command": "uvx", "args": ["vectora-mcp"]},
                    }
                }
            },
        )
        result = extract_mcp_config(p)
        assert any(n["label"] == "rag" for n in result["nodes"])

    def test_env_vars_not_exposed(self, tmp_path: Path):
        from backend.context_graph.mcp_ingest import extract_mcp_config

        p = self._write(
            tmp_path,
            {
                "mcpServers": {
                    "secret-svc": {
                        "command": "cmd",
                        "env": {
                            "API_KEY": "supersecret123",
                            "DATABASE_URL": "postgres://...",
                        },
                    }
                }
            },
        )
        result = extract_mcp_config(p)
        text = json.dumps(result)
        assert "supersecret123" not in text
        assert "postgres://" not in text

    def test_env_var_names_may_appear(self, tmp_path: Path):
        from backend.context_graph.mcp_ingest import extract_mcp_config

        p = self._write(
            tmp_path,
            {
                "mcpServers": {
                    "svc": {
                        "command": "cmd",
                        "env": {"API_KEY": "secret"},
                    }
                }
            },
        )
        result = extract_mcp_config(p)
        text = json.dumps(result)
        # Values must not appear, names may appear in nodes
        assert "secret" not in text

    def test_file_not_found_returns_error(self, tmp_path: Path):
        from backend.context_graph.mcp_ingest import extract_mcp_config

        p = tmp_path / ".mcp.json"
        result = extract_mcp_config(p)
        assert "error" in result

    def test_file_too_large_returns_error(self, tmp_path: Path):
        from backend.context_graph.mcp_ingest import (
            _MAX_BYTES,
            extract_mcp_config,
        )

        p = tmp_path / ".mcp.json"
        p.write_bytes(b"{" + b" " * (_MAX_BYTES + 1))
        result = extract_mcp_config(p)
        assert "error" in result

    def test_invalid_json_returns_error(self, tmp_path: Path):
        from backend.context_graph.mcp_ingest import extract_mcp_config

        p = tmp_path / ".mcp.json"
        p.write_text("{not valid json", encoding="utf-8")
        result = extract_mcp_config(p)
        assert "error" in result

    def test_root_not_object_returns_error(self, tmp_path: Path):
        from backend.context_graph.mcp_ingest import extract_mcp_config

        p = tmp_path / ".mcp.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        result = extract_mcp_config(p)
        assert "error" in result

    def test_no_mcp_servers_returns_error(self, tmp_path: Path):
        from backend.context_graph.mcp_ingest import extract_mcp_config

        p = self._write(tmp_path, {"version": 1})
        result = extract_mcp_config(p)
        assert "error" in result

    def test_max_servers_limit(self, tmp_path: Path):
        from backend.context_graph.mcp_ingest import (
            _MAX_SERVERS_PER_FILE,
            extract_mcp_config,
        )

        servers = {
            f"svc-{i}": {"command": "cmd"} for i in range(_MAX_SERVERS_PER_FILE + 10)
        }
        p = self._write(tmp_path, {"mcpServers": servers})
        result = extract_mcp_config(p)
        # Should truncate at _MAX_SERVERS_PER_FILE
        server_nodes = [n for n in result["nodes"] if n.get("kind") == "mcp_server"]
        assert len(server_nodes) <= _MAX_SERVERS_PER_FILE

    def test_server_edges_to_config_file(self, tmp_path: Path):
        from backend.context_graph.mcp_ingest import extract_mcp_config

        p = self._write(
            tmp_path,
            {
                "mcpServers": {
                    "mytool": {"command": "node", "args": ["index.js"]},
                }
            },
        )
        result = extract_mcp_config(p)
        assert len(result["edges"]) >= 1

    def test_non_dict_server_entry_skipped(self, tmp_path: Path):
        from backend.context_graph.mcp_ingest import extract_mcp_config

        p = self._write(
            tmp_path,
            {
                "mcpServers": {
                    "bad": "not-a-dict",
                    "good": {"command": "npx"},
                }
            },
        )
        result = extract_mcp_config(p)
        labels = [n["label"] for n in result["nodes"]]
        assert "good" in labels
        assert "bad" not in labels

    def test_server_name_with_special_chars_produces_node(self, tmp_path: Path):
        from backend.context_graph.mcp_ingest import extract_mcp_config

        p = self._write(
            tmp_path,
            {
                "mcpServers": {
                    "my-server": {"command": "cmd"},
                }
            },
        )
        result = extract_mcp_config(p)
        labels = [n.get("label", "") for n in result["nodes"]]
        assert any("my-server" in lbl for lbl in labels)
