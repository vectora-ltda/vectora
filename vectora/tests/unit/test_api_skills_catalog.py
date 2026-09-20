"""GET /skills/catalog — catálogo curado de skills do registry remoto,
distinto de GET /skills (que lista as instaladas)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import Request

from backend.api.handlers import skills as skills_handler
from backend.workspace.skills import list_wellknown_catalog


@pytest.mark.asyncio
async def test_get_skills_catalog_returns_remote_entries(monkeypatch):
    monkeypatch.setattr(
        skills_handler.registry_client,
        "fetch_catalog",
        AsyncMock(
            return_value=[
                {
                    "id": "s1",
                    "name": "Skill 1",
                    "source": "https://github.com/vectora-ltda/skill-1",
                }
            ]
        ),
    )

    result = await skills_handler.get_skills_catalog()

    assert result.total == 1
    assert result.entries[0].id == "s1"
    assert result.entries[0].catalog_source == "remote"


@pytest.mark.parametrize(
    ("provider", "invalid_source"),
    [
        ("remote", None),
        ("remote", ""),
        ("remote", "   "),
        ("enterprise", None),
        ("enterprise", ""),
        ("enterprise", "   "),
    ],
)
@pytest.mark.asyncio
async def test_get_skills_catalog_skips_entries_without_usable_source(
    monkeypatch: pytest.MonkeyPatch,
    provider: Literal["remote", "enterprise"],
    invalid_source: str | None,
) -> None:
    valid_entry = {
        "id": f"valid-{provider}",
        "name": f"Valid {provider}",
        "source": f"https://github.com/vectora-ltda/valid-{provider}",
    }
    invalid_entry = {"id": f"invalid-{provider}", "name": "Invalid"}
    if invalid_source is not None:
        invalid_entry["source"] = invalid_source

    remote_entries = [valid_entry, invalid_entry] if provider == "remote" else []
    enterprise_entries = (
        [valid_entry, invalid_entry] if provider == "enterprise" else []
    )
    monkeypatch.setattr(
        skills_handler.registry_client,
        "fetch_catalog",
        AsyncMock(return_value=remote_entries),
    )
    monkeypatch.setattr(
        skills_handler.registry_client,
        "fetch_enterprise_catalog",
        AsyncMock(return_value=enterprise_entries),
    )

    result = await skills_handler.get_skills_catalog()

    assert [(entry.id, entry.catalog_source) for entry in result.entries] == [
        (f"valid-{provider}", provider)
    ]


@pytest.mark.asyncio
async def test_get_skills_catalog_empty_is_not_error(monkeypatch):
    monkeypatch.setattr(
        skills_handler.registry_client, "fetch_catalog", AsyncMock(return_value=[])
    )

    result = await skills_handler.get_skills_catalog()

    assert result.model_dump() == {"entries": [], "total": 0}


def test_local_catalog_marks_entry_provenance(tmp_path: Path) -> None:
    skill_dir = tmp_path / "vectora-utilities"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: Vectora Utilities
description: Utilidades locais do usuário
version: 1.0.0
---
""",
        encoding="utf-8",
    )

    assert [
        entry.model_dump(exclude_none=True)
        for entry in list_wellknown_catalog(tmp_path)
    ] == [
        {
            "id": "vectora-utilities",
            "name": "Vectora Utilities",
            "description": "Utilidades locais do usuário",
            "source": str(skill_dir),
            "catalog_source": "local",
            "category": "local",
            "tags": [],
        }
    ]


class TestSkillsCatalogQueryFilters:
    """`GET /skills/catalog?q=&category=&tags=` — filtro em memória sobre
    o catálogo já cacheado por `registry_client.fetch_catalog`."""

    _ENTRIES = [
        {
            "id": "s1",
            "name": "Docker Deploy",
            "description": "publica containers",
            "source": "https://github.com/vectora-ltda/docker-deploy",
            "category": "devtools",
            "tags": ["docker", "ci"],
        },
        {
            "id": "s2",
            "name": "Writer",
            "description": "gera documentação",
            "source": "https://github.com/vectora-ltda/writer",
            "category": "docs",
            "tags": ["markdown"],
        },
    ]

    @pytest.mark.asyncio
    async def test_q_filtra_por_nome_ou_descricao(self, monkeypatch):
        monkeypatch.setattr(
            skills_handler.registry_client,
            "fetch_catalog",
            AsyncMock(return_value=self._ENTRIES),
        )

        result = await skills_handler.get_skills_catalog(q="docker")

        assert [entry.id for entry in result.entries] == ["s1"]
        assert result.total == 1

    @pytest.mark.asyncio
    async def test_category_filtra_exato(self, monkeypatch):
        monkeypatch.setattr(
            skills_handler.registry_client,
            "fetch_catalog",
            AsyncMock(return_value=self._ENTRIES),
        )

        result = await skills_handler.get_skills_catalog(category="docs")

        assert [entry.id for entry in result.entries] == ["s2"]

    @pytest.mark.asyncio
    async def test_tags_filtra_por_membro_da_lista(self, monkeypatch):
        monkeypatch.setattr(
            skills_handler.registry_client,
            "fetch_catalog",
            AsyncMock(return_value=self._ENTRIES),
        )

        result = await skills_handler.get_skills_catalog(tags="ci")

        assert [entry.id for entry in result.entries] == ["s1"]

    @pytest.mark.asyncio
    async def test_sem_match_devolve_lista_vazia_nao_erro(self, monkeypatch):
        monkeypatch.setattr(
            skills_handler.registry_client,
            "fetch_catalog",
            AsyncMock(return_value=self._ENTRIES),
        )

        result = await skills_handler.get_skills_catalog(
            q="nao existe nenhuma skill assim"
        )

        assert result.model_dump() == {"entries": [], "total": 0}


class TestCatalogSkillInstall:
    @pytest.mark.asyncio
    async def test_instala_apenas_a_fonte_resolvida_pelo_catalogo(self, monkeypatch):
        monkeypatch.setattr(
            skills_handler.registry_client,
            "fetch_catalog",
            AsyncMock(
                return_value=[
                    {
                        "id": "remote-skill-1",
                        "name": "Minha Skill",
                        "description": "faz coisas",
                        "source": "https://github.com/user/skill",
                    }
                ]
            ),
        )
        monkeypatch.setattr(
            skills_handler.registry_client,
            "fetch_enterprise_catalog",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(skills_handler, "list_wellknown_catalog", lambda: [])
        install_spy = lambda user_id, source, scope, target, **kwargs: SimpleNamespace(
            model_dump=lambda: {"id": "remote-skill-1", "source": source}
        )
        monkeypatch.setattr(skills_handler, "install_skill", install_spy)

        request = cast(Request, SimpleNamespace(state=SimpleNamespace(user=None)))
        result = await skills_handler.install_user_skill(
            request,
            skills_handler.CatalogSkillInstallRequest(skill_id="remote-skill-1"),
        )

        assert result == {
            "status": "ok",
            "skill": {
                "id": "remote-skill-1",
                "source": "https://github.com/user/skill",
            },
        }

    @pytest.mark.asyncio
    async def test_fonte_fora_do_catalogo_e_rejeitada(self, monkeypatch):
        monkeypatch.setattr(
            skills_handler.registry_client,
            "fetch_catalog",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            skills_handler.registry_client,
            "fetch_enterprise_catalog",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(skills_handler, "list_wellknown_catalog", lambda: [])

        with pytest.raises(skills_handler.HTTPException) as exc_info:
            request = cast(Request, SimpleNamespace(state=SimpleNamespace(user=None)))
            await skills_handler.install_user_skill(
                request,
                skills_handler.CatalogSkillInstallRequest(skill_id="unknown"),
            )
        assert exc_info.value.status_code == 404
