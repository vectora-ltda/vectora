"""Remember — install_skill_from_content: instala uma skill gerada pelo
learning loop diretamente a partir de conteúdo em memória (sem git/path)."""

from __future__ import annotations

import pytest

from backend.workspace import skills


@pytest.fixture(autouse=True)
def _isolated_skills_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        skills,
        "_skills_dir",
        lambda user_id, scope="user", target=None: tmp_path / user_id,
    )
    skills._versions.clear()


def test_install_skill_from_content_writes_skill_md_and_index() -> None:
    skill = skills.install_skill_from_content(
        "u1", "Debug de streaming", "Use quando o SSE duplicar tokens", "1. Faça X"
    )

    assert skill.id == "debug-de-streaming"
    assert (skills._skills_dir("u1") / skill.id / "SKILL.md").is_file()
    installed = skills.list_skills("u1")
    assert [s.id for s in installed] == ["debug-de-streaming"]


def test_install_skill_from_content_duplicate_slug_raises_clear_error() -> None:
    skills.install_skill_from_content("u1", "Minha Skill", "desc", "corpo")

    with pytest.raises(ValueError, match="já instalada"):
        skills.install_skill_from_content("u1", "Minha Skill", "outra desc", "outro")


def test_install_skill_from_content_empty_name_or_description_raises() -> None:
    with pytest.raises(ValueError, match="name vazio"):
        skills.install_skill_from_content("u1", "  ", "desc", "corpo")
    with pytest.raises(ValueError, match="description vazio"):
        skills.install_skill_from_content("u1", "Nome", "  ", "corpo")


def test_runtime_skill_install_uses_session_scoped_memory(tmp_path) -> None:
    source = tmp_path / "runtime-skill"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: Runtime\ndescription: Session only\n---\n", encoding="utf-8"
    )

    installed = skills.install_skill(
        "u1", str(source), "runtime", "run-1", confirm_unverified=True
    )

    assert installed.id == "runtime"
    assert skills.list_skills("u1", "runtime", "run-1")[0].id == "runtime"
    assert skills.list_skills("u1", "runtime", "run-2") == []
