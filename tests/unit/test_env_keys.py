"""Tests for backend/services/env_keys.py.

Fonte única de "aplicar key de LLM em runtime" — usada por PATCH
/admin/api-keys e POST /envs. Cobre o bug real: uma key setada por
qualquer um dos dois caminhos precisa valer na PRÓXIMA chamada ao
provider (os.environ + settings), não só ficar persistida em disco/banco.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.services import env_keys


class TestKnownLlmEnvKeys:
    def test_covers_all_expected_providers(self):
        assert {
            "GOOGLE_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "COHERE_API_KEY",
            "TAVILY_API_KEY",
            "OPENROUTER_API_KEY",
        } == env_keys.KNOWN_LLM_ENV_KEYS

    def test_github_token_not_included(self):
        """Erro/borda: tokens de integração (OAuth) não são keys de LLM —
        não devem ser tratados por esse caminho."""
        assert "GITHUB_TOKEN" not in env_keys.KNOWN_LLM_ENV_KEYS


class TestDefaultEnvFile:
    def test_returns_vectora_env_path(self, tmp_path, monkeypatch):
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path / ".vectora")
        p = env_keys.default_env_file()
        assert p == tmp_path / ".vectora" / ".env"
        assert p.parent.exists()  # mkdir(parents=True, exist_ok=True)


@pytest.fixture
def _restore_settings():
    """`apply_llm_env_key` muta `settings` (singleton global) em runtime por
    design — restaura os atributos tocados nos testes pra não vazar estado
    entre testes do mesmo processo pytest."""
    from backend.settings import settings

    original = {
        "google_api_key": settings.google_api_key,
        "tavily_api_key": settings.tavily_api_key,
        "cohere_api_key": settings.cohere_api_key,
    }
    yield settings
    for attr, value in original.items():
        object.__setattr__(settings, attr, value)


class TestApplyLlmEnvKey:
    def test_persists_env_and_updates_os_environ(
        self, tmp_path, monkeypatch, _restore_settings
    ):
        env_file = tmp_path / ".env"
        monkeypatch.setenv("GOOGLE_API_KEY", "placeholder")  # monkeypatch "adota" a key

        with patch("backend.cli.keys.upsert_env_key") as mock_upsert:
            env_keys.apply_llm_env_key(env_file, "GOOGLE_API_KEY", "AIza-nova-key")

        mock_upsert.assert_called_once_with(env_file, "GOOGLE_API_KEY", "AIza-nova-key")
        assert __import__("os").environ["GOOGLE_API_KEY"] == "AIza-nova-key"
        assert _restore_settings.google_api_key == "AIza-nova-key"

    def test_empty_value_clears_key(self, tmp_path, monkeypatch, _restore_settings):
        """Erro/borda: valor vazio 'esquece' a key — mesmo comportamento
        de patch_api_keys (não é um erro, é uma limpeza intencional)."""
        import os

        env_file = tmp_path / ".env"
        monkeypatch.setenv("TAVILY_API_KEY", "old-value")

        with patch("backend.cli.keys.upsert_env_key"):
            env_keys.apply_llm_env_key(env_file, "TAVILY_API_KEY", "")

        assert os.environ["TAVILY_API_KEY"] == ""
        assert _restore_settings.tavily_api_key is None

    def test_whitespace_only_value_is_stripped(self, tmp_path, monkeypatch):
        import os

        env_file = tmp_path / ".env"
        monkeypatch.setenv("COHERE_API_KEY", "placeholder")
        with patch("backend.cli.keys.upsert_env_key") as mock_upsert:
            env_keys.apply_llm_env_key(env_file, "COHERE_API_KEY", "   ")

        mock_upsert.assert_called_once_with(env_file, "COHERE_API_KEY", "")
        assert os.environ["COHERE_API_KEY"] == ""

    def test_unknown_settings_attr_does_not_raise(self, tmp_path, monkeypatch):
        """Erro/borda: env var sem atributo correspondente em `settings`
        (hasattr False) não deve quebrar — só não atualiza settings."""
        env_file = tmp_path / ".env"
        monkeypatch.setenv("NOT_A_REAL_ENV_VAR", "placeholder")
        with patch("backend.cli.keys.upsert_env_key"):
            # Não lança mesmo que "settings.not_a_real_field" não exista.
            env_keys.apply_llm_env_key(env_file, "NOT_A_REAL_ENV_VAR", "x")
        assert __import__("os").environ["NOT_A_REAL_ENV_VAR"] == "x"
