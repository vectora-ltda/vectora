"""Tests for release update-channel selection."""

from __future__ import annotations

import pytest
from select_update_channel import select_update_channel


def test_maintenance_version_selects_maintenance() -> None:
    assert select_update_channel("0.1.23") == "maintenance"


@pytest.mark.parametrize("version", ["0.2.0", "1.0.0"])
def test_current_minor_versions_select_latest(version: str) -> None:
    assert select_update_channel(version) == "latest"


@pytest.mark.parametrize("version", ["", "0.1", "v0.1.23", "0.1.x"])
def test_missing_or_malformed_versions_fail(version: str) -> None:
    with pytest.raises(ValueError, match="Versão do pacote inválida"):
        select_update_channel(version)


def test_null_version_fails_with_controlled_error() -> None:
    with pytest.raises(ValueError, match="Versão do pacote inválida"):
        select_update_channel(None)
