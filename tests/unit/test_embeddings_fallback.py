"""Fallback de embeddings Cohere↔Voyage por quota (Parte B).

Contrato:
- FallbackEmbeddings usa o primário; em quota error troca para o secundário e
  registra record_switch. Erro não-quota propaga sem troca.
- Cobre embed_query/embed_documents (sync) e aembed_query/aembed_documents (async).
- _build_lc_embeddings: preferência explícita (embedding_provider) vence se
  buildável; senão Cohere+Voyage → FallbackEmbeddings; só um → esse; nenhum
  dos dois → Ollama, depois OpenRouter (embeddings locais/gateway); nada
  configurado → None.
- _build_ollama_embeddings/_build_openrouter_embeddings: None sem modelo
  configurado (o modelo é o próprio gate — nunca assume um default).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.llm import provider_fallback as pf
from backend.llm.base import Embeddings
from backend.llm.fallback_embeddings import FallbackEmbeddings


class _FakeEmb(Embeddings):
    def __init__(self, *, tag: float, error: Exception | None = None) -> None:
        self._tag = tag
        self._error = error

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._error is not None:
            raise self._error
        return [[self._tag] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        if self._error is not None:
            raise self._error
        return [self._tag]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._error is not None:
            raise self._error
        return [[self._tag] for _ in texts]

    async def aembed_query(self, text: str) -> list[float]:
        if self._error is not None:
            raise self._error
        return [self._tag]


def _wrap(primary: Embeddings, secondary: Embeddings) -> FallbackEmbeddings:
    return FallbackEmbeddings(
        primary,
        secondary,
        primary_id="cohere:embed-v4",
        secondary_id="voyage:voyage-3",
    )


class TestFallbackEmbeddingsSync:
    def test_query_primary_ok_no_switch(self):
        pf.drain_switches()
        emb = _wrap(_FakeEmb(tag=1.0), _FakeEmb(tag=2.0))
        assert emb.embed_query("x") == [1.0]
        assert pf.drain_switches() == []

    def test_query_primary_quota_switches(self):
        pf.drain_switches()
        emb = _wrap(_FakeEmb(tag=1.0, error=Exception("429 quota")), _FakeEmb(tag=2.0))
        assert emb.embed_query("x") == [2.0]
        assert pf.drain_switches() == [
            {"from": "cohere:embed-v4", "to": "voyage:voyage-3"}
        ]

    def test_query_non_quota_reraises(self):
        emb = _wrap(_FakeEmb(tag=1.0, error=ValueError("boom")), _FakeEmb(tag=2.0))
        with pytest.raises(ValueError):
            emb.embed_query("x")

    def test_documents_quota_switches(self):
        emb = _wrap(_FakeEmb(tag=1.0, error=Exception("rate limit")), _FakeEmb(tag=2.0))
        assert emb.embed_documents(["a", "b"]) == [[2.0], [2.0]]

    def test_documents_both_fail_propagates(self):
        emb = _wrap(
            _FakeEmb(tag=1.0, error=Exception("429")),
            _FakeEmb(tag=2.0, error=Exception("boom secundário")),
        )
        with pytest.raises(Exception, match="secund"):
            emb.embed_documents(["a"])


class TestFallbackEmbeddingsAsync:
    async def test_aquery_primary_ok(self):
        pf.drain_switches()
        emb = _wrap(_FakeEmb(tag=1.0), _FakeEmb(tag=2.0))
        assert await emb.aembed_query("x") == [1.0]
        assert pf.drain_switches() == []

    async def test_aquery_quota_switches(self):
        pf.drain_switches()
        emb = _wrap(_FakeEmb(tag=1.0, error=Exception("429 quota")), _FakeEmb(tag=2.0))
        assert await emb.aembed_query("x") == [2.0]
        assert pf.drain_switches() == [
            {"from": "cohere:embed-v4", "to": "voyage:voyage-3"}
        ]

    async def test_adocuments_quota_switches(self):
        emb = _wrap(
            _FakeEmb(tag=1.0, error=Exception("RESOURCE_EXHAUSTED")), _FakeEmb(tag=2.0)
        )
        assert await emb.aembed_documents(["a"]) == [[2.0]]

    async def test_aquery_non_quota_reraises(self):
        emb = _wrap(_FakeEmb(tag=1.0, error=ValueError("boom")), _FakeEmb(tag=2.0))
        with pytest.raises(ValueError):
            await emb.aembed_query("x")


class TestBuildLcEmbeddings:
    def test_both_configured_returns_fallback(self):
        from backend.storage import factory

        with (
            patch.object(
                factory, "_build_cohere_embeddings", lambda: _FakeEmb(tag=1.0)
            ),
            patch.object(
                factory, "_build_voyage_embeddings", lambda: _FakeEmb(tag=2.0)
            ),
        ):
            emb = factory._build_lc_embeddings()
        assert isinstance(emb, FallbackEmbeddings)

    def test_only_cohere_returns_cohere(self):
        from backend.storage import factory

        cohere = _FakeEmb(tag=1.0)
        with (
            patch.object(factory, "_build_cohere_embeddings", lambda: cohere),
            patch.object(factory, "_build_voyage_embeddings", lambda: None),
        ):
            assert factory._build_lc_embeddings() is cohere

    def test_only_voyage_returns_voyage(self):
        from backend.storage import factory

        voyage = _FakeEmb(tag=2.0)
        with (
            patch.object(factory, "_build_cohere_embeddings", lambda: None),
            patch.object(factory, "_build_voyage_embeddings", lambda: voyage),
        ):
            assert factory._build_lc_embeddings() is voyage

    def test_none_configured_returns_none(self):
        from backend.storage import factory

        with (
            patch.object(factory, "_build_cohere_embeddings", lambda: None),
            patch.object(factory, "_build_voyage_embeddings", lambda: None),
            patch.object(factory, "_build_ollama_embeddings", lambda: None),
            patch.object(factory, "_build_openrouter_embeddings", lambda: None),
        ):
            assert factory._build_lc_embeddings() is None

    def test_neither_cohere_nor_voyage_falls_back_to_ollama(self):
        from backend.storage import factory

        ollama = _FakeEmb(tag=3.0)
        with (
            patch.object(factory, "_build_cohere_embeddings", lambda: None),
            patch.object(factory, "_build_voyage_embeddings", lambda: None),
            patch.object(factory, "_build_ollama_embeddings", lambda: ollama),
        ):
            assert factory._build_lc_embeddings() is ollama

    def test_neither_cohere_nor_voyage_nor_ollama_falls_back_to_openrouter(self):
        from backend.storage import factory

        openrouter = _FakeEmb(tag=4.0)
        with (
            patch.object(factory, "_build_cohere_embeddings", lambda: None),
            patch.object(factory, "_build_voyage_embeddings", lambda: None),
            patch.object(factory, "_build_ollama_embeddings", lambda: None),
            patch.object(factory, "_build_openrouter_embeddings", lambda: openrouter),
        ):
            assert factory._build_lc_embeddings() is openrouter

    def test_explicit_preference_wins_even_with_cohere_configured(self):
        from backend.settings import settings
        from backend.storage import factory

        ollama = _FakeEmb(tag=5.0)
        cohere = _FakeEmb(tag=1.0)
        with (
            patch.object(settings, "embedding_provider", "ollama"),
            patch.object(factory, "_build_cohere_embeddings", lambda: cohere),
            patch.object(
                factory, "_build_ollama_embeddings", lambda _model=None: ollama
            ),
        ):
            assert factory._build_lc_embeddings() is ollama

    def test_preference_without_credential_falls_back_to_default_chain(self):
        from backend.settings import settings
        from backend.storage import factory

        cohere = _FakeEmb(tag=1.0)
        with (
            patch.object(settings, "embedding_provider", "ollama"),
            patch.object(factory, "_build_ollama_embeddings", lambda _model=None: None),
            patch.object(factory, "_build_cohere_embeddings", lambda: cohere),
            patch.object(factory, "_build_voyage_embeddings", lambda: None),
        ):
            assert factory._build_lc_embeddings() is cohere


class TestBuildLcEmbeddingsRuntimePreference:
    """``rag_settings.embed_provider``/``embed_model`` (PATCH /rag/settings,
    seletor da aba de memória) precisam ser lidos por ``_build_lc_embeddings``
    — antes eram só persistidos, nunca consultados aqui (a escolha na UI não
    tinha efeito nenhum na seleção real de embeddings)."""

    def test_runtime_ollama_com_embed_model_vence_mesmo_com_cohere_configurado(
        self, tmp_path
    ):
        from backend.storage import factory
        from backend.workspace.runtime_settings import RuntimeSettings

        rs = RuntimeSettings(path=tmp_path / "settings.json")
        rs.set_rag_settings(embed_provider="ollama", embed_model="qwen3-embedding")
        ollama = _FakeEmb(tag=6.0)
        cohere = _FakeEmb(tag=1.0)

        captured: list[str | None] = []

        def _fake_ollama_builder(model: str | None = None) -> Embeddings:
            captured.append(model)
            return ollama

        with (
            patch("backend.workspace.runtime_settings.runtime_settings", rs),
            patch.object(factory, "_build_cohere_embeddings", lambda: cohere),
            patch.object(factory, "_build_ollama_embeddings", _fake_ollama_builder),
        ):
            assert factory._build_lc_embeddings() is ollama
        assert captured == ["qwen3-embedding"]

    def test_runtime_ollama_sem_embed_model_e_sem_default_cai_pro_fallback(
        self, tmp_path
    ):
        """Par de erro: usuário escolheu Ollama na UI mas não tem modelo
        configurado (nem via UI, nem via env) — não quebra, cai pra Cohere/
        Voyage se disponíveis (mesmo comportamento gracioso de sempre)."""
        from backend.storage import factory
        from backend.workspace.runtime_settings import RuntimeSettings

        rs = RuntimeSettings(path=tmp_path / "settings.json")
        rs.set_rag_settings(embed_provider="ollama")
        cohere = _FakeEmb(tag=1.0)

        with (
            patch("backend.workspace.runtime_settings.runtime_settings", rs),
            patch.object(factory, "_build_ollama_embeddings", lambda _model=None: None),
            patch.object(factory, "_build_cohere_embeddings", lambda: cohere),
            patch.object(factory, "_build_voyage_embeddings", lambda: None),
        ):
            assert factory._build_lc_embeddings() is cohere

    def test_runtime_auto_usa_settings_embedding_provider_estatico(self, tmp_path):
        """embed_provider="auto" (default) — comportamento antigo preservado:
        cai pro ``settings.embedding_provider`` (env var), não pro runtime."""
        from backend.settings import settings
        from backend.storage import factory
        from backend.workspace.runtime_settings import RuntimeSettings

        rs = RuntimeSettings(path=tmp_path / "settings.json")
        voyage = _FakeEmb(tag=2.0)

        with (
            patch("backend.workspace.runtime_settings.runtime_settings", rs),
            patch.object(settings, "embedding_provider", "voyage"),
            patch.object(factory, "_build_voyage_embeddings", lambda: voyage),
        ):
            assert factory._build_lc_embeddings() is voyage


class TestBuildOllamaOpenRouterEmbeddings:
    def test_ollama_without_model_returns_none(self):
        from backend.settings import settings
        from backend.storage import factory

        with patch.object(settings, "ollama_embedding_model", None):
            assert factory._build_ollama_embeddings() is None

    def test_ollama_with_model_builds_embeddings(self):
        """Cliente nativo (`/api/embed`)."""
        from backend.llm.ollama.embeddings import OllamaEmbeddings
        from backend.settings import settings
        from backend.storage import factory

        with (
            patch.object(settings, "ollama_embedding_model", "qwen3-embedding:0.6b"),
            patch.object(settings, "ollama_base_url", "http://127.0.0.1:11434"),
        ):
            emb = factory._build_ollama_embeddings()
        assert isinstance(emb, OllamaEmbeddings)
        assert emb.model == "qwen3-embedding:0.6b"

    def test_openrouter_without_key_returns_none(self):
        from backend.settings import settings
        from backend.storage import factory

        with (
            patch.object(settings, "openrouter_api_key", None),
            patch.object(
                settings, "openrouter_embedding_model", "qwen/qwen3-embedding-0.6b"
            ),
        ):
            assert factory._build_openrouter_embeddings() is None

    def test_openrouter_without_model_returns_none(self):
        from backend.settings import settings
        from backend.storage import factory

        with (
            patch.object(settings, "openrouter_api_key", "sk-or-v1-abc"),
            patch.object(settings, "openrouter_embedding_model", None),
        ):
            assert factory._build_openrouter_embeddings() is None

    def test_openrouter_with_key_and_model_builds_embeddings(self):
        """Cliente nativo — preserva `input_type` (modelo assimétrico
        precisa saber se é consulta ou documento) e `usage.cost`."""
        from backend.llm.openrouter.embeddings import OpenRouterEmbeddings
        from backend.settings import settings
        from backend.storage import factory

        with (
            patch.object(settings, "openrouter_api_key", "sk-or-v1-abc"),
            patch.object(
                settings, "openrouter_embedding_model", "qwen/qwen3-embedding-0.6b"
            ),
        ):
            emb = factory._build_openrouter_embeddings()
        assert isinstance(emb, OpenRouterEmbeddings)
        assert emb.model == "qwen/qwen3-embedding-0.6b"
