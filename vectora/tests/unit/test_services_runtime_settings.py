"""Tests for backend/workspace/runtime_settings.py (backing SQLite, não JSON)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from backend.workspace.runtime_settings import RuntimeSettings


def test_apply_model_change_updates_nine_router_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.settings import settings
    from backend.workspace import runtime_settings as runtime_module

    monkeypatch.setattr(
        runtime_module.runtime_settings,
        "set_active_model",
        lambda _provider, _model: None,
    )
    monkeypatch.setattr(
        type(settings), "set_model", lambda _self, _provider, _model: None
    )
    runtime_module.apply_model_change("nine_router", "cx/gpt-5.6-luna")

    assert os.environ["LLM_PROVIDER"] == "nine_router"
    assert os.environ["NINE_ROUTER_MODEL"] == "cx/gpt-5.6-luna"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_settings_path(tmp_path: Path) -> Path:
    """Retorna um caminho temporário pro SQLite de settings (ainda não existe)."""
    return tmp_path / "checkpoints.db"


@pytest.fixture
def rs(tmp_settings_path: Path) -> RuntimeSettings:
    """RuntimeSettings apontando para arquivo temporário."""
    return RuntimeSettings(path=tmp_settings_path)


# ---------------------------------------------------------------------------
# Defaults sem arquivo
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_no_file_uses_defaults(self, tmp_settings_path: Path) -> None:
        """Provider/modelo NUNCA têm default inventado — string vazia até o
        usuário escolher explicitamente (`set_active_model`); só
        theme/language (cosméticos) têm default de fábrica."""
        assert not tmp_settings_path.exists()
        rs = RuntimeSettings(path=tmp_settings_path)
        assert rs.active_provider == ""
        assert rs.active_model == ""
        assert rs.theme == "dark"

    def test_get_unknown_key_returns_default(self, rs: RuntimeSettings) -> None:
        assert rs.get("chave_inexistente", "fallback") == "fallback"

    def test_get_unknown_key_no_default_returns_none(self, rs: RuntimeSettings) -> None:
        assert rs.get("chave_inexistente") is None


# ---------------------------------------------------------------------------
# Persistência: salvar e recarregar
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_set_creates_file(
        self, rs: RuntimeSettings, tmp_settings_path: Path
    ) -> None:
        rs.set("test_key", "test_value")
        assert tmp_settings_path.exists()

    def test_save_and_reload(self, tmp_settings_path: Path) -> None:
        rs1 = RuntimeSettings(path=tmp_settings_path)
        rs1.set("my_key", "my_value")

        rs2 = RuntimeSettings(path=tmp_settings_path)
        assert rs2.get("my_key") == "my_value"

    def test_multiple_keys_persisted(self, tmp_settings_path: Path) -> None:
        rs = RuntimeSettings(path=tmp_settings_path)
        rs.set("k1", "v1")
        rs.set("k2", 42)

        rs2 = RuntimeSettings(path=tmp_settings_path)
        assert rs2.get("k1") == "v1"
        assert rs2.get("k2") == 42

    def test_overwrite_key(self, rs: RuntimeSettings) -> None:
        rs.set("key", "first")
        rs.set("key", "second")
        assert rs.get("key") == "second"

    def test_commit_falho_faz_rollback_e_nao_vaza_cache_ou_dado_parcial(
        self, tmp_settings_path: Path
    ) -> None:
        """Uma falha real de commit não deixa escrita parcial nem cache sujo."""
        rs = RuntimeSettings(path=tmp_settings_path)
        rs.set("persistido_antes", "ok")
        connection = rs._conn

        class CommitFailureOnce:
            def __init__(self, delegate: sqlite3.Connection) -> None:
                self._delegate = delegate
                self._failed = False

            def execute(self, sql: str, parameters: tuple[object, ...] = ()):
                # O wrapper é um dublê local apenas para injetar a falha de commit.
                return self._delegate.execute(sql, cast("tuple", parameters))

            def commit(self) -> None:
                if not self._failed:
                    self._failed = True
                    raise sqlite3.OperationalError("commit simulado")
                self._delegate.commit()

            def rollback(self) -> None:
                self._delegate.rollback()

        rs._conn = cast("sqlite3.Connection", CommitFailureOnce(connection))
        with pytest.raises(sqlite3.OperationalError):
            rs.set("persistido_depois", "nao deve persistir")
        assert rs.get("persistido_depois") is None

        rs.set("persistido_depois", "somente esta deve persistir")
        recarregado = RuntimeSettings(path=tmp_settings_path)
        assert recarregado.get("persistido_antes") == "ok"
        assert recarregado.get("persistido_depois") == "somente esta deve persistir"


# ---------------------------------------------------------------------------
# set_active_model
# ---------------------------------------------------------------------------


class TestSetActiveModel:
    def test_set_active_model_updates_provider(self, rs: RuntimeSettings) -> None:
        rs.set_active_model("openai", "gpt-4o")
        assert rs.active_provider == "openai"

    def test_set_active_model_updates_model(self, rs: RuntimeSettings) -> None:
        rs.set_active_model("anthropic", "claude-sonnet-4-5")
        assert rs.active_model == "claude-sonnet-4-5"

    def test_set_active_model_cohere(self, rs: RuntimeSettings) -> None:
        rs.set_active_model("cohere", "command-a-03-2025")
        assert rs.active_provider == "cohere"
        assert rs.active_model == "command-a-03-2025"

    def test_set_active_model_persists(self, tmp_settings_path: Path) -> None:
        rs1 = RuntimeSettings(path=tmp_settings_path)
        rs1.set_active_model("openai", "gpt-4o")

        rs2 = RuntimeSettings(path=tmp_settings_path)
        assert rs2.active_provider == "openai"
        assert rs2.active_model == "gpt-4o"

    def test_has_distingue_nunca_configurado_de_configurado(
        self, rs: RuntimeSettings
    ) -> None:
        """Erro/borda: `has()` é a forma correta de checar se o usuário
        configurou algo — `active_provider`/`active_model` sozinhos não
        distinguem "nunca configurado" de "configurado com valor vazio"."""
        assert rs.has("active_provider") is False
        assert rs.has("active_model") is False

        rs.set_active_model("openai", "gpt-4o")

        assert rs.has("active_provider") is True
        assert rs.has("active_model") is True


# ---------------------------------------------------------------------------
# Tolerância a falhas
# ---------------------------------------------------------------------------


class TestFaultTolerance:
    def test_arquivo_nao_sqlite_no_caminho_falha_alto_e_claro(
        self, tmp_settings_path: Path
    ) -> None:
        """Um arquivo que não é SQLite de verdade no caminho de checkpoints.db
        é o mesmo cenário de corrupção que já afeta users/auth nesse arquivo
        (rbac/auth.py::_get_db() também não se recupera disso) — falha alto e
        claro em vez de mascarar com defaults, não degradação silenciosa."""
        tmp_settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_settings_path.write_text("isto nao e um banco sqlite", encoding="utf-8")

        with pytest.raises(sqlite3.DatabaseError):
            RuntimeSettings(path=tmp_settings_path)

    def test_empty_file_falls_back_to_defaults(self, tmp_settings_path: Path) -> None:
        """SQLite trata um arquivo de 0 bytes como banco novo válido —
        provider/modelo continuam vazios (nenhum default inventado)."""
        tmp_settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_settings_path.write_text("", encoding="utf-8")

        rs = RuntimeSettings(path=tmp_settings_path)
        assert rs.active_provider == ""

    def test_partial_state_uses_defaults_for_missing_keys(
        self, tmp_settings_path: Path
    ) -> None:
        """Provider escolhido sem o modelo correspondente não deve inventar
        um modelo — a chave que falta continua vazia, nunca um valor de
        outro provider/fábrica."""
        rs1 = RuntimeSettings(path=tmp_settings_path)
        rs1.set("active_provider", "openai")

        rs2 = RuntimeSettings(path=tmp_settings_path)
        assert rs2.active_provider == "openai"
        assert rs2.active_model == ""


# ---------------------------------------------------------------------------
# fallback_order (Parte A3)
# ---------------------------------------------------------------------------


class TestFallbackOrder:
    def test_default_empty(self, rs: RuntimeSettings) -> None:
        assert rs.fallback_order == []

    def test_set_and_get_roundtrip(self, rs: RuntimeSettings) -> None:
        rs.set_fallback_order(["openai:gpt-4o", "google-genai:gemini-2.5-flash"])
        assert rs.fallback_order == [
            "openai:gpt-4o",
            "google-genai:gemini-2.5-flash",
        ]

    def test_preserves_order(self, rs: RuntimeSettings) -> None:
        rs.set_fallback_order(["cohere:command-a", "openai:gpt-4o"])
        assert rs.fallback_order == ["cohere:command-a", "openai:gpt-4o"]

    def test_filters_empty_and_blank(self, rs: RuntimeSettings) -> None:
        rs.set_fallback_order(["openai:gpt-4o", "", "   ", "cohere:command-a"])
        assert rs.fallback_order == ["openai:gpt-4o", "cohere:command-a"]

    def test_trims_whitespace(self, rs: RuntimeSettings) -> None:
        rs.set_fallback_order(["  openai:gpt-4o  "])
        assert rs.fallback_order == ["openai:gpt-4o"]

    def test_set_empty_clears(self, rs: RuntimeSettings) -> None:
        rs.set_fallback_order(["openai:gpt-4o"])
        rs.set_fallback_order([])
        assert rs.fallback_order == []

    def test_persists_to_disk(self, tmp_settings_path: Path) -> None:
        rs = RuntimeSettings(path=tmp_settings_path)
        rs.set_fallback_order(["openai:gpt-4o"])
        rs2 = RuntimeSettings(path=tmp_settings_path)
        assert rs2.fallback_order == ["openai:gpt-4o"]

    def test_invalid_type_in_store_returns_empty(self, rs: RuntimeSettings) -> None:
        rs.set("llm_fallback_order", "not-a-list")
        assert rs.fallback_order == []

    def test_overwrite_replaces(self, rs: RuntimeSettings) -> None:
        rs.set_fallback_order(["openai:gpt-4o", "cohere:command-a"])
        rs.set_fallback_order(["google-genai:gemini-2.5-flash"])
        assert rs.fallback_order == ["google-genai:gemini-2.5-flash"]

    def test_entries_are_strings(self, rs: RuntimeSettings) -> None:
        rs.set_fallback_order(["openai:gpt-4o"])
        assert all(isinstance(x, str) for x in rs.fallback_order)


# ---------------------------------------------------------------------------
# reload()
# ---------------------------------------------------------------------------


class TestReload:
    def test_reload_picks_up_external_changes(self, tmp_settings_path: Path) -> None:
        rs = RuntimeSettings(path=tmp_settings_path)
        rs.set("key", "original")

        # Modifica o SQLite por uma conexão externa (simula outro processo).
        with sqlite3.connect(str(tmp_settings_path)) as conn:
            conn.execute(
                "UPDATE app_settings SET value = ? WHERE key = ?",
                ('"updated"', "key"),
            )
            conn.commit()

        # Antes do reload — valor antigo em memória (cache não invalidado)
        assert rs.get("key") == "original"

        rs.reload()
        assert rs.get("key") == "updated"


class TestRagSettings:
    """rag_settings: defaults + merge + persistência (aba de memória)."""

    def test_defaults(self, rs) -> None:
        s = rs.rag_settings
        assert s["reranker_enabled"] is True
        assert s["reranker_top_k"] == 5
        assert s["rerank_provider"] == "auto"
        assert s["embed_provider"] == "auto"
        assert s["ingest_file_types"] == []

    def test_set_merges_partial(self, rs) -> None:
        rs.set_rag_settings(reranker_enabled=False, reranker_top_k=12)
        s = rs.rag_settings
        assert s["reranker_enabled"] is False
        assert s["reranker_top_k"] == 12
        # Campos não informados mantêm o default.
        assert s["rerank_provider"] == "auto"

    def test_set_ignores_none(self, rs) -> None:
        rs.set_rag_settings(reranker_top_k=8)
        rs.set_rag_settings(reranker_top_k=None, rerank_provider="voyage")
        s = rs.rag_settings
        assert s["reranker_top_k"] == 8  # não sobrescrito por None
        assert s["rerank_provider"] == "voyage"

    def test_persists_across_reload(self, tmp_settings_path) -> None:
        from backend.workspace.runtime_settings import RuntimeSettings

        rs1 = RuntimeSettings(path=tmp_settings_path)
        rs1.set_rag_settings(reranker_top_k=20, ingest_file_types=["code"])
        rs2 = RuntimeSettings(path=tmp_settings_path)
        assert rs2.rag_settings["reranker_top_k"] == 20
        assert rs2.rag_settings["ingest_file_types"] == ["code"]


class TestFrontendPrefs:
    """frontend_prefs: preferências do web app sincronizadas com o backend."""

    def test_defaults_vazio(self, rs) -> None:
        assert rs.get_frontend_prefs("u1") == {}

    def test_set_merge_e_leitura(self, rs) -> None:
        rs.set_frontend_prefs("u1", {"selectedModel": "cohere:command-a-plus"})
        rs.set_frontend_prefs("u1", {"theme": "dark"})
        assert rs.get_frontend_prefs("u1") == {
            "selectedModel": "cohere:command-a-plus",
            "theme": "dark",
        }

    def test_ignora_chaves_fora_da_whitelist(self, rs) -> None:
        rs.set_frontend_prefs("u1", {"theme": "dark", "chave_desconhecida": "x"})
        prefs = rs.get_frontend_prefs("u1")
        assert prefs == {"theme": "dark"}

    def test_isola_por_usuario(self, rs) -> None:
        rs.set_frontend_prefs("u1", {"theme": "dark"})
        rs.set_frontend_prefs("u2", {"theme": "light"})
        assert rs.get_frontend_prefs("u1") == {"theme": "dark"}
        assert rs.get_frontend_prefs("u2") == {"theme": "light"}

    def test_persiste_apos_reload(self, tmp_settings_path) -> None:
        from backend.workspace.runtime_settings import RuntimeSettings

        rs1 = RuntimeSettings(path=tmp_settings_path)
        rs1.set_frontend_prefs("u1", {"selectedModel": "google-genai:gemini-2.5-flash"})
        rs2 = RuntimeSettings(path=tmp_settings_path)
        assert rs2.get_frontend_prefs("u1") == {
            "selectedModel": "google-genai:gemini-2.5-flash"
        }

    def test_retorno_do_set_e_o_estado_mesclado_final(self, rs) -> None:
        rs.set_frontend_prefs("u1", {"theme": "dark"})
        result = rs.set_frontend_prefs("u1", {"language": "pt"})
        assert result == {"theme": "dark", "language": "pt"}


class TestAuthRequired:
    """auth_required — substitui VECTORA_AUTH_REQUIRED no .env (setup-local)."""

    def test_default_true(self, rs: RuntimeSettings) -> None:
        assert rs.auth_required is True

    def test_setter_persiste(self, rs: RuntimeSettings) -> None:
        rs.auth_required = False
        assert rs.auth_required is False

    def test_persiste_apos_reload(self, tmp_settings_path: Path) -> None:
        rs1 = RuntimeSettings(path=tmp_settings_path)
        rs1.auth_required = False
        rs2 = RuntimeSettings(path=tmp_settings_path)
        assert rs2.auth_required is False


class TestLocalUser:
    """Nome/empresa do usuário local — substitui ~/.vectora/local_user.json."""

    def test_defaults_vazio(self, rs: RuntimeSettings) -> None:
        assert rs.local_user_name == ""
        assert rs.local_user_company == ""

    def test_set_local_user_roundtrip(self, rs: RuntimeSettings) -> None:
        rs.set_local_user("Bruno", "Vectora")
        assert rs.local_user_name == "Bruno"
        assert rs.local_user_company == "Vectora"

    def test_company_vazia_e_valida(self, rs: RuntimeSettings) -> None:
        rs.set_local_user("Ada", "")
        assert rs.local_user_name == "Ada"
        assert rs.local_user_company == ""

    def test_persiste_apos_reload(self, tmp_settings_path: Path) -> None:
        rs1 = RuntimeSettings(path=tmp_settings_path)
        rs1.set_local_user("Bruno", "Vectora")
        rs2 = RuntimeSettings(path=tmp_settings_path)
        assert rs2.local_user_name == "Bruno"
        assert rs2.local_user_company == "Vectora"

    def test_local_username_default_vazio(self, rs: RuntimeSettings) -> None:
        assert rs.local_username == ""

    def test_set_local_user_com_username_roundtrip(self, rs: RuntimeSettings) -> None:
        rs.set_local_user("Bruno", "Vectora", username="brunosoares")
        assert rs.local_username == "brunosoares"

    def test_set_local_user_sem_username_nao_sobrescreve(
        self, rs: RuntimeSettings
    ) -> None:
        rs.set_local_user("Bruno", "Vectora", username="brunosoares")
        rs.set_local_user("Bruno", "Vectora Corp")
        assert rs.local_username == "brunosoares"

    def test_local_username_persiste_apos_reload(self, tmp_settings_path: Path) -> None:
        rs1 = RuntimeSettings(path=tmp_settings_path)
        rs1.set_local_user("Bruno", "Vectora", username="brunosoares")
        rs2 = RuntimeSettings(path=tmp_settings_path)
        assert rs2.local_username == "brunosoares"
