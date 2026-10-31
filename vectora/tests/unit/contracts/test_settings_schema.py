"""Testes para conformidade e integridade do schema de settings.json (v1)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.contracts.models import (
    AppearanceSettings,
    EditorSettings,
    FileSettings,
    LocaleSettings,
    UserSettingsV1,
)


def test_user_settings_v1_defaults() -> None:
    """Verifica o comportamento de user settings v1 defaults."""
    settings = UserSettingsV1()
    assert settings.schema_url == "vectora://schemas/settings/v1.json"
    assert settings.version == 1
    assert settings.locale.language == "pt-BR"
    assert settings.appearance.theme == "dark"
    assert settings.editor.fontSize == 13
    assert settings.files.encoding == "utf-8"


def test_user_settings_v1_schema_file_matches_model() -> None:
    """Verifica o comportamento de user settings v1 schema file matches model."""
    schema_file = (
        Path(__file__).resolve().parents[3]
        / "backend"
        / "contracts"
        / "settings"
        / "v1.schema.json"
    )
    assert schema_file.is_file(), "Arquivo v1.schema.json não encontrado"

    schema_json = json.loads(schema_file.read_text(encoding="utf-8"))
    model_schema = UserSettingsV1.model_json_schema()

    assert schema_json["title"] == model_schema["title"]
    assert "properties" in schema_json
    assert "locale" in schema_json["properties"]
    assert "appearance" in schema_json["properties"]
    assert "editor" in schema_json["properties"]
    assert "files" in schema_json["properties"]
    assert "workbenches" in schema_json["properties"]
    assert "updates" in schema_json["properties"]


def test_user_settings_v1_serialization_roundtrip() -> None:
    """Verifica o comportamento de user settings v1 serialization roundtrip."""
    custom = UserSettingsV1(
        locale=LocaleSettings(language="en", timezone="UTC"),
        appearance=AppearanceSettings(theme="light", sidebarPosition="right"),
        editor=EditorSettings(fontSize=16, fontLigatures=False, minimap=False),
        files=FileSettings(autoSave="afterDelay", autoSaveDelayMs=500),
    )
    dumped = custom.model_dump(by_alias=True)
    assert dumped["$schema"] == "vectora://schemas/settings/v1.json"
    assert dumped["locale"]["language"] == "en"
    assert dumped["appearance"]["theme"] == "light"
    assert dumped["editor"]["fontSize"] == 16
    assert dumped["files"]["autoSave"] == "afterDelay"

    reconstructed = UserSettingsV1.model_validate(dumped)
    assert reconstructed.locale.language == "en"
    assert reconstructed.appearance.theme == "light"
    assert reconstructed.editor.fontSize == 16


def test_user_settings_v1_validation_rejects_invalid_values() -> None:
    """Verifica o comportamento de user settings v1 validation rejects invalid values."""
    with pytest.raises(ValidationError):
        UserSettingsV1(editor=EditorSettings(fontSize=4))  # fontSize ge=8

    with pytest.raises(ValidationError):
        UserSettingsV1(appearance={"theme": "unsupported-theme"})  # type: ignore[arg-type]
