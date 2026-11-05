"""Tests — índice de busca unificado (fatos + skills + buckets RAG).

Verifica:
- busca com termo presente nos três tipos retorna os três (happy path)
- usuário sem nenhuma memória de nenhum tipo retorna lista vazia sem lançar
- falha isolada num tipo (skills corrompidas) não derruba os outros dois
- filtro por `types` restringe a busca a um subconjunto
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from backend.services.memory_index import _search_facts, search_unified_memory
from backend.testing.mocks import NativeInMemoryStore
from backend.vtypes.skill import Skill


@dataclass
class _FakeBucket:
    id: str
    name: str
    description_md: str
    source_path: str | None = None
    created_at: str = ""


@pytest.fixture
def store():
    return NativeInMemoryStore()


def _make_skill(name: str = "godot-helper") -> Skill:
    return Skill(
        id="skill-1",
        name=name,
        description="Ajuda com projetos Godot 4.7",
        source="https://example.com/skill.git",
        path="/tmp/skill",
        installed_at="2026-08-08T00:00:00Z",
        installed_by="user-1",
    )


class TestSearchUnifiedMemoryHappyPath:
    """Query que bate nos três tipos retorna os três simultaneamente."""

    async def test_returns_fact_skill_and_bucket(self, store, monkeypatch):
        await store.aput(
            ("user", "user-1", "memories"),
            "godot-fact",
            {"content": "Godot usa GDScript por padrão", "metadata": {}},
        )
        monkeypatch.setattr(
            "backend.services.agent_factory.get_store", AsyncMock(return_value=store)
        )
        monkeypatch.setattr(
            "backend.workspace.skills.list_skills",
            lambda user_id: [_make_skill("godot-helper")],
        )
        monkeypatch.setattr(
            "backend.services.rag_buckets.list_buckets",
            lambda rs, workspace_id: [
                _FakeBucket(
                    id="bucket-1",
                    name="Godot Docs",
                    description_md="Documentação oficial do Godot 4.7",
                )
            ],
        )

        hits = await search_unified_memory(
            "godot", user_id="user-1", workspace_id="ws-1"
        )

        types = {h.type for h in hits}
        assert types == {"fact", "skill", "rag_bucket"}
        assert any(h.id == "godot-fact" for h in hits)
        assert any(h.id == "skill-1" for h in hits)
        assert any(h.id == "bucket-1" for h in hits)


class TestSearchUnifiedMemoryEmptyUser:
    """Usuário sem nenhuma memória de nenhum tipo não lança, devolve []."""

    async def test_empty_user_returns_empty_list(self, store, monkeypatch):
        monkeypatch.setattr(
            "backend.services.agent_factory.get_store", AsyncMock(return_value=store)
        )
        monkeypatch.setattr("backend.workspace.skills.list_skills", lambda user_id: [])
        monkeypatch.setattr(
            "backend.services.rag_buckets.list_buckets",
            lambda rs, workspace_id: [],
        )

        hits = await search_unified_memory(
            "qualquer coisa", user_id="user-sem-nada", workspace_id="ws-1"
        )

        assert hits == []

    async def test_no_workspace_id_skips_bucket_search_without_raising(
        self, store, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.services.agent_factory.get_store", AsyncMock(return_value=store)
        )
        monkeypatch.setattr("backend.workspace.skills.list_skills", lambda user_id: [])

        hits = await search_unified_memory(
            "qualquer coisa", user_id="user-1", workspace_id=None
        )

        assert hits == []


class TestSearchUnifiedMemoryIsolatesFailures:
    """Storage corrompido num tipo não derruba a busca dos outros dois."""

    async def test_corrupted_skills_index_does_not_break_facts_and_buckets(
        self, store, monkeypatch
    ):
        await store.aput(
            ("user", "user-1", "memories"),
            "surviving-fact",
            {"content": "fato sobrevive à falha de skills", "metadata": {}},
        )
        monkeypatch.setattr(
            "backend.services.agent_factory.get_store", AsyncMock(return_value=store)
        )

        def _raise(user_id: str) -> list[Skill]:
            raise RuntimeError("index.json corrompido")

        monkeypatch.setattr("backend.workspace.skills.list_skills", _raise)
        monkeypatch.setattr(
            "backend.services.rag_buckets.list_buckets",
            lambda rs, workspace_id: [
                _FakeBucket(
                    id="bucket-1", name="Bucket", description_md="fato de teste"
                )
            ],
        )

        hits = await search_unified_memory(
            "fato", user_id="user-1", workspace_id="ws-1"
        )

        types = {h.type for h in hits}
        assert "skill" not in types
        assert "fact" in types
        assert "rag_bucket" in types

    async def test_broken_store_does_not_break_skills_and_buckets(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.agent_factory.get_store",
            AsyncMock(side_effect=RuntimeError("store indisponível")),
        )
        monkeypatch.setattr(
            "backend.workspace.skills.list_skills",
            lambda user_id: [_make_skill()],
        )
        monkeypatch.setattr(
            "backend.services.rag_buckets.list_buckets",
            lambda rs, workspace_id: [],
        )

        hits = await search_unified_memory(
            "godot", user_id="user-1", workspace_id="ws-1"
        )

        types = {h.type for h in hits}
        assert "fact" not in types
        assert "skill" in types


class TestSearchUnifiedMemoryTypesFilter:
    """`types` restringe a busca a um subconjunto explícito."""

    async def test_types_filter_skips_untargeted_storages(self, store, monkeypatch):
        await store.aput(
            ("user", "user-1", "memories"),
            "a-fact",
            {"content": "conteúdo", "metadata": {}},
        )
        skills_called = False

        def _list_skills(user_id: str) -> list[Skill]:
            nonlocal skills_called
            skills_called = True
            return [_make_skill()]

        monkeypatch.setattr(
            "backend.services.agent_factory.get_store", AsyncMock(return_value=store)
        )
        monkeypatch.setattr("backend.workspace.skills.list_skills", _list_skills)
        monkeypatch.setattr(
            "backend.services.rag_buckets.list_buckets",
            lambda rs, workspace_id: [],
        )

        hits = await search_unified_memory(
            "conteúdo",
            user_id="user-1",
            workspace_id="ws-1",
            types=frozenset({"fact"}),
        )

        assert all(h.type == "fact" for h in hits)
        assert skills_called is False


class TestSearchFactsSubstringFallback:
    """Sem embedding configurado, `asearch` ignora `query` — `_search_facts`
    precisa filtrar por substring pra não ficar inconsistente com
    `_search_skills`/`_search_rag_buckets` (que sempre filtram)."""

    async def test_sem_score_aplica_filtro_substring(self, store, monkeypatch):
        monkeypatch.setattr(
            "backend.services.agent_factory.get_store", AsyncMock(return_value=store)
        )
        await store.aput(
            ("user", "user-1", "memories"),
            "f1",
            {"content": "gosta de python", "metadata": {}},
        )
        await store.aput(
            ("user", "user-1", "memories"),
            "f2",
            {"content": "trabalha com rust", "metadata": {}},
        )

        hits = await _search_facts("python", "user-1", limit=10)

        assert [h.id for h in hits] == ["f1"]

    async def test_com_score_nao_reaplica_filtro(self, monkeypatch):
        """Quando o store já devolveu ranking semântico (score presente),
        confiar nele — não filtrar de novo por substring, que poderia
        remover um resultado semanticamente relevante sem o termo literal."""

        class _ScoredItem:
            def __init__(self, key: str, value: dict, score: float) -> None:
                self.key = key
                self.value = value
                self.score = score

        class _FakeScoredStore:
            async def asearch(self, ns, *, query, limit):
                return [
                    _ScoredItem("f1", {"content": "prefere respostas objetivas"}, 0.9)
                ]

        monkeypatch.setattr(
            "backend.services.agent_factory.get_store",
            AsyncMock(return_value=_FakeScoredStore()),
        )

        hits = await _search_facts("conciso", "user-1", limit=10)

        assert [h.id for h in hits] == ["f1"]

    async def test_query_vazia_nao_filtra(self, store, monkeypatch):
        monkeypatch.setattr(
            "backend.services.agent_factory.get_store", AsyncMock(return_value=store)
        )
        await store.aput(
            ("user", "user-1", "memories"),
            "f1",
            {"content": "fato um", "metadata": {}},
        )
        await store.aput(
            ("user", "user-1", "memories"),
            "f2",
            {"content": "fato dois", "metadata": {}},
        )

        hits = await _search_facts("", "user-1", limit=10)

        assert {h.id for h in hits} == {"f1", "f2"}
