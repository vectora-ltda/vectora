from __future__ import annotations

import json
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


def test_remote_snapshot_replaces_isolated_replica_before_cache_invalidation(
    monkeypatch, tmp_path: Path
):
    first_path = tmp_path / "first-policy.json"
    second_path = tmp_path / "second-policy.json"
    published: list[str] = []
    monkeypatch.setattr(mcp_policy, "_policy_file", lambda: first_path)
    monkeypatch.setattr(
        "backend.persistence.kv.publish_soon",
        lambda channel, payload: published.append(payload),
    )
    mcp_policy._reset_for_tests()
    mcp_policy._replica_id = "sender"
    mcp_policy._state_origin = "sender"
    mcp_policy.set_rule("instance", ["github"], updated_by="admin")
    snapshot = json.loads(published[-1])

    monkeypatch.setattr(mcp_policy, "_policy_file", lambda: second_path)
    mcp_policy._reset_for_tests()
    mcp_policy._replica_id = "receiver"
    mcp_policy._state_origin = "receiver"
    mcp_policy.set_rule("instance", ["github", "slack"], updated_by="other")
    assert mcp_policy.evaluate("slack").allowed

    invalidations: list[bool] = []
    monkeypatch.setattr(
        "backend.workspace.plugins.invalidate_mcp_cache",
        lambda: invalidations.append(True),
    )
    mcp_policy.apply_remote_version(
        snapshot["version"], snapshot["rules"], snapshot["origin"]
    )

    assert not mcp_policy.evaluate("slack").allowed
    assert mcp_policy.evaluate("github").allowed
    assert invalidations == [True]

    mcp_policy.set_rule("instance", ["github"], updated_by="other")
    local_snapshot = json.loads(published[-1])
    assert local_snapshot["origin"] == "receiver"

    mcp_policy._reset_for_tests()
    assert not mcp_policy.evaluate("slack").allowed
    assert mcp_policy.policy_version() == local_snapshot["version"]
