"""Catálogo de buckets de RAG (backend/services/rag_buckets.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import rag_buckets
from backend.workspace.runtime_settings import RuntimeSettings


@pytest.fixture
def rs(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings(path=tmp_path / "checkpoints.db")


class TestCreateAndList:
    def test_create_bucket_returns_registered_record(self, rs: RuntimeSettings) -> None:
        bucket = rag_buckets.create_bucket(
            rs, workspace_id="ws1", name="Godot Docs", source_path="/docs/godot"
        )

        assert bucket.workspace_id == "ws1"
        assert bucket.name == "Godot Docs"
        assert bucket.source_path == "/docs/godot"
        assert bucket.created_at

    def test_list_buckets_scoped_to_workspace(self, rs: RuntimeSettings) -> None:
        rag_buckets.create_bucket(rs, workspace_id="ws1", name="A")
        rag_buckets.create_bucket(rs, workspace_id="ws2", name="B")

        result = rag_buckets.list_buckets(rs, "ws1")

        assert [b.name for b in result] == ["A"]

    def test_list_buckets_empty_workspace_returns_empty_list(
        self, rs: RuntimeSettings
    ) -> None:
        assert rag_buckets.list_buckets(rs, "ws-nunca-usado") == []

    def test_two_buckets_never_share_id(self, rs: RuntimeSettings) -> None:
        a = rag_buckets.create_bucket(rs, workspace_id="ws1", name="A")
        b = rag_buckets.create_bucket(rs, workspace_id="ws1", name="B")

        assert a.id != b.id


class TestGetAndDelete:
    def test_get_bucket_returns_none_for_unknown_id(self, rs: RuntimeSettings) -> None:
        assert rag_buckets.get_bucket(rs, "id-que-nao-existe") is None

    def test_delete_bucket_removes_from_catalog(self, rs: RuntimeSettings) -> None:
        bucket = rag_buckets.create_bucket(rs, workspace_id="ws1", name="A")

        rag_buckets.delete_bucket(rs, bucket.id)

        assert rag_buckets.get_bucket(rs, bucket.id) is None

    def test_delete_bucket_unknown_id_is_noop(self, rs: RuntimeSettings) -> None:
        assert rag_buckets.delete_bucket(rs, "id-que-nao-existe") is False

    def test_delete_bucket_rejects_other_workspace(self, rs: RuntimeSettings) -> None:
        bucket = rag_buckets.create_bucket(rs, workspace_id="ws1", name="A")

        assert rag_buckets.delete_bucket(rs, bucket.id, workspace_id="ws2") is False
        assert rag_buckets.get_bucket(rs, bucket.id) is not None

    def test_delete_bucket_also_removes_from_active_set(
        self, rs: RuntimeSettings
    ) -> None:
        bucket = rag_buckets.create_bucket(rs, workspace_id="ws1", name="A")
        rag_buckets.set_active(rs, workspace_id="ws1", bucket_id=bucket.id, active=True)

        rag_buckets.delete_bucket(rs, bucket.id)

        assert rag_buckets.get_active_bucket_ids(rs, "ws1") == []


class TestActiveBuckets:
    def test_set_active_true_then_appears_in_active_ids(
        self, rs: RuntimeSettings
    ) -> None:
        bucket = rag_buckets.create_bucket(rs, workspace_id="ws1", name="A")

        rag_buckets.set_active(rs, workspace_id="ws1", bucket_id=bucket.id, active=True)

        assert rag_buckets.get_active_bucket_ids(rs, "ws1") == [bucket.id]

    def test_set_active_false_removes_from_active_ids(
        self, rs: RuntimeSettings
    ) -> None:
        bucket = rag_buckets.create_bucket(rs, workspace_id="ws1", name="A")
        rag_buckets.set_active(rs, workspace_id="ws1", bucket_id=bucket.id, active=True)

        rag_buckets.set_active(
            rs, workspace_id="ws1", bucket_id=bucket.id, active=False
        )

        assert rag_buckets.get_active_bucket_ids(rs, "ws1") == []

    def test_set_active_unknown_bucket_id_never_creates_orphan_entry(
        self, rs: RuntimeSettings
    ) -> None:
        rag_buckets.set_active(
            rs, workspace_id="ws1", bucket_id="id-que-nao-existe", active=True
        )

        assert rag_buckets.get_active_bucket_ids(rs, "ws1") == []

    def test_set_active_rejects_other_workspace(self, rs: RuntimeSettings) -> None:
        bucket = rag_buckets.create_bucket(rs, workspace_id="ws1", name="A")

        rag_buckets.set_active(rs, workspace_id="ws2", bucket_id=bucket.id, active=True)

        assert rag_buckets.get_active_bucket_ids(rs, "ws2") == []

    def test_active_buckets_isolated_per_workspace(self, rs: RuntimeSettings) -> None:
        bucket = rag_buckets.create_bucket(rs, workspace_id="ws1", name="A")
        rag_buckets.set_active(rs, workspace_id="ws1", bucket_id=bucket.id, active=True)

        assert rag_buckets.get_active_bucket_ids(rs, "ws2") == []

    def test_get_active_bucket_ids_no_workspace_active_map_returns_empty(
        self, rs: RuntimeSettings
    ) -> None:
        assert rag_buckets.get_active_bucket_ids(rs, "ws-nunca-usado") == []
