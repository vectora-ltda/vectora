"""Remember — tools learn_from_session/install_learned_skill/save_learned_fact."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from backend.services.learning import DistillationResult, SkillDraft
from backend.tools.context import ToolContext
from backend.tools.learning import (
    apply_memory_consolidation,
    install_learned_skill,
    learn_from_session,
    save_learned_fact,
)
from backend.vtypes.skill import Skill


@pytest.mark.asyncio
async def test_learn_from_session_missing_thread_id_returns_error():
    result = json.loads(await learn_from_session(ctx=ToolContext()))

    assert result["status"] == "error"
    assert "thread_id" in result["error"]


@pytest.mark.asyncio
async def test_learn_from_session_returns_deduped_proposal(monkeypatch):
    from backend.services import agent_factory

    monkeypatch.setattr(
        agent_factory,
        "aget_thread_messages",
        AsyncMock(
            return_value=[
                ("human", "bug no streaming", "", []),
                ("ai", "corrigido", "", []),
            ]
        ),
    )
    monkeypatch.setattr(
        "backend.tools.learning.distill_transcript",
        AsyncMock(
            return_value=DistillationResult(
                skills=[
                    SkillDraft(name="Já instalada", description="d", content="c"),
                    SkillDraft(name="Nova", description="d2", content="c2"),
                ],
                facts=["fato durável"],
            )
        ),
    )
    monkeypatch.setattr(
        "backend.workspace.skills.list_skills",
        lambda user_id: [
            Skill(
                id="ja-instalada",
                name="Já instalada",
                description="d",
                source="git",
                path="/tmp/x",
                installed_at="2026-01-01T00:00:00Z",
                installed_by=user_id,
            )
        ],
    )

    result = json.loads(
        await learn_from_session(ctx=ToolContext(thread_id="t1", user_id="u1"))
    )

    assert result["status"] == "ok"
    assert [s["name"] for s in result["skills"]] == ["Nova"]
    assert result["facts"] == ["fato durável"]


@pytest.mark.asyncio
async def test_learn_from_session_no_signal_returns_empty_lists_not_error(
    monkeypatch,
):
    from backend.services import agent_factory

    monkeypatch.setattr(
        agent_factory,
        "aget_thread_messages",
        AsyncMock(return_value=[("human", "oi", "", [])]),
    )
    monkeypatch.setattr(
        "backend.tools.learning.distill_transcript",
        AsyncMock(return_value=DistillationResult()),
    )
    monkeypatch.setattr("backend.workspace.skills.list_skills", lambda user_id: [])

    result = json.loads(await learn_from_session(ctx=ToolContext(thread_id="t1")))

    assert result == {"status": "ok", "skills": [], "facts": []}


@pytest.mark.asyncio
async def test_install_learned_skill_calls_workspace_skills(monkeypatch, tmp_path):
    from backend.workspace import skills as skills_module

    monkeypatch.setattr(
        skills_module,
        "_skills_dir",
        lambda user_id, scope="user", target=None: tmp_path / user_id,
    )
    skills_module._versions.clear()

    result = json.loads(
        await install_learned_skill(
            name="Skill aprendida",
            description="quando usar",
            content="passo a passo",
            ctx=ToolContext(user_id="u1"),
        )
    )

    assert result["status"] == "installed"
    assert result["skill_id"] == "skill-aprendida"


@pytest.mark.asyncio
async def test_install_learned_skill_duplicate_returns_error_not_exception(
    monkeypatch, tmp_path
):
    from backend.workspace import skills as skills_module

    monkeypatch.setattr(
        skills_module,
        "_skills_dir",
        lambda user_id, scope="user", target=None: tmp_path / user_id,
    )
    skills_module._versions.clear()
    skills_module.install_skill_from_content("u1", "Dup", "d", "c")

    result = json.loads(
        await install_learned_skill(
            name="Dup",
            description="d2",
            content="c2",
            ctx=ToolContext(user_id="u1"),
        )
    )

    assert result["status"] == "error"
    assert "já instalada" in result["error"]


@pytest.mark.asyncio
async def test_install_learned_skill_mirrors_artifact_and_resolves_pending(
    monkeypatch, tmp_path
):
    """Instalar uma skill aprovada espelha um artifact na aba Plan e limpa
    a proposta pendente do gatilho automático."""
    from backend.workspace import skills as skills_module

    monkeypatch.setattr(
        skills_module,
        "_skills_dir",
        lambda user_id, scope="user", target=None: tmp_path / user_id,
    )
    skills_module._versions.clear()

    create_artifact_calls: list[dict] = []

    async def _fake_create_artifact(
        *, artifact_type: str, title: str, content: str, ctx: object
    ) -> str:
        create_artifact_calls.append(
            {
                "artifact_type": artifact_type,
                "title": title,
                "content": content,
                "ctx": ctx,
            }
        )
        return "{}"

    monkeypatch.setattr("backend.tools.fs.create_artifact", _fake_create_artifact)
    resolve_calls: list[str] = []

    async def _fake_set_pending(thread_id: str, pending: bool) -> None:
        resolve_calls.append(thread_id)
        assert pending is False

    monkeypatch.setattr(
        "backend.api.handlers.threads.set_remember_pending", _fake_set_pending
    )

    result = json.loads(
        await install_learned_skill(
            name="Skill espelhada",
            description="quando usar",
            content="passo a passo",
            ctx=ToolContext(user_id="u1", thread_id="t-mirror"),
        )
    )

    assert result["status"] == "installed"
    assert create_artifact_calls[0]["artifact_type"] == "skill_learned"
    assert resolve_calls == ["t-mirror"]


@pytest.mark.asyncio
async def test_save_learned_fact_persists_via_save_memory(monkeypatch):
    save_memory_calls: list[dict] = []

    async def _fake_save_memory(**kwargs: object) -> str:
        save_memory_calls.append(kwargs)
        return "ok"

    async def _fake_create_artifact(
        *, artifact_type: str, title: str, content: str, ctx: object
    ) -> str:
        return "{}"

    monkeypatch.setattr("backend.tools.memory.save_memory", _fake_save_memory)
    monkeypatch.setattr("backend.tools.fs.create_artifact", _fake_create_artifact)
    monkeypatch.setattr(
        "backend.api.handlers.threads.set_remember_pending",
        AsyncMock(),
    )

    result = json.loads(
        await save_learned_fact(
            fact="usuário prefere respostas curtas",
            ctx=ToolContext(user_id="u1", thread_id="t1"),
        )
    )

    assert result["status"] == "saved"
    assert save_memory_calls[0]["content"] == "usuário prefere respostas curtas"
    assert save_memory_calls[0]["metadata"] == {
        "tag": "user_model",
        "source": "learn_from_session",
    }


@pytest.mark.asyncio
async def test_save_learned_fact_error_returns_status_error_not_raised(monkeypatch):
    async def _boom_save_memory(**kwargs: object) -> str:
        raise RuntimeError("store indisponível")

    monkeypatch.setattr("backend.tools.memory.save_memory", _boom_save_memory)

    result = json.loads(
        await save_learned_fact(
            fact="fato qualquer",
            ctx=ToolContext(user_id="u1", thread_id="t1"),
        )
    )

    assert result["status"] == "error"
    assert "store indisponível" in result["error"]


# ---------------------------------------------------------------------------
# apply_memory_consolidation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_memory_consolidation_writes_section(monkeypatch, tmp_path):
    from backend.scheduling import memory_consolidation

    monkeypatch.setattr(memory_consolidation, "memory_dir", lambda: tmp_path)

    async def _fake_create_artifact(
        *, artifact_type: str, title: str, content: str, ctx: object
    ) -> str:
        return "{}"

    monkeypatch.setattr("backend.tools.fs.create_artifact", _fake_create_artifact)
    monkeypatch.setattr("backend.tools.learning._mirror_to_plan_tab", AsyncMock())

    result = json.loads(
        await apply_memory_consolidation(
            category="decisions",
            content="Usar SQLite.",
            ctx=ToolContext(user_id="u1"),
        )
    )

    assert result == {"status": "applied", "category": "decisions"}
    path = tmp_path / "decisions.md"
    assert path.exists()
    assert "Usar SQLite." in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_apply_memory_consolidation_invalid_category_returns_error_not_raised(
    monkeypatch, tmp_path
):
    from backend.scheduling import memory_consolidation

    monkeypatch.setattr(memory_consolidation, "memory_dir", lambda: tmp_path)

    result = json.loads(
        await apply_memory_consolidation(
            category="not-a-real-category",
            content="x",
            ctx=ToolContext(user_id="u1"),
        )
    )

    assert result["status"] == "error"
    assert "not-a-real-category" in result["error"]
    assert not any(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_apply_memory_consolidation_unchanged_content_returns_unchanged(
    monkeypatch, tmp_path
):
    from backend.scheduling import memory_consolidation

    (tmp_path / "gotchas.md").write_text("JWT expira rápido.", encoding="utf-8")
    monkeypatch.setattr(memory_consolidation, "memory_dir", lambda: tmp_path)
    monkeypatch.setattr("backend.tools.learning._mirror_to_plan_tab", AsyncMock())

    result = json.loads(
        await apply_memory_consolidation(
            category="gotchas",
            content="JWT expira rápido.",
            ctx=ToolContext(user_id="u1"),
        )
    )

    assert result == {"status": "unchanged", "category": "gotchas"}
