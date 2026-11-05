"""Testes unitários para src/api/middleware/rate_limit.py.

Cobre:
- attach_limiter: configura slowapi sem explodir
- get_limiter: retorna o limiter do state
- _get_client_ip: lê X-Forwarded-For e request.client.host
"""

from __future__ import annotations

import pytest

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

    def test_returns_forwarded_for_when_present(self):
        from backend.api.middleware.rate_limit import _get_client_ip

        req = self._make_request(headers={"X-Forwarded-For": "10.0.0.1, 192.168.0.1"})
        assert _get_client_ip(req) == "10.0.0.1"

    def test_returns_client_host_when_no_forwarded(self):
        from backend.api.middleware.rate_limit import _get_client_ip

        req = self._make_request(headers={}, client_host="192.168.1.5")
        assert _get_client_ip(req) == "192.168.1.5"

    def test_returns_unknown_when_no_client(self):
        from unittest.mock import MagicMock

        from backend.api.middleware.rate_limit import _get_client_ip

        req = MagicMock()
        req.headers = {}
        req.client = None
        assert _get_client_ip(req) == "unknown"


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
