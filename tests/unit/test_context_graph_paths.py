"""Testes para backend/services/context_graph/paths.py.

Cobre: GRAPH_OUT, GRAPH_OUT_NAME, out_path, default_graph_json.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch


class TestOutPath:
    def test_out_path_no_parts(self):
        from backend.context_graph.paths import GRAPH_OUT, out_path

        result = out_path()
        assert result == Path(GRAPH_OUT)

    def test_out_path_with_parts(self):
        from backend.context_graph.paths import GRAPH_OUT, out_path

        result = out_path("cache", "ast")
        assert result == Path(GRAPH_OUT, "cache", "ast")


class TestDefaultGraphJson:
    def test_returns_string(self):
        from backend.context_graph.paths import default_graph_json

        result = default_graph_json()
        assert isinstance(result, str)
        assert "graph.json" in result


class TestGraphOutEnvOverride:
    def test_absolute_path_override_name(self):
        with patch.dict(os.environ, {"VECTORA_GRAPH_OUT": "/shared/output"}):
            import importlib

            import backend.context_graph.paths as paths_mod

            importlib.reload(paths_mod)
            assert paths_mod.GRAPH_OUT_NAME == "output"
