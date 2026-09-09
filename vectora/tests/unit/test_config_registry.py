"""Testes do schema declarativo de configuração (backend/config/) — registry,
adapters e o dispatcher de CLI (`vectora config <categoria>`).

Isolamento: nenhum teste toca `~/.vectora/.env` nem `~/.vectora/checkpoints.db`
reais — `EnvAdapter` mocka `backend.cli.keys.upsert_env_key`, `RuntimeSettingsAdapter`
usa um `RuntimeSettings` apontando pra `tmp_path`, `ConfigTomlAdapter` usa
`write_config_section` mockado.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.config import registry as registry_mod
from backend.config.adapters import (
    ConfigTomlAdapter,
    EnvAdapter,
    RuntimeSettingsAdapter,
)
from backend.config.registry import (
    DuplicateSettingFieldError,
    fields_for_category,
    get_field,
    get_value,
    set_value,
    setting_field,
)

# ---------------------------------------------------------------------------
# registry.py
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_registry():
    """Isola o registry global entre testes que registram campos ad hoc —
    sem isso, o segundo teste que registrar `"x"` colidiria com o primeiro."""
    snapshot = dict(registry_mod._REGISTRY)
    registry_mod._REGISTRY.clear()
    yield
    registry_mod._REGISTRY.clear()
    registry_mod._REGISTRY.update(snapshot)


class _NoopAdapter:
    def get(self, key: str) -> object:
        return None

    def set(self, key: str, value: object) -> None:
        pass


class TestSettingField:
    def test_registra_e_recupera(self, clean_registry):
        setting_field(
            "x",
            category="preferences",
            cli_flag="--x",
            description="d",
            adapter=_NoopAdapter(),
        )
        field = get_field("x")
        assert field is not None
        assert field.category == "preferences"
        assert field.cli_flag == "--x"

    def test_chave_duplicada_levanta_erro(self, clean_registry):
        setting_field(
            "y",
            category="preferences",
            cli_flag="--y",
            description="d",
            adapter=_NoopAdapter(),
        )
        with pytest.raises(DuplicateSettingFieldError):
            setting_field(
                "y",
                category="preferences",
                cli_flag="--y2",
                description="d2",
                adapter=_NoopAdapter(),
            )

    def test_chave_inexistente_retorna_none(self, clean_registry):
        assert get_field("nao-existe") is None

    def test_get_set_value_delegam_para_adapter(
        self: TestSettingField, clean_registry: None
    ) -> None:
        calls: list[tuple[str, object]] = []

        class _RecordingAdapter:
            def get(self, key: str) -> object:
                calls.append(("get", key))
                return "value"

            def set(self, key: str, value: object) -> None:
                calls.append(("set", value))

        adapter = _RecordingAdapter()
        setting_field(
            "delegated",
            category="preferences",
            cli_flag="--delegated",
            description="d",
            adapter=adapter,
        )
        set_value("delegated", "value")
        assert get_value("delegated") == "value"
        assert calls == [("set", "value"), ("get", "delegated")]
        with pytest.raises(KeyError):
            get_value("missing")

    def test_fields_for_category_filtra_corretamente(self, clean_registry):
        setting_field(
            "a",
            category="cat1",
            cli_flag="--a",
            description="d",
            adapter=_NoopAdapter(),
        )
        setting_field(
            "b",
            category="cat2",
            cli_flag="--b",
            description="d",
            adapter=_NoopAdapter(),
        )
        assert [f.key for f in fields_for_category("cat1")] == ["a"]
        assert [f.key for f in fields_for_category("cat2")] == ["b"]
        assert fields_for_category("cat-vazia") == []

    def test_get_set_delegam_pro_adapter(self, clean_registry):
        calls: list[tuple[str, object]] = []

        class _RecordingAdapter:
            def get(self, key: str) -> object:
                calls.append(("get", key))
                return "valor"

            def set(self, key: str, value: object) -> None:
                calls.append(("set", value))

        field = setting_field(
            "z",
            category="preferences",
            cli_flag="--z",
            description="d",
            adapter=_RecordingAdapter(),
        )
        assert field.get() == "valor"
        field.set("novo")
        assert calls == [("get", "z"), ("set", "novo")]


class TestFieldsRealmenteRegistrados:
    """Confirma que importar backend.config popula o registry com os campos
    reais definidos em fields.py — não é só uma API vazia."""

    def test_categorias_esperadas_existem(self):
        from backend.config import all_categories

        cats = all_categories()
        assert "integrations" in cats
        assert "connect" in cats
        assert "preferences" in cats

    def test_google_api_key_registrada(self):
        field = get_field("google_api_key")
        assert field is not None
        assert field.category == "integrations"
        assert field.secret is True


# ---------------------------------------------------------------------------
# adapters.py
# ---------------------------------------------------------------------------


@pytest.fixture
def _restore_settings():
    from backend.settings import settings

    original = {
        "google_api_key": settings.google_api_key,
        "default_model": getattr(settings, "default_model", None),
    }
    yield settings
    for attr, value in original.items():
        object.__setattr__(settings, attr, value)


class TestEnvAdapter:
    def test_set_delega_pra_apply_llm_env_key(
        self, tmp_path, monkeypatch, _restore_settings
    ):
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path / ".vectora")
        monkeypatch.setenv("GOOGLE_API_KEY", "placeholder")

        adapter = EnvAdapter("GOOGLE_API_KEY")
        with patch("backend.cli.keys.upsert_env_key") as mock_upsert:
            adapter.set("google_api_key", "AIza-nova")

        mock_upsert.assert_called_once()
        assert _restore_settings.google_api_key == "AIza-nova"

    def test_get_le_do_settings_singleton(self, monkeypatch, _restore_settings):
        """`EnvAdapter.get` lê a fonte canônica em runtime: `os.environ`
        (que `apply_llm_env_key` seta na escrita). `settings` espelha no
        boot, mas uma key gravada/limpa em runtime só reflete aqui."""
        from unittest.mock import patch

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "chave-atual"}, clear=False):
            adapter = EnvAdapter("GOOGLE_API_KEY")
            assert adapter.get("google_api_key") == "chave-atual"


class TestRuntimeSettingsAdapter:
    def test_get_set_isolado_por_tmp_path(self, tmp_path, monkeypatch):
        import backend.workspace.runtime_settings as rs_mod
        from backend.workspace.runtime_settings import RuntimeSettings

        isolated = RuntimeSettings(path=tmp_path / "checkpoints.db")
        monkeypatch.setattr(rs_mod, "runtime_settings", isolated)

        adapter = RuntimeSettingsAdapter()
        adapter.set("theme", "light")
        assert adapter.get("theme") == "light"
        # Confirma isolamento real (não é a instância global).
        assert isolated.get("theme") == "light"

    def test_settings_key_diferente_da_chave_publica(self, tmp_path, monkeypatch):
        import backend.workspace.runtime_settings as rs_mod
        from backend.workspace.runtime_settings import RuntimeSettings

        isolated = RuntimeSettings(path=tmp_path / "checkpoints.db")
        monkeypatch.setattr(rs_mod, "runtime_settings", isolated)

        adapter = RuntimeSettingsAdapter(settings_key="user_timezone")
        adapter.set("timezone", "America/Sao_Paulo")
        assert isolated.get("user_timezone") == "America/Sao_Paulo"
        assert adapter.get("timezone") == "America/Sao_Paulo"


class TestConfigTomlAdapter:
    def test_set_chama_write_config_section_e_atualiza_settings(
        self, monkeypatch, _restore_settings
    ):
        calls: list[tuple[str, dict]] = []

        def _fake_write(section: str, values: dict) -> None:
            calls.append((section, values))

        monkeypatch.setattr(
            "backend.services.license.write_config_section", _fake_write
        )

        adapter = ConfigTomlAdapter("server")
        adapter.set("default_model", "gemini-2.5-pro")

        assert calls == [("server", {"default_model": "gemini-2.5-pro"})]
        assert _restore_settings.default_model == "gemini-2.5-pro"

    def test_set_rejeita_tipo_invalido(self):
        adapter = ConfigTomlAdapter("server")
        with pytest.raises(TypeError):
            adapter.set("x", {"nested": "dict"})


# ---------------------------------------------------------------------------
# CLI: vectora config <categoria> --get/--set (backend/cli/config.py)
# ---------------------------------------------------------------------------


class TestCliCategoryCommand:
    def _ns(self, **overrides):
        import argparse

        base = {
            "config_action": None,
            "config_arg": None,
            "api_key": None,
            "set_values": None,
            "get_values": None,
            "list_collection": False,
            "user_id": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_set_e_get_roundtrip(self, tmp_path, monkeypatch, capsys):
        import backend.workspace.runtime_settings as rs_mod
        from backend.cli.config import run_config
        from backend.workspace.runtime_settings import RuntimeSettings

        isolated = RuntimeSettings(path=tmp_path / "checkpoints.db")
        monkeypatch.setattr(rs_mod, "runtime_settings", isolated)

        run_config(self._ns(config_action="preferences", set_values=["theme=light"]))
        out = capsys.readouterr().out
        assert "theme=light" in out

        run_config(self._ns(config_action="preferences", get_values=["theme"]))
        out = capsys.readouterr().out
        assert "theme=light" in out

    def test_get_chave_de_outra_categoria_da_erro(self, capsys):
        from backend.cli.config import run_config

        with pytest.raises(SystemExit) as exc:
            run_config(
                self._ns(config_action="preferences", get_values=["telegram_bot_token"])
            )
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "não existe na categoria" in out

    def test_set_formato_invalido_da_erro(self):
        from backend.cli.config import run_config

        with pytest.raises(SystemExit) as exc:
            run_config(self._ns(config_action="preferences", set_values=["sem-igual"]))
        assert exc.value.code == 1

    def test_sem_get_nem_set_lista_campos_da_categoria(self, capsys):
        from backend.cli.config import run_config

        run_config(self._ns(config_action="integrations"))
        out = capsys.readouterr().out
        assert "google_api_key" in out

    def test_secret_mascarado_na_listagem(self, tmp_path, monkeypatch, capsys):
        from backend.cli.config import run_config
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path / ".vectora")
        monkeypatch.setenv("GOOGLE_API_KEY", "placeholder")
        original = settings.google_api_key
        try:
            with patch("backend.cli.keys.upsert_env_key"):
                run_config(
                    self._ns(
                        config_action="integrations",
                        set_values=["google_api_key=AIzaSy-super-secreta-0000"],
                    )
                )
            out = capsys.readouterr().out
            assert "AIzaSy-super-secreta-0000" not in out
            assert "0000" in out  # últimos 4 chars aparecem, o resto não
        finally:
            object.__setattr__(settings, "google_api_key", original)


class TestCliCollectionCommand:
    """`vectora config provider-routing|memory|account --list` — cobre a
    listagem das categorias de coleção pelo dispatcher de configuração da CLI."""

    def _ns(self, **overrides):
        import argparse

        base = {
            "config_action": None,
            "config_arg": None,
            "api_key": None,
            "set_values": None,
            "get_values": None,
            "list_collection": False,
            "user_id": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_sem_list_flag_da_erro(self, capsys):
        from backend.cli.config import run_config

        with pytest.raises(SystemExit) as exc:
            run_config(self._ns(config_action="provider-routing"))
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "--list" in out

    def test_provider_routing_lista_modelos_registrados(self, capsys, monkeypatch):
        from backend.cli.config import run_config
        from backend.config.adapters import RegisteredModelsTableAdapter

        async def _fake_list(self):
            return [{"id": "1", "tag": "llama3", "created_at": "2026-01-01"}]

        monkeypatch.setattr(RegisteredModelsTableAdapter, "list_items", _fake_list)

        run_config(self._ns(config_action="provider-routing", list_collection=True))
        out = capsys.readouterr().out
        assert "ollama_registered_models" in out
        assert "llama3" in out

    def test_provider_routing_sem_itens_nao_quebra(self, capsys, monkeypatch):
        from backend.cli.config import run_config
        from backend.config.adapters import RegisteredModelsTableAdapter

        async def _fake_list(self):
            return []

        monkeypatch.setattr(RegisteredModelsTableAdapter, "list_items", _fake_list)

        run_config(self._ns(config_action="provider-routing", list_collection=True))
        out = capsys.readouterr().out
        assert "(nenhum item)" in out

    def test_memory_usa_user_id_local_por_default(self, capsys, monkeypatch):
        from backend.cli.config import run_config
        from backend.config.adapters import MemoryAdapter

        seen_user_ids = []

        async def _fake_list(self, user_id):
            seen_user_ids.append(user_id)
            return [{"key": "k1", "content": "prefere respostas curtas"}]

        monkeypatch.setattr(MemoryAdapter, "list_items", _fake_list)

        run_config(self._ns(config_action="memory", list_collection=True))
        out = capsys.readouterr().out
        assert seen_user_ids == ["local"]
        assert "prefere respostas curtas" in out

    def test_account_respeita_user_id_explicito(self, capsys, monkeypatch):
        from backend.cli.config import run_config
        from backend.config.adapters import UserProfileAdapter

        seen_user_ids = []

        async def _fake_list(self, user_id):
            seen_user_ids.append(user_id)
            return [{"id": user_id, "username": "bruno"}]

        monkeypatch.setattr(UserProfileAdapter, "list_items", _fake_list)

        run_config(
            self._ns(config_action="account", list_collection=True, user_id="user-42")
        )
        out = capsys.readouterr().out
        assert seen_user_ids == ["user-42"]
        assert "user-42" in out
