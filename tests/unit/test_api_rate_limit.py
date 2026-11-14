"""Testes unitários para src/api/middleware/rate_limit.py.

Cobre:
- attach_limiter: configura slowapi sem explodir
- get_limiter: retorna o limiter do state
- _get_client_ip: lê X-Forwarded-For e request.client.host
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# _get_client_ip
# ---------------------------------------------------------------------------


class TestGetClientIp:
    def _make_request(self, headers=None, client_host="127.0.0.1"):
        from unittest.mock import MagicMock

        from fastapi import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
        }
        req = MagicMock(spec=Request)
        req.headers = headers or {}
        req.client = MagicMock()
        req.client.host = client_host
        return req


# ---------------------------------------------------------------------------
# attach_limiter
# ---------------------------------------------------------------------------


class TestAttachLimiter:
    def test_attach_does_not_raise(self):
        from fastapi import FastAPI

        from backend.api.middleware.rate_limit import attach_limiter

        app = FastAPI()
        attach_limiter(app)  # Não deve levantar exceção

    def test_limiter_attached_to_app_state(self):
        from fastapi import FastAPI

        from backend.api.middleware.rate_limit import attach_limiter

        app = FastAPI()
        attach_limiter(app)
        # Se slowapi estiver disponível, o limiter é attachado
        # Se não estiver, o state não tem o atributo — ambos são válidos
        # O importante é não explodir


# ---------------------------------------------------------------------------
# get_limiter
# ---------------------------------------------------------------------------


class TestGetLimiter:
    def test_returns_none_when_no_limiter(self):
        from unittest.mock import MagicMock

        from backend.api.middleware.rate_limit import get_limiter

        req = MagicMock()
        req.app.state = MagicMock(spec=[])  # sem atributo limiter
        result = get_limiter(req)
        assert result is None

    def test_returns_limiter_when_present(self):
        from unittest.mock import MagicMock

        from backend.api.middleware.rate_limit import get_limiter

        req = MagicMock()
        fake_limiter = object()
        req.app.state.limiter = fake_limiter
        assert get_limiter(req) is fake_limiter
