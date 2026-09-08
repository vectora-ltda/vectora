from __future__ import annotations

from pathlib import Path

from backend.services import mcp_policy


def test_workspace_rule_overrides_instance_and_empty_blocks(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(mcp_policy, "_policy_file", lambda: tmp_path / "policy.json")
    mcp_policy._reset_for_tests()

    assert mcp_policy.evaluate("github", "ws-1").allowed
    mcp_policy.set_rule("instance", ["github"], updated_by="admin")
    assert mcp_policy.evaluate("github", "ws-2").allowed
    assert not mcp_policy.evaluate("slack", "ws-2").allowed
    mcp_policy.set_rule("workspace", [], workspace_id="ws-2", updated_by="admin")
    assert not mcp_policy.evaluate("github", "ws-2").allowed
    assert mcp_policy.evaluate("github", "ws-1").allowed


def test_policy_round_trips_version_and_rules(monkeypatch, tmp_path: Path):
    path = tmp_path / "policy.json"
    monkeypatch.setattr(mcp_policy, "_policy_file", lambda: path)
    mcp_policy._reset_for_tests()
    mcp_policy.set_rule("instance", ["brave-search"], updated_by="root")

    mcp_policy._reset_for_tests()
    assert mcp_policy.policy_version() == 1
    assert mcp_policy.list_rules()[0].allowlist == ["brave-search"]
