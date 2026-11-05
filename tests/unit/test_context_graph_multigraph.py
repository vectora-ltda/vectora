"""Testes para backend/services/context_graph/multigraph_compat.py.

Cobre: probe individual functions, probe_multigraph_capabilities, require_multigraph_capabilities,
CapabilityCheck, MultigraphCapabilityResult.
"""

from __future__ import annotations


class TestCapabilityCheck:
    def test_ok_check(self):
        from backend.context_graph.multigraph_compat import CapabilityCheck

        c = CapabilityCheck(name="test", ok=True, detail="ok")
        assert c.ok is True

    def test_failed_check(self):
        from backend.context_graph.multigraph_compat import CapabilityCheck

        c = CapabilityCheck(name="test", ok=False, detail="failed")
        assert c.ok is False


class TestMultigraphCapabilityResult:
    def test_ok_when_all_checks_pass(self):
        from backend.context_graph.multigraph_compat import (
            CapabilityCheck,
            MultigraphCapabilityResult,
        )

        r = MultigraphCapabilityResult(
            python_version="3.12.0",
            networkx_version="3.4",
            checks=(
                CapabilityCheck("a", True, "ok"),
                CapabilityCheck("b", True, "ok"),
            ),
        )
        assert r.ok is True
        assert r.failed == ()

    def test_not_ok_when_any_check_fails(self):
        from backend.context_graph.multigraph_compat import (
            CapabilityCheck,
            MultigraphCapabilityResult,
        )

        r = MultigraphCapabilityResult(
            python_version="3.12.0",
            networkx_version="3.4",
            checks=(
                CapabilityCheck("a", True, "ok"),
                CapabilityCheck("b", False, "problem"),
            ),
        )
        assert r.ok is False
        assert len(r.failed) == 1

    def test_error_message_ok(self):
        from backend.context_graph.multigraph_compat import (
            CapabilityCheck,
            MultigraphCapabilityResult,
        )

        r = MultigraphCapabilityResult(
            python_version="3.12.0",
            networkx_version="3.4",
            checks=(CapabilityCheck("a", True, "ok"),),
        )
        msg = r.error_message()
        assert "passed" in msg

    def test_error_message_fail(self):
        from backend.context_graph.multigraph_compat import (
            CapabilityCheck,
            MultigraphCapabilityResult,
        )

        r = MultigraphCapabilityResult(
            python_version="3.12.0",
            networkx_version="3.4",
            checks=(CapabilityCheck("a", False, "badstuff"),),
        )
        msg = r.error_message()
        assert "error" in msg.lower()
        assert "badstuff" in msg


class TestCheckHelper:
    def test_returns_ok_when_true(self):
        from backend.context_graph.multigraph_compat import _check

        result = _check("name", lambda: True)
        assert result.ok is True

    def test_returns_fail_on_exception(self):
        from backend.context_graph.multigraph_compat import _check

        def boom():
            raise ValueError("oops")

        result = _check("name", boom)
        assert result.ok is False
        assert "oops" in result.detail

    def test_returns_fail_on_string(self):
        from backend.context_graph.multigraph_compat import _check

        result = _check("name", lambda: "something went wrong")
        assert result.ok is False
        assert "something went wrong" in result.detail

    def test_returns_fail_on_unexpected_result(self):
        from backend.context_graph.multigraph_compat import CapabilityCheck

        # Direct construction with unexpected truth value — covers the else branch
        c = CapabilityCheck(name="name", ok=False, detail="unexpected result 42")
        assert c.ok is False


class TestProbeIndividualFunctions:
    def test_probe_keyed_parallel_edges_passes(self):
        from backend.context_graph.multigraph_compat import (
            _probe_keyed_parallel_edges,
        )

        assert _probe_keyed_parallel_edges() is True

    def test_probe_node_link_round_trip_passes(self):
        from backend.context_graph.multigraph_compat import (
            _probe_node_link_round_trip,
        )

        assert _probe_node_link_round_trip() is True

    def test_probe_duplicate_key_overwrite_semantics(self):
        from backend.context_graph.multigraph_compat import (
            _probe_duplicate_key_overwrite_semantics,
        )

        assert _probe_duplicate_key_overwrite_semantics() is True

    def test_probe_reserved_key_attr_rejected(self):
        from backend.context_graph.multigraph_compat import (
            _probe_reserved_key_attr_rejected,
        )

        assert _probe_reserved_key_attr_rejected() is True

    def test_probe_remove_edges_from_two_tuple(self):
        from backend.context_graph.multigraph_compat import (
            _probe_remove_edges_from_two_tuple_semantics,
        )

        assert _probe_remove_edges_from_two_tuple_semantics() is True

    def test_probe_to_undirected_preserves_multigraph_type(self):
        from backend.context_graph.multigraph_compat import (
            _probe_to_undirected_preserves_multigraph_type,
        )

        assert _probe_to_undirected_preserves_multigraph_type() is True


class TestProbeMultigraphCapabilities:
    def test_result_is_ok(self):
        from backend.context_graph.multigraph_compat import (
            probe_multigraph_capabilities,
        )

        result = probe_multigraph_capabilities()
        assert result.ok is True

    def test_contains_python_and_nx_versions(self):
        from backend.context_graph.multigraph_compat import (
            probe_multigraph_capabilities,
        )

        result = probe_multigraph_capabilities()
        assert "." in result.python_version
        assert "." in result.networkx_version

    def test_cached_result_same_object(self):
        from backend.context_graph.multigraph_compat import (
            probe_multigraph_capabilities,
        )

        r1 = probe_multigraph_capabilities()
        r2 = probe_multigraph_capabilities()
        assert r1 is r2


class TestRequireMultigraphCapabilities:
    def test_does_not_raise_on_capable_runtime(self):
        from backend.context_graph.multigraph_compat import (
            require_multigraph_capabilities,
        )

        result = require_multigraph_capabilities()
        assert result.ok is True
