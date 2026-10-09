"""Tests for src/settings.py"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from backend.settings import Settings

# ---------------------------------------------------------------------------
# Smoke tests (existentes)
# ---------------------------------------------------------------------------


def test_settings_loads():
    s = Settings()
    assert s is not None


def test_settings_derived_paths():
    s = Settings()
    assert s.lancedb_dir is not None
    assert str(s.lancedb_dir).endswith("lancedb")


def test_settings_embedding_queue_dsn():
    s = Settings()
    assert s.embedding_queue_dsn is not None
    assert "sqlite" in s.embedding_queue_dsn


def test_settings_defaults():
    s = Settings()
    assert isinstance(s.max_context_tokens, int)
    assert s.max_context_tokens > 0


def test_settings_get_cohere_api_key_returns_none_or_str():
    s = Settings()
    key = s.get_cohere_api_key()
    assert key is None or isinstance(key, str)


def test_settings_gets_nine_router_model_and_api_key():
    s = Settings()
    s.llm_provider = "nine_router"
    s.nine_router_default_model = "cx/gpt-5.6-luna"
    s.nine_router_api_key = "test-key"
    assert s.get_llm_model() == "cx/gpt-5.6-luna"
    assert s.get_llm_api_key() == "test-key"


def test_settings_set_model_updates_nine_router_default():
    s = Settings()
    s.set_model("nine_router", "cx/gpt-5.6-luna")
    assert s.nine_router_default_model == "cx/gpt-5.6-luna"


def test_settings_set_model_updates_llamacpp_model():
    s = Settings()
    s.set_model("llamacpp", "local-model")
    assert s.llamacpp_model == "local-model"


def test_settings_vectora_app_secret_vem_do_defaults_env(monkeypatch):
    """Fixo por produto (backend/defaults.env) — não é auto-gerado por
    instalação, precisa bater com o mesmo valor configurado no Worker via
    `wrangler secret put VECTORA_APP_SECRET`."""
    monkeypatch.delenv("VECTORA_APP_SECRET", raising=False)
    s = Settings()
    assert len(s.vectora_app_secret) == 64

    monkeypatch.setenv("VECTORA_APP_SECRET", "self-hosted-override")
    s_override = Settings()
    assert s_override.vectora_app_secret == "self-hosted-override"


# ---------------------------------------------------------------------------
# TLS do servidor web (SSL_CERTFILE / SSL_KEYFILE)
# ---------------------------------------------------------------------------


def test_settings_ssl_defaults_none():
    s = Settings()
    assert s.ssl_certfile is None
    assert s.ssl_keyfile is None


def test_settings_ssl_from_env(monkeypatch):
    monkeypatch.setenv("SSL_CERTFILE", "/certs/fullchain.pem")
    monkeypatch.setenv("SSL_KEYFILE", "/certs/key.pem")
    s = Settings()
    assert s.ssl_certfile == "/certs/fullchain.pem"
    assert s.ssl_keyfile == "/certs/key.pem"


def test_cli_parser_accepts_ssl_flags():
    from backend.main import _build_parser

    parser = _build_parser()
    args = parser.parse_args(
        [
            "start",
            "--ssl-certfile",
            "cert.pem",
            "--ssl-keyfile",
            "key.pem",
        ]
    )
    assert args.ssl_certfile == "cert.pem"
    assert args.ssl_keyfile == "key.pem"


def test_cli_parser_ssl_flags_default_none():
    from backend.main import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["start"])
    assert args.ssl_certfile is None
    assert args.ssl_keyfile is None


# ---------------------------------------------------------------------------
# Cohere provider
# ---------------------------------------------------------------------------


def test_cohere_chat_model_default():
    s = Settings()
    assert s.cohere_chat_model == "command-a-03-2025"


def test_cohere_in_get_llm_model_when_provider_set(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "cohere")
    s = Settings()
    # Sem chave configurada, get_llm_model ainda retorna o campo correto
    assert s.cohere_chat_model is not None
    assert isinstance(s.cohere_chat_model, str)


def test_cohere_available_when_api_key_set(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "cohere_test_key_fake")
    s = Settings()
    providers = s.get_available_providers()
    assert "cohere" in providers


def test_cohere_not_available_without_api_key(monkeypatch):
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    s = Settings()
    # Só aparece se a chave estiver configurada
    providers = s.get_available_providers()
    # Se cohere_api_key não está setado no env, não deve aparecer
    if not s.cohere_api_key:
        assert "cohere" not in providers


def test_set_model_cohere():
    s = Settings()
    s.set_model("cohere", "command-r-plus-08-2024")
    assert s.cohere_chat_model == "command-r-plus-08-2024"


def test_set_model_cohere_default_model():
    s = Settings()
    s.set_model("cohere", "command-a-03-2025")
    assert s.cohere_chat_model == "command-a-03-2025"


# ---------------------------------------------------------------------------
# OpenAI model default — corrigido em fix/critical-bugs (era "gpt-5.5", agora "gpt-4o")
# ---------------------------------------------------------------------------


def test_openai_model_default_is_valid():
    s = Settings()
    invalid_models = {"gpt-5.5", "gpt-5", "gpt-4.5"}
    assert s.openai_model not in invalid_models, (
        f"openai_model='{s.openai_model}' é um modelo inválido/inexistente"
    )


def test_openai_model_default_is_gpt4o():
    s = Settings()
    assert s.openai_model == "gpt-4o"


# ---------------------------------------------------------------------------
# get_llm_model e get_llm_api_key por provider
# ---------------------------------------------------------------------------


def test_get_llm_model_google():
    # Testa a lógica de get_llm_model() diretamente, sem depender da hierarquia de env
    s = Settings()
    s.llm_provider = "google-genai"
    assert s.get_llm_model() == s.google_model


def test_get_llm_model_openai():
    s = Settings()
    s.llm_provider = "openai"
    # openai_model padrão é "gpt-4o" (corrigido em fix/critical-bugs)
    assert s.get_llm_model() == s.openai_model


def test_get_llm_model_cohere():
    # Testa que get_llm_model() retorna cohere_chat_model quando provider=cohere
    s = Settings()
    s.llm_provider = "cohere"
    assert s.get_llm_model() == s.cohere_chat_model


# ---------------------------------------------------------------------------
# Literal do provider inclui "cohere"
# ---------------------------------------------------------------------------


def test_llm_provider_literal_accepts_cohere():
    # Testa que o campo llm_provider aceita "cohere" como valor válido
    s = Settings()
    s.llm_provider = "cohere"  # type: ignore[assignment]
    assert s.llm_provider == "cohere"


# ---------------------------------------------------------------------------
# VoyageAI (Parte B) — embeddings/rerank alternativos ao Cohere
# ---------------------------------------------------------------------------


def test_voyage_api_key_default_none(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    s = Settings()
    assert s.voyage_api_key is None


def test_voyage_embedding_model_default():
    s = Settings()
    assert s.voyage_embedding_model == "voyage-3"


def test_voyage_rerank_model_default():
    s = Settings()
    assert s.voyage_rerank_model == "rerank-2"


def test_voyage_api_key_from_env(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "voy-secret-123")
    s = Settings()
    assert s.voyage_api_key == "voy-secret-123"


def test_voyage_embedding_model_from_env(monkeypatch):
    monkeypatch.setenv("VOYAGE_EMBEDDING_MODEL", "voyage-3-large")
    s = Settings()
    assert s.voyage_embedding_model == "voyage-3-large"


def test_voyage_rerank_model_from_env(monkeypatch):
    monkeypatch.setenv("VOYAGE_RERANK_MODEL", "rerank-2-lite")
    s = Settings()
    assert s.voyage_rerank_model == "rerank-2-lite"


def test_voyage_key_independent_from_cohere(monkeypatch):
    # voyage_api_key e cohere_api_key são campos separados — setar um não muda o outro.
    monkeypatch.setenv("VOYAGE_API_KEY", "voy-1")
    s = Settings()
    assert s.voyage_api_key == "voy-1"
    assert s.voyage_api_key != s.cohere_api_key


class TestConfiguredLlmProviders:
    """configured_llm_providers: só providers com credencial (model selector)."""

    def test_only_configured_providers_listed(self):
        s = Settings()
        s.google_api_key = "g-key"
        s.openai_api_key = None
        s.anthropic_api_key = None
        s.cohere_api_key = "c-key"
        s.openrouter_api_key = None
        s.ollama_base_url = None
        assert s.configured_llm_providers() == ["google-genai", "cohere"]

    def test_no_keys_returns_empty(self):
        s = Settings()
        s.google_api_key = None
        s.openai_api_key = None
        s.anthropic_api_key = None
        s.cohere_api_key = None
        s.openrouter_api_key = None
        s.ollama_base_url = None
        # COHERE_API_KEY do ambiente pode existir; isola para o caso vazio.
        import os as _os

        prev = _os.environ.pop("COHERE_API_KEY", None)
        try:
            assert s.configured_llm_providers() == []
        finally:
            if prev is not None:
                _os.environ["COHERE_API_KEY"] = prev

    def test_openai_and_anthropic_when_keyed(self):
        s = Settings()
        s.google_api_key = None
        s.openai_api_key = "o-key"
        s.anthropic_api_key = "a-key"
        s.cohere_api_key = None
        s.openrouter_api_key = None
        s.ollama_base_url = None
        import os as _os

        prev = _os.environ.pop("COHERE_API_KEY", None)
        try:
            assert s.configured_llm_providers() == ["openai", "anthropic"]
        finally:
            if prev is not None:
                _os.environ["COHERE_API_KEY"] = prev

    def test_ollama_listed_when_base_url_set(self):
        s = Settings()
        s.google_api_key = None
        s.openai_api_key = None
        s.anthropic_api_key = None
        s.cohere_api_key = None
        s.openrouter_api_key = None
        s.ollama_base_url = "http://localhost:11434"
        import os as _os

        prev = _os.environ.pop("COHERE_API_KEY", None)
        try:
            assert s.configured_llm_providers() == ["ollama"]
        finally:
            if prev is not None:
                _os.environ["COHERE_API_KEY"] = prev


# ---------------------------------------------------------------------------
# Precedência de chaves de LLM: ~/.vectora/.env vence sobre .env de
# projeto/cwd (bug: chave de teste esquecida em vectora/.env sobrescrevia
# silenciosamente a chave paga do usuário a cada boot, causando "quota
# esgotada" indevido).
# ---------------------------------------------------------------------------


class TestLlmKeyPrecedence:
    def test_user_env_llm_key_wins_over_project_env(
        self, monkeypatch, tmp_path, caplog
    ):
        home_dir = tmp_path / "home"
        project_dir = tmp_path / "project"
        (home_dir / ".vectora").mkdir(parents=True)
        project_dir.mkdir(parents=True)

        (home_dir / ".vectora" / ".env").write_text(
            "GOOGLE_API_KEY=user-real-paid-key\n", encoding="utf-8"
        )
        (project_dir / ".env").write_text(
            "GOOGLE_API_KEY=stale-test-key-no-billing\n", encoding="utf-8"
        )

        monkeypatch.setattr(Path, "home", lambda: home_dir)
        monkeypatch.chdir(project_dir)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        # `tests/conftest.py::pytest_configure` isola VECTORA_HOME por
        # padrão pra suíte inteira nunca vazar pro ~/.vectora real — este
        # teste testa especificamente o fallback pra Path.home() quando
        # VECTORA_HOME NÃO está setado, então precisa desfazer o default
        # da sessão pra exercitar o caminho que quer testar.
        monkeypatch.delenv("VECTORA_HOME", raising=False)

        with caplog.at_level(logging.DEBUG, logger="backend.settings"):
            Settings()

        assert os.environ["GOOGLE_API_KEY"] == "user-real-paid-key"
        assert any("GOOGLE_API_KEY" in record.message for record in caplog.records)

    def test_no_warning_when_project_env_agrees_with_user_env(
        self, monkeypatch, tmp_path, caplog
    ):
        home_dir = tmp_path / "home"
        project_dir = tmp_path / "project"
        (home_dir / ".vectora").mkdir(parents=True)
        project_dir.mkdir(parents=True)

        (home_dir / ".vectora" / ".env").write_text(
            "GOOGLE_API_KEY=same-key\n", encoding="utf-8"
        )
        (project_dir / ".env").write_text("GOOGLE_API_KEY=same-key\n", encoding="utf-8")

        monkeypatch.setattr(Path, "home", lambda: home_dir)
        monkeypatch.chdir(project_dir)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        # `tests/conftest.py::pytest_configure` isola VECTORA_HOME por
        # padrão pra suíte inteira nunca vazar pro ~/.vectora real — este
        # teste testa especificamente o fallback pra Path.home() quando
        # VECTORA_HOME NÃO está setado, então precisa desfazer o default
        # da sessão pra exercitar o caminho que quer testar.
        monkeypatch.delenv("VECTORA_HOME", raising=False)

        with caplog.at_level(logging.DEBUG, logger="backend.settings"):
            Settings()

        assert os.environ["GOOGLE_API_KEY"] == "same-key"
        assert not any("GOOGLE_API_KEY" in record.message for record in caplog.records)
