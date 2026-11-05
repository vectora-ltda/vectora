"""Testes do loop de revalidação de licença do servidor.

O loop roda no lifespan (``src/api/server.py``) e NUNCA pode derrubar o
servidor — falha de licença/network vira warning e o estado fica visível
via ``GET /license/status``.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.api import server as server_mod
from backend.services.license import LicenseError


def _run_one_iteration(monkeypatch: pytest.MonkeyPatch, validate) -> list[str]:
    """Executa exatamente 1 iteração do loop (cancela no primeiro sleep)."""
    from backend.services import license as lic

    monkeypatch.setattr(lic, "validate_license_async", validate)

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(server_mod.asyncio, "sleep", fake_sleep)

    async def runner() -> None:
        with pytest.raises(asyncio.CancelledError):
            await server_mod._license_revalidation_loop()

    asyncio.run(runner())
    return [str(s) for s in sleeps]


def test_loop_sucesso_dorme_6h(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.services.license import LicenseStatusInfo

    async def ok(**kw):
        return LicenseStatusInfo(
            tier="pro",
            status="active",
            days_remaining=30,
            expires_at="",
            validated_at="",
            cached=False,
        )

    sleeps = _run_one_iteration(monkeypatch, ok)
    assert sleeps == [str(server_mod._LICENSE_REVALIDATE_INTERVAL_S)]


def test_loop_license_error_nao_derruba(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(**kw):
        raise LicenseError("token inválido")

    # Não pode propagar LicenseError — só o CancelledError do sleep fake.
    sleeps = _run_one_iteration(monkeypatch, fail)
    assert len(sleeps) == 1


def test_loop_erro_inesperado_nao_derruba(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(**kw):
        raise RuntimeError("network explodiu")

    sleeps = _run_one_iteration(monkeypatch, boom)
    assert len(sleeps) == 1
