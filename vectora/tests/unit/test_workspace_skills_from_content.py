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


def test_install_skill_from_content_writes_skill_md_and_index():
    skill = skills.install_skill_from_content(
        "u1", "Debug de streaming", "Use quando o SSE duplicar tokens", "1. Faça X"
    )

    assert skill.id == "debug-de-streaming"
    assert (skills._skills_dir("u1") / skill.id / "SKILL.md").is_file()
    installed = skills.list_skills("u1")
    assert [s.id for s in installed] == ["debug-de-streaming"]


def test_install_skill_from_content_duplicate_slug_raises_clear_error():
    skills.install_skill_from_content("u1", "Minha Skill", "desc", "corpo")

    with pytest.raises(ValueError, match="já instalada"):
        skills.install_skill_from_content("u1", "Minha Skill", "outra desc", "outro")


def test_install_skill_from_content_empty_name_or_description_raises():
    with pytest.raises(ValueError, match="name vazio"):
        skills.install_skill_from_content("u1", "  ", "desc", "corpo")
    with pytest.raises(ValueError, match="description vazio"):
        skills.install_skill_from_content("u1", "Nome", "  ", "corpo")
