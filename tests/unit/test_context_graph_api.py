"""Testes para backend/api/handlers/context_graph.py.

Cobre helpers (_graph_dir, _read_status_file, _require_graph_json) e
os endpoints assíncronos (query, explain, path, status, affected) via chamada
direta das funções com request mockado — sem TestClient para evitar dependência
de toda a stack FastAPI.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_registry(tmp_path: Path) -> tuple[MagicMock, MagicMock]:
    ws = MagicMock()
    ws.cwd = str(tmp_path)
    registry = MagicMock()
    registry.get = MagicMock(return_value=ws)
    return registry, ws


def _write_graph(tmp_path: Path, data: dict) -> Path:
    d = tmp_path / ".vectora" / "context-graph"
    d.mkdir(parents=True, exist_ok=True)
    (d / "graph.json").write_text(json.dumps(data), encoding="utf-8")
    return d


def _write_status(graph_dir: Path, status: str, **extra) -> None:
    payload = {"status": status, "built_at": "2026-01-01T00:00:00Z", **extra}
    (graph_dir / "build_status.json").write_text(json.dumps(payload), encoding="utf-8")


def _fake_request(user_id: str = "u1") -> MagicMock:
    req = MagicMock()
    req.state.user = MagicMock()
    req.state.user.id = user_id
    return req


# ---------------------------------------------------------------------------
# _graph_dir
# ---------------------------------------------------------------------------


class TestGraphDir:
    def test_workspace_not_found_returns_none(self):
        from backend.api.handlers.context_graph import _graph_dir

        registry = MagicMock()
        registry.get = MagicMock(return_value=None)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            assert _graph_dir("nonexistent") is None

    def test_workspace_found_returns_expected_path(self, tmp_path):
        from backend.api.handlers.context_graph import _graph_dir

        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            result = _graph_dir("ws1")
        assert result == tmp_path / ".vectora" / "context-graph"


# ---------------------------------------------------------------------------
# _read_status_file
# ---------------------------------------------------------------------------


class TestReadStatusFile:
    def test_returns_none_when_file_absent(self, tmp_path):
        from backend.api.handlers.context_graph import _read_status_file

        graph_dir = tmp_path / ".vectora" / "context-graph"
        graph_dir.mkdir(parents=True)
        assert _read_status_file(graph_dir) is None

    def test_reads_done_status_with_counts(self, tmp_path):
        from backend.api.handlers.context_graph import _read_status_file

        graph_dir = tmp_path / ".vectora" / "context-graph"
        graph_dir.mkdir(parents=True)
        _write_status(graph_dir, "done", node_count=5, edge_count=12)
        s = _read_status_file(graph_dir)
        assert s is not None
        assert s.status == "done"
        assert s.node_count == 5
        assert s.edge_count == 12

    def test_reads_running_status(self, tmp_path):
        from backend.api.handlers.context_graph import _read_status_file

        graph_dir = tmp_path / ".vectora" / "context-graph"
        graph_dir.mkdir(parents=True)
        _write_status(graph_dir, "running")
        s = _read_status_file(graph_dir)
        assert s is not None
        assert s.status == "running"

    def test_reads_error_status(self, tmp_path):
        from backend.api.handlers.context_graph import _read_status_file

        graph_dir = tmp_path / ".vectora" / "context-graph"
        graph_dir.mkdir(parents=True)
        _write_status(graph_dir, "error", error="Pipeline falhou")
        s = _read_status_file(graph_dir)
        assert s is not None
        assert s.status == "error"
        assert "Pipeline falhou" in (s.error or "")

    def test_reads_running_with_step_info(self, tmp_path):
        from backend.api.handlers.context_graph import _read_status_file

        graph_dir = tmp_path / ".vectora" / "context-graph"
        graph_dir.mkdir(parents=True)
        _write_status(
            graph_dir,
            "running",
            step=2,
            step_total=9,
            step_label="Extraindo AST...",
            files_total=10,
            files_done=3,
            files_list=["src/a.py", "src/b.py"],
        )
        s = _read_status_file(graph_dir)
        assert s is not None
        assert s.step == 2
        assert s.step_total == 9
        assert s.step_label == "Extraindo AST..."
        assert s.files_total == 10
        assert s.files_done == 3
        assert s.files_list == ["src/a.py", "src/b.py"]


# ---------------------------------------------------------------------------
# _require_graph_json
# ---------------------------------------------------------------------------


class TestRequireGraphJson:
    def test_raises_404_when_file_missing(self, tmp_path):
        from fastapi import HTTPException

        from backend.api.handlers.context_graph import _require_graph_json

        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            with pytest.raises(HTTPException) as exc:
                _require_graph_json("ws1")
        assert exc.value.status_code == 404

    def test_returns_parsed_data(self, tmp_path):
        from backend.api.handlers.context_graph import _require_graph_json

        _write_graph(tmp_path, {"nodes": [{"id": "x"}], "edges": []})
        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            data = _require_graph_json("ws1")
        assert data["nodes"][0]["id"] == "x"


# ---------------------------------------------------------------------------
# get_status — leitura via build_status.json no disco
# ---------------------------------------------------------------------------


class TestGetStatus:
    @pytest.mark.asyncio
    async def test_running_from_status_file(self, tmp_path):
        from backend.api.handlers import context_graph as cg
        from backend.api.handlers.context_graph import get_status

        graph_dir = _write_graph(tmp_path, {})
        _write_status(graph_dir, "running")
        registry, _ = _make_registry(tmp_path)
        # Build ativo neste processo → status "running" é legítimo (não-órfão).
        with (
            patch("backend.workspace.workspace.workspace_registry", registry),
            patch.dict(cg._active_builds, {"ws1": object()}, clear=False),
        ):
            s = await get_status(_fake_request(), "ws1")
        assert s.status == "running"

    @pytest.mark.asyncio
    async def test_stale_running_without_active_task_returns_not_built(self, tmp_path):
        # Status "running" no disco mas sem task ativa (processo morreu) → órfão.
        from backend.api.handlers import context_graph as cg
        from backend.api.handlers.context_graph import get_status

        graph_dir = _write_graph(tmp_path, {})
        _write_status(graph_dir, "running")
        registry, _ = _make_registry(tmp_path)
        cg._active_builds.pop("ws1", None)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            s = await get_status(_fake_request(), "ws1")
        assert s.status == "not_built"

    @pytest.mark.asyncio
    async def test_stale_running_with_checkpoint_returns_paused(self, tmp_path):
        # Órfão mas com checkpoint AST → oferece resume (paused), não do zero.
        from backend.api.handlers import context_graph as cg
        from backend.api.handlers.context_graph import get_status

        graph_dir = _write_graph(tmp_path, {})
        _write_status(graph_dir, "running")
        (graph_dir / "checkpoint_ast.json").write_text("{}", encoding="utf-8")
        registry, _ = _make_registry(tmp_path)
        cg._active_builds.pop("ws1", None)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            s = await get_status(_fake_request(), "ws1")
        assert s.status == "paused"

    @pytest.mark.asyncio
    async def test_error_from_status_file(self, tmp_path):
        from backend.api.handlers.context_graph import get_status

        graph_dir = _write_graph(tmp_path, {})
        _write_status(graph_dir, "error", error="Pipeline falhou")
        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            s = await get_status(_fake_request(), "ws1")
        assert s.status == "error"
        assert "Pipeline falhou" in (s.error or "")

    @pytest.mark.asyncio
    async def test_done_from_status_file(self, tmp_path):
        from backend.api.handlers.context_graph import get_status

        graph_dir = _write_graph(tmp_path, {})
        _write_status(graph_dir, "done", node_count=10, edge_count=25)
        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            s = await get_status(_fake_request(), "ws1")
        assert s.status == "done"
        assert s.node_count == 10
        assert s.edge_count == 25

    @pytest.mark.asyncio
    async def test_falls_back_to_graph_json_when_no_status_file(self, tmp_path):
        from backend.api.handlers.context_graph import get_status

        _write_graph(tmp_path, {"nodes": [{"id": "a"}], "edges": []})
        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            s = await get_status(_fake_request(), "ws1")
        assert s.status == "done"

    @pytest.mark.asyncio
    async def test_not_built_when_no_files(self, tmp_path):
        from backend.api.handlers.context_graph import get_status

        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            s = await get_status(_fake_request(), "ws1")
        assert s.status == "not_built"


# ---------------------------------------------------------------------------
# post_query
# ---------------------------------------------------------------------------


SAMPLE_DATA = {
    "nodes": [
        {"id": "n_auth", "label": "AuthService", "file_type": "code"},
        {"id": "n_login", "label": "login", "file_type": "code"},
        {"id": "n_token", "label": "Token", "file_type": "code"},
    ],
    "edges": [
        {
            "source": "n_auth",
            "target": "n_login",
            "relation": "calls",
            "label": "calls",
        },
        {
            "source": "n_login",
            "target": "n_token",
            "relation": "calls",
            "label": "returns",
        },
    ],
}


class TestPostQuery:
    @pytest.mark.asyncio
    async def test_matches_by_label(self, tmp_path):
        from backend.api.handlers.context_graph import QueryRequest, post_query

        _write_graph(tmp_path, SAMPLE_DATA)
        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            resp = await post_query(
                _fake_request(), "ws1", QueryRequest(question="auth")
            )
        assert any(n["id"] == "n_auth" for n in resp.nodes)
        assert "1" in resp.answer

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, tmp_path):
        from backend.api.handlers.context_graph import QueryRequest, post_query

        _write_graph(tmp_path, SAMPLE_DATA)
        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            resp = await post_query(
                _fake_request(), "ws1", QueryRequest(question="zzznothingmatches")
            )
        assert resp.nodes == []

    @pytest.mark.asyncio
    async def test_includes_neighborhood_edges(self, tmp_path):
        from backend.api.handlers.context_graph import QueryRequest, post_query

        _write_graph(tmp_path, SAMPLE_DATA)
        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            resp = await post_query(
                _fake_request(), "ws1", QueryRequest(question="auth", top_k=5)
            )
        assert len(resp.edges) >= 1


# ---------------------------------------------------------------------------
# post_explain
# ---------------------------------------------------------------------------


class TestPostExplain:
    @pytest.mark.asyncio
    async def test_returns_node_and_neighbors(self, tmp_path):
        from backend.api.handlers.context_graph import ExplainRequest, post_explain

        _write_graph(tmp_path, SAMPLE_DATA)
        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            resp = await post_explain(
                _fake_request(), "ws1", ExplainRequest(node_id="n_auth")
            )
        ids = {n["id"] for n in resp.nodes}
        assert "n_auth" in ids
        assert "n_login" in ids

    @pytest.mark.asyncio
    async def test_raises_404_for_unknown_node(self, tmp_path):
        from fastapi import HTTPException

        from backend.api.handlers.context_graph import ExplainRequest, post_explain

        _write_graph(tmp_path, SAMPLE_DATA)
        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            with pytest.raises(HTTPException) as exc:
                await post_explain(
                    _fake_request(), "ws1", ExplainRequest(node_id="ghost")
                )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# post_path
# ---------------------------------------------------------------------------


class TestPostPath:
    @pytest.mark.asyncio
    async def test_returns_path_between_nodes(self, tmp_path):
        from backend.api.handlers.context_graph import PathRequest, post_path

        _write_graph(tmp_path, SAMPLE_DATA)
        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            resp = await post_path(
                _fake_request(), "ws1", PathRequest(source="n_auth", target="n_token")
            )
        ids = {n["id"] for n in resp.nodes}
        assert "n_auth" in ids
        assert "n_token" in ids
        assert "n_auth" in resp.answer

    @pytest.mark.asyncio
    async def test_raises_404_when_no_path(self, tmp_path):
        from fastapi import HTTPException

        from backend.api.handlers.context_graph import PathRequest, post_path

        _write_graph(
            tmp_path,
            {
                "nodes": [
                    {"id": "a", "label": "A", "file_type": "code"},
                    {"id": "b", "label": "B", "file_type": "code"},
                ],
                "edges": [],
            },
        )
        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            with pytest.raises(HTTPException) as exc:
                await post_path(
                    _fake_request(), "ws1", PathRequest(source="a", target="b")
                )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_for_unknown_source(self, tmp_path):
        from fastapi import HTTPException

        from backend.api.handlers.context_graph import PathRequest, post_path

        _write_graph(tmp_path, SAMPLE_DATA)
        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            with pytest.raises(HTTPException) as exc:
                await post_path(
                    _fake_request(), "ws1", PathRequest(source="ghost", target="n_auth")
                )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# post_affected
# ---------------------------------------------------------------------------


class TestPostAffected:
    @pytest.mark.asyncio
    async def test_seed_found_returns_answer(self, tmp_path):
        from backend.api.handlers.context_graph import AffectedRequest, post_affected

        _write_graph(tmp_path, SAMPLE_DATA)
        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            resp = await post_affected(
                _fake_request(), "ws1", AffectedRequest(node_query="login")
            )
        assert len(resp.answer) > 0

    @pytest.mark.asyncio
    async def test_no_graph_raises_404(self, tmp_path):
        from fastapi import HTTPException

        from backend.api.handlers.context_graph import AffectedRequest, post_affected

        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            with pytest.raises(HTTPException) as exc:
                await post_affected(
                    _fake_request(), "ws1", AffectedRequest(node_query="anything")
                )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# post_build / delete_build
# ---------------------------------------------------------------------------


class TestPostBuild:
    @pytest.mark.asyncio
    async def test_enfileira_build_e_retorna_queued(self, tmp_path):
        from backend.api.handlers.context_graph import BuildRequest, post_build

        registry, _ = _make_registry(tmp_path)
        mock_task = MagicMock()
        mock_task.add_done_callback = MagicMock()

        with patch("backend.workspace.workspace.workspace_registry", registry):
            with patch(
                "backend.api.handlers.context_graph.asyncio.create_task",
                return_value=mock_task,
            ):
                resp = await post_build(_fake_request(), "ws1", BuildRequest())

        assert resp.status == "queued"
        mock_task.add_done_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_ja_em_andamento_retorna_running(self, tmp_path):
        from backend.api.handlers.context_graph import BuildRequest, post_build

        graph_dir = tmp_path / ".vectora" / "context-graph"
        graph_dir.mkdir(parents=True)
        _write_status(graph_dir, "running")
        registry, _ = _make_registry(tmp_path)

        with patch("backend.workspace.workspace.workspace_registry", registry):
            resp = await post_build(_fake_request(), "ws1", BuildRequest())

        assert resp.status == "running"

    @pytest.mark.asyncio
    async def test_workspace_nao_encontrado_levanta_404(self):
        from fastapi import HTTPException

        from backend.api.handlers.context_graph import BuildRequest, post_build

        registry = MagicMock()
        registry.get = MagicMock(return_value=None)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            with pytest.raises(HTTPException) as exc:
                await post_build(_fake_request(), "ws_miss", BuildRequest())

        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_resume_repassa_flag_ao_pipeline(self, tmp_path):
        """resume=True no BuildRequest deve chegar ao build_workspace_graph via _run_build."""
        from backend.api.handlers.context_graph import BuildRequest, _run_build

        registry, _ = _make_registry(tmp_path)

        async def pipeline_ok(*_a, resume=False, **_k):
            return MagicMock(error=None, node_count=0, edge_count=0)

        mock_pipeline = AsyncMock(side_effect=pipeline_ok)
        with (
            patch("backend.workspace.workspace.workspace_registry", registry),
            patch(
                "backend.context_graph.pipeline.build_workspace_graph",
                new=mock_pipeline,
            ),
        ):
            await _run_build("ws1", BuildRequest(resume=True))

        assert mock_pipeline.call_count == 1
        _, kwargs = mock_pipeline.call_args
        assert kwargs.get("resume") is True

    @pytest.mark.asyncio
    async def test_file_types_repassa_ao_pipeline(self, tmp_path):
        # file_types do BuildRequest deve chegar ao pipeline (None quando vazio).
        from backend.api.handlers.context_graph import BuildRequest, _run_build

        registry, _ = _make_registry(tmp_path)

        async def pipeline_ok(*_a, **_k):
            return MagicMock(error=None, node_count=0, edge_count=0)

        mock_pipeline = AsyncMock(side_effect=pipeline_ok)
        with (
            patch("backend.workspace.workspace.workspace_registry", registry),
            patch(
                "backend.context_graph.pipeline.build_workspace_graph",
                new=mock_pipeline,
            ),
        ):
            await _run_build("ws1", BuildRequest(file_types=["document"]))
            await _run_build("ws1", BuildRequest(file_types=[]))

        first = mock_pipeline.call_args_list[0].kwargs
        second = mock_pipeline.call_args_list[1].kwargs
        assert first.get("file_types") == ["document"]
        assert second.get("file_types") is None  # vazio → None (todos)


class TestDeleteBuild:
    @pytest.mark.asyncio
    async def test_cancela_task_ativa(self, tmp_path):
        from backend.api.handlers.context_graph import delete_build

        graph_dir = tmp_path / ".vectora" / "context-graph"
        graph_dir.mkdir(parents=True)
        _write_status(graph_dir, "running")

        mock_task = MagicMock()
        mock_task.done = MagicMock(return_value=False)
        mock_task.cancel = MagicMock()

        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            with patch(
                "backend.api.handlers.context_graph._active_builds", {"ws1": mock_task}
            ):
                await delete_build(_fake_request(), "ws1")

        mock_task.cancel.assert_called_once()
        assert not (graph_dir / "build_status.json").exists()

    @pytest.mark.asyncio
    async def test_sem_task_ativa_nao_falha(self, tmp_path):
        from backend.api.handlers.context_graph import delete_build

        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            await delete_build(_fake_request(), "ws1")

    @pytest.mark.asyncio
    async def test_task_ja_concluida_nao_cancela(self, tmp_path):
        from backend.api.handlers.context_graph import delete_build

        mock_task = MagicMock()
        mock_task.done = MagicMock(return_value=True)
        mock_task.cancel = MagicMock()

        registry, _ = _make_registry(tmp_path)
        with patch("backend.workspace.workspace.workspace_registry", registry):
            with patch(
                "backend.api.handlers.context_graph._active_builds", {"ws1": mock_task}
            ):
                await delete_build(_fake_request(), "ws1")

        mock_task.cancel.assert_not_called()


# ---------------------------------------------------------------------------
# _run_build — status final (paused/error/done)
# ---------------------------------------------------------------------------


class TestRunBuildStatus:
    async def _run(self, tmp_path: Path, side_effect):
        from backend.api.handlers.context_graph import BuildRequest, _run_build

        registry, _ = _make_registry(tmp_path)
        with (
            patch("backend.workspace.workspace.workspace_registry", registry),
            patch(
                "backend.context_graph.pipeline.build_workspace_graph",
                side_effect=side_effect,
            ),
        ):
            await _run_build("ws1", BuildRequest())
        from backend.api.handlers.context_graph import _read_status_file

        return _read_status_file(tmp_path / ".vectora" / "context-graph")

    @pytest.mark.asyncio
    async def test_quota_exhausted_writes_paused(self, tmp_path):
        from backend.llm.provider_fallback import QuotaExhaustedError

        async def boom(*a, **k):
            raise QuotaExhaustedError("todos os providers esgotaram a quota")

        s = await self._run(tmp_path, boom)
        assert s is not None
        assert s.status == "paused"
        assert "esgotaram" in (s.error or "")

    @pytest.mark.asyncio
    async def test_generic_error_writes_error(self, tmp_path):
        async def boom(*a, **k):
            raise ValueError("pipeline quebrou")

        s = await self._run(tmp_path, boom)
        assert s is not None
        assert s.status == "error"

    @pytest.mark.asyncio
    async def test_quota_not_classified_as_error(self, tmp_path):
        from backend.llm.provider_fallback import QuotaExhaustedError

        async def boom(*a, **k):
            raise QuotaExhaustedError("quota")

        s = await self._run(tmp_path, boom)
        assert s is not None
        assert s.status != "error"

    @pytest.mark.asyncio
    async def test_success_writes_done_with_counts(self, tmp_path):
        result = MagicMock()
        result.error = None
        result.node_count = 7
        result.edge_count = 9

        async def ok(*a, **k):
            return result

        s = await self._run(tmp_path, ok)
        assert s is not None
        assert s.status == "done"
        assert s.node_count == 7
        assert s.edge_count == 9

    @pytest.mark.asyncio
    async def test_result_error_writes_error(self, tmp_path):
        result = MagicMock()
        result.error = "extração falhou"

        async def err(*a, **k):
            return result

        s = await self._run(tmp_path, err)
        assert s is not None
        assert s.status == "error"
        assert s.error == "extração falhou"

    @pytest.mark.asyncio
    async def test_paused_preserva_step_do_ultimo_on_progress(self, tmp_path):
        """Ao pausar por quota, step/step_total do último on_progress devem
        aparecer no status paused (para o frontend mostrar x/y passos)."""
        from backend.api.handlers.context_graph import BuildRequest, _run_build
        from backend.llm.provider_fallback import QuotaExhaustedError

        registry, _ = _make_registry(tmp_path)

        async def boom_after_progress(*a, on_progress=None, **k):
            if on_progress:
                on_progress(3, 9, "Análise semântica", 5, 10)
            raise QuotaExhaustedError("quota")

        with (
            patch("backend.workspace.workspace.workspace_registry", registry),
            patch(
                "backend.context_graph.pipeline.build_workspace_graph",
                side_effect=boom_after_progress,
            ),
        ):
            await _run_build("ws1", BuildRequest())

        from backend.api.handlers.context_graph import _read_status_file

        s = _read_status_file(tmp_path / ".vectora" / "context-graph")
        assert s is not None
        assert s.status == "paused"
        assert s.step == 3
        assert s.step_total == 9
        assert s.partial is True

    @pytest.mark.asyncio
    async def test_paused_sem_on_progress_nao_tem_step(self, tmp_path):
        """Se quota estoura antes de qualquer on_progress, step deve ser None."""
        from backend.llm.provider_fallback import QuotaExhaustedError

        async def boom_imediato(*a, **k):
            raise QuotaExhaustedError("quota imediata")

        s = await self._run(tmp_path, boom_imediato)
        assert s is not None
        assert s.status == "paused"
        assert s.step is None
        assert s.partial is True


# ---------------------------------------------------------------------------
# _check_workspace_access — dependência do router que garante que um usuário
# autenticado só lê/muta o Context Graph de workspaces que possui.
# ---------------------------------------------------------------------------


class TestCheckWorkspaceAccess:
    def _fake_owned_workspace(self, owner_id: str | None):
        ws = MagicMock()
        ws.owner_id = owner_id
        return ws

    def test_raises_403_for_other_users_workspace(self):
        from fastapi import HTTPException

        from backend.api.handlers.context_graph import _check_workspace_access

        ws = self._fake_owned_workspace("alice")
        registry = MagicMock()
        registry.get = MagicMock(return_value=ws)
        req = _fake_request(user_id="bob")
        req.state.user.role = "member"

        with (
            patch("backend.workspace.workspace.workspace_registry", registry),
            pytest.raises(HTTPException) as exc,
        ):
            _check_workspace_access(req, "ws-1")
        assert exc.value.status_code == 403

    def test_allows_owner(self):
        from backend.api.handlers.context_graph import _check_workspace_access

        ws = self._fake_owned_workspace("alice")
        registry = MagicMock()
        registry.get = MagicMock(return_value=ws)
        req = _fake_request(user_id="alice")
        req.state.user.role = "member"

        with patch("backend.workspace.workspace.workspace_registry", registry):
            _check_workspace_access(req, "ws-1")  # não levanta

    def test_allows_workspace_without_owner(self):
        from backend.api.handlers.context_graph import _check_workspace_access

        ws = self._fake_owned_workspace(None)
        registry = MagicMock()
        registry.get = MagicMock(return_value=ws)
        req = _fake_request(user_id="bob")
        req.state.user.role = "member"

        with patch("backend.workspace.workspace.workspace_registry", registry):
            _check_workspace_access(req, "ws-1")  # não levanta — legado/sem dono

    def test_allows_root_regardless_of_owner(self):
        from backend.api.handlers.context_graph import _check_workspace_access

        ws = self._fake_owned_workspace("alice")
        registry = MagicMock()
        registry.get = MagicMock(return_value=ws)
        req = _fake_request(user_id="bob")
        req.state.user.role = "root"

        with patch("backend.workspace.workspace.workspace_registry", registry):
            _check_workspace_access(req, "ws-1")  # não levanta
