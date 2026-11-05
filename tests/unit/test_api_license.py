"""Testes dos endpoints de licença — /license/{status,validate,connect}.

Cobrem o fluxo do setup wizard: validação forçada do token e login com a
conta vectora.company (``services.vectora.company/license/agent-login``
mockada via httpx).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.api.handlers import license as handler
from backend.services import license as lic


@pytest.fixture(autouse=True)
def _isolado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isola cache/config e env vars do serviço de licença."""
    monkeypatch.setattr(lic, "CACHE_PATH", tmp_path / "license_cache.json")
    monkeypatch.setattr(lic, "CONFIG_PATH", tmp_path / "config.toml")
    for var in ("VECTORA_TOKEN", "VECTORA_LICENSE_BYPASS", "VECTORA_LICENSE_URL"):
        monkeypatch.delenv(var, raising=False)


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _patch_http(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, tuple[dict, int]],
) -> list[tuple[str, dict]]:
    """Mocka httpx.AsyncClient.post nos módulos handler e serviço.

    ``responses`` mapeia substring da URL → (payload, status). Retorna a lista
    de chamadas capturadas (url, json_body).
    """
    calls: list[tuple[str, dict]] = []

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> None: ...

        async def post(self, url: str, json: dict | None = None, **kw: Any) -> Any:
            calls.append((url, json or {}))
            for fragment, (payload, status) in responses.items():
                if fragment in url:
                    return _FakeResponse(payload, status)
            return _FakeResponse({}, 500)

    monkeypatch.setattr(handler.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(lic.httpx, "AsyncClient", _FakeClient)
    return calls


def _request_with_role(role: str | None) -> Any:
    """Request fake com request.state.user.role para _require_root."""
    user = SimpleNamespace(role=role) if role else None
    state = SimpleNamespace(user=user)
    return SimpleNamespace(state=state)


# ---------------------------------------------------------------------------
# GET /license/status
# ---------------------------------------------------------------------------


def test_status_sem_cache_e_unconfigured() -> None:
    result = asyncio.run(handler.license_status())
    assert result["configured"] is False
    assert result["status"] == "unknown"


def test_status_com_cache() -> None:
    lic.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    lic.CACHE_PATH.write_text(
        json.dumps(
            {
                "tier": "pro",
                "status": "trial",
                "days_remaining": 9,
                "expires_at": "2026-06-20",
                "validated_at": "2026-06-11T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    result = asyncio.run(handler.license_status())
    assert result["configured"] is True
    assert result["tier"] == "pro"
    assert result["days_remaining"] == 9


# ---------------------------------------------------------------------------
# POST /license/validate
# ---------------------------------------------------------------------------


def test_validate_sem_token_responde_token_missing() -> None:
    result = asyncio.run(handler.license_validate())
    assert result == {"valid": False, "configured": False, "error": "token_missing"}


def test_validate_token_valido(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VECTORA_TOKEN", "tok_ok")
    _patch_http(
        monkeypatch,
        {
            "license/validate": (
                {
                    "valid": True,
                    "tier": "pro",
                    "status": "active",
                    "days_remaining": 30,
                },
                200,
            )
        },
    )
    result = asyncio.run(handler.license_validate())
    assert result["valid"] is True
    assert result["tier"] == "pro"


def test_validate_token_invalido_vai_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VECTORA_TOKEN", "tok_ruim")
    _patch_http(
        monkeypatch,
        {"license/validate": ({"valid": False, "reason": "not_found"}, 200)},
    )
    result = asyncio.run(handler.license_validate())
    # Falha de licença NUNCA vira HTTP 4xx aqui — o wizard lê {valid, error}.
    assert result["valid"] is False
    assert "inválido" in result["error"]


# ---------------------------------------------------------------------------
# POST /license/connect
# ---------------------------------------------------------------------------


def _connect(
    monkeypatch: pytest.MonkeyPatch,
    *,
    role: str | None = "root",
    email: str = "user@example.com",
    password: str = "secret",
    responses: dict[str, tuple[dict, int]] | None = None,
) -> tuple[dict, list[tuple[str, dict]]]:
    calls = _patch_http(monkeypatch, responses or {})
    body = handler.ConnectBody(email=email, password=password)
    result = asyncio.run(handler.license_connect(body, _request_with_role(role)))
    return result, calls


def test_connect_exige_root(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _connect(monkeypatch, role="member")
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        _connect(monkeypatch, role=None)
    assert exc.value.status_code == 401


def test_connect_credenciais_incompletas(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _connect(monkeypatch, email="sem-arroba", password="x")
    assert exc.value.status_code == 422


def test_connect_credenciais_erradas_401(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _connect(
            monkeypatch,
            responses={"agent-login": ({"error": "invalid_credentials"}, 401)},
        )
    assert exc.value.status_code == 401


def test_connect_sem_token_404(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _connect(
            monkeypatch,
            responses={"agent-login": ({"error": "token_not_found"}, 404)},
        )
    assert exc.value.status_code == 404


def test_connect_sucesso_persiste_e_valida(monkeypatch: pytest.MonkeyPatch) -> None:
    result, calls = _connect(
        monkeypatch,
        responses={
            "agent-login": (
                {"token": "tok_novo", "tier": "free", "status": "trialing"},
                200,
            ),
            "license/validate": (
                {
                    "valid": True,
                    "tier": "free",
                    "status": "trial",
                    "days_remaining": 30,
                },
                200,
            ),
        },
    )
    assert result["connected"] is True
    assert result["valid"] is True
    # Token foi ativado no env e persistido no config.toml [license].
    import os

    assert os.environ["VECTORA_TOKEN"] == "tok_novo"
    assert 'token = "tok_novo"' in lic.CONFIG_PATH.read_text(encoding="utf-8")
    # E a validação remota foi de fato chamada com o token novo.
    validate_calls = [c for c in calls if "license/validate" in c[0]]
    assert validate_calls and validate_calls[0][1]["token"] == "tok_novo"


# ---------------------------------------------------------------------------
# POST /license/validate-token — wizard de onboarding (pré-conta, gate VPS)
# ---------------------------------------------------------------------------


def test_validate_token_endpoint_exige_ausencia_de_usuarios(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    monkeypatch.setattr("backend.rbac.auth.has_users", _async_true, raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            handler.license_validate_token(handler.ValidateTokenBody(token="tok"))
        )
    assert exc.value.status_code == 409


def _async_true() -> Any:
    async def _inner() -> bool:
        return True

    return _inner()


def _async_false() -> Any:
    async def _inner() -> bool:
        return False

    return _inner()


def test_validate_token_endpoint_token_vazio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.rbac.auth.has_users", _async_false, raising=False)
    result = asyncio.run(
        handler.license_validate_token(handler.ValidateTokenBody(token="  "))
    )
    assert result == {"valid": False, "error": "token_required"}


def test_validate_token_endpoint_pro_persiste(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.rbac.auth.has_users", _async_false, raising=False)
    _patch_http(
        monkeypatch,
        {
            "license/validate": (
                {
                    "valid": True,
                    "tier": "pro",
                    "status": "active",
                    "days_remaining": 30,
                },
                200,
            )
        },
    )
    result = asyncio.run(
        handler.license_validate_token(handler.ValidateTokenBody(token="tok_pro"))
    )
    import os

    assert result["valid"] is True
    assert result["tier"] == "pro"
    assert os.environ["VECTORA_TOKEN"] == "tok_pro"
    # Token Pro válido é persistido — o /auth/signup seguinte já herda ele.
    assert 'token = "tok_pro"' in lic.CONFIG_PATH.read_text(encoding="utf-8")


def test_validate_token_endpoint_free_nao_persiste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.rbac.auth.has_users", _async_false, raising=False)
    _patch_http(
        monkeypatch,
        {
            "license/validate": (
                {
                    "valid": True,
                    "tier": "free",
                    "status": "active",
                    "days_remaining": 0,
                },
                200,
            )
        },
    )
    result = asyncio.run(
        handler.license_validate_token(handler.ValidateTokenBody(token="tok_free"))
    )
    assert result["valid"] is False
    assert result["error"] == "not_pro_tier"
    assert not lic.CONFIG_PATH.is_file()


def test_validate_token_endpoint_invalido(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.rbac.auth.has_users", _async_false, raising=False)
    _patch_http(
        monkeypatch,
        {"license/validate": ({"valid": False, "reason": "not_found"}, 200)},
    )
    result = asyncio.run(
        handler.license_validate_token(handler.ValidateTokenBody(token="tok_ruim"))
    )
    assert result["valid"] is False
    assert not lic.CONFIG_PATH.is_file()
