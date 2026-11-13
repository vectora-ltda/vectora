"""Endpoints dos limites de mídia (/admin/media-limits)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from fastapi import HTTPException

from backend.api.handlers import admin
from backend.services import media_limits as limits

if TYPE_CHECKING:
    from fastapi import Request


class _Request:
    """Requisição mínima: ``require_admin`` e ``_get_user`` são substituídos."""


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Troca os campos media_* por um dicionário em memória."""
    values: dict[str, str] = {}
    monkeypatch.setattr(admin, "require_admin", lambda _user: None)
    monkeypatch.setattr(admin, "_get_user", lambda _request: object())
    monkeypatch.setattr(limits, "_setting", lambda key: values.get(key, ""))

    class _Field:
        def __init__(self, name: str) -> None:
            self.name = name

        def set(self, value: str) -> None:
            values[self.name] = value

    monkeypatch.setattr("backend.config.registry.get_field", _Field)
    return values


async def test_get_devolve_valor_padrao_e_faixa(store: dict[str, str]) -> None:
    data = await admin.get_media_limits(cast("Request", _Request()))
    assert data["pdf_pages"] == {"value": 20, "default": 20, "min": 1, "max": 500}
    assert data["max_side"] == {"value": 1280, "default": 1280, "min": 256, "max": 4096}
    assert set(data) == {
        "pdf_pages",
        "video_frames",
        "max_side",
        "quality",
        "max_images",
    }


async def test_patch_grava_e_devolve_o_estado_efetivo(store: dict[str, str]) -> None:
    data = await admin.patch_media_limits(
        cast("Request", _Request()),
        admin.MediaLimitsBody(pdf_pages=40, quality=90),
    )
    assert store == {"media_pdf_max_pages": "40", "media_image_quality": "90"}
    assert cast("dict", data["pdf_pages"])["value"] == 40
    assert cast("dict", data["quality"])["value"] == 90
    assert cast("dict", data["video_frames"])["value"] == 12


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("pdf_pages", 0), ("pdf_pages", 501), ("max_side", 100), ("quality", 99)],
)
async def test_patch_recusa_valor_fora_da_faixa(
    store: dict[str, str], campo: str, valor: int
) -> None:
    with pytest.raises(HTTPException) as exc:
        await admin.patch_media_limits(
            cast("Request", _Request()), admin.MediaLimitsBody(**{campo: valor})
        )
    assert exc.value.status_code == 422
    assert store == {}


async def test_patch_vazio_nao_grava_nada(store: dict[str, str]) -> None:
    await admin.patch_media_limits(cast("Request", _Request()), admin.MediaLimitsBody())
    assert store == {}
