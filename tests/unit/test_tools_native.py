"""Tools nativas utilitárias (backend/tools/native/) — cobre caminho feliz
+ erro para cada uma das tools registradas em ALL_TOOLS: time_now,
time_parse, http_request (cada uma em seu próprio módulo) e
hash_text/base64_encode/base64_decode/jwt_decode/regex_test/json_query
(consolidadas em crypto_utils.py).

Todas as tools nativas são funções ``@vtool`` (backend/tools/registry.py) —
chamadas diretamente como corrotina, sem interface ``BaseTool.ainvoke``.
"""

from __future__ import annotations

import hashlib
import json

import pytest

# ---------------------------------------------------------------------------
# time_now
# ---------------------------------------------------------------------------


class TestTimeNow:
    @pytest.mark.asyncio
    async def test_returns_iso_string_utc(self) -> None:
        from backend.tools.native.time_now import time_now

        result = await time_now(timezone="UTC")
        assert isinstance(result, str)
        assert "T" in result  # ISO 8601

    @pytest.mark.asyncio
    async def test_default_timezone_is_utc(self) -> None:
        from backend.tools.native.time_now import time_now

        result = await time_now()
        assert "T" in result

    @pytest.mark.asyncio
    async def test_invalid_timezone_returns_error(self) -> None:
        from backend.tools.native.time_now import time_now

        result = await time_now(timezone="Invalid/Zone")
        assert result.startswith("error:")


# ---------------------------------------------------------------------------
# time_parse
# ---------------------------------------------------------------------------


class TestTimeParse:
    @pytest.mark.asyncio
    async def test_iso_date_parses(self) -> None:
        from backend.tools.native.time_parse import time_parse

        result = await time_parse(date_string="2024-01-15T10:30:00Z")
        assert "2024" in result
        assert result.startswith("error:") is False

    @pytest.mark.asyncio
    async def test_date_only_parses(self) -> None:
        from backend.tools.native.time_parse import time_parse

        result = await time_parse(date_string="2024-01-15")
        assert "2024" in result

    @pytest.mark.asyncio
    async def test_invalid_date_returns_error(self) -> None:
        from backend.tools.native.time_parse import time_parse

        result = await time_parse(date_string="not a date at all $$$$")
        assert result.startswith("error:")


# ---------------------------------------------------------------------------
# hash_text
# ---------------------------------------------------------------------------


class TestHashText:
    @pytest.mark.asyncio
    async def test_sha256_hash(self) -> None:
        from backend.tools.native.crypto_utils import hash_text

        result = await hash_text(text="hello", algorithm="sha256")
        expected = hashlib.sha256(b"hello").hexdigest()
        assert result == expected

    @pytest.mark.asyncio
    async def test_md5_hash(self) -> None:
        from backend.tools.native.crypto_utils import hash_text

        result = await hash_text(text="world", algorithm="md5")
        expected = hashlib.md5(b"world").hexdigest()  # noqa: S324
        assert result == expected

    @pytest.mark.asyncio
    async def test_sha512_hash(self) -> None:
        from backend.tools.native.crypto_utils import hash_text

        result = await hash_text(text="x", algorithm="sha512")
        assert len(result) == 128  # sha512 hex = 128 chars

    @pytest.mark.asyncio
    async def test_invalid_algorithm_returns_error(self) -> None:
        from backend.tools.native.crypto_utils import hash_text

        result = await hash_text(text="x", algorithm="zzz")
        assert result.startswith("error:")


# ---------------------------------------------------------------------------
# base64_encode / base64_decode
# ---------------------------------------------------------------------------


class TestBase64:
    @pytest.mark.asyncio
    async def test_encode(self) -> None:
        import base64

        from backend.tools.native.crypto_utils import base64_encode

        result = await base64_encode(text="hello")
        assert result == base64.b64encode(b"hello").decode()

    @pytest.mark.asyncio
    async def test_decode(self) -> None:
        import base64

        from backend.tools.native.crypto_utils import base64_decode

        encoded = base64.b64encode(b"world").decode()
        result = await base64_decode(encoded=encoded)
        assert result == "world"

    @pytest.mark.asyncio
    async def test_invalid_decode_returns_error(self) -> None:
        from backend.tools.native.crypto_utils import base64_decode

        result = await base64_decode(encoded="!!!not_valid_base64!!!")
        assert result.startswith("error:")


# ---------------------------------------------------------------------------
# regex_test
# ---------------------------------------------------------------------------


class TestRegexTest:
    @pytest.mark.asyncio
    async def test_match_found(self) -> None:
        from backend.tools.native.crypto_utils import regex_test

        result = await regex_test(pattern=r"\d+", text="abc123def")
        assert result == "match"

    @pytest.mark.asyncio
    async def test_no_match(self) -> None:
        from backend.tools.native.crypto_utils import regex_test

        result = await regex_test(pattern=r"\d{5}", text="abc")
        assert result == "no match"

    @pytest.mark.asyncio
    async def test_invalid_pattern_returns_error(self) -> None:
        from backend.tools.native.crypto_utils import regex_test

        result = await regex_test(pattern="[invalid(", text="abc")
        assert result.startswith("error:")


# ---------------------------------------------------------------------------
# json_query
# ---------------------------------------------------------------------------


class TestJsonQuery:
    @pytest.mark.asyncio
    async def test_simple_key(self) -> None:
        from backend.tools.native.crypto_utils import json_query

        obj = json.dumps({"name": "Alice", "age": 30})
        result = await json_query(json_str=obj, path="name")
        assert result == "Alice"

    @pytest.mark.asyncio
    async def test_nested_key(self) -> None:
        from backend.tools.native.crypto_utils import json_query

        obj = json.dumps({"a": {"b": {"c": 42}}})
        result = await json_query(json_str=obj, path="a.b.c")
        assert result == "42"

    @pytest.mark.asyncio
    async def test_array_index(self) -> None:
        from backend.tools.native.crypto_utils import json_query

        obj = json.dumps({"items": [10, 20, 30]})
        result = await json_query(json_str=obj, path="items[1]")
        assert result == "20"

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self) -> None:
        from backend.tools.native.crypto_utils import json_query

        result = await json_query(json_str="not json {{{", path="key")
        assert result.startswith("error:")

    @pytest.mark.asyncio
    async def test_missing_key_returns_error(self) -> None:
        from backend.tools.native.crypto_utils import json_query

        obj = json.dumps({"x": 1})
        result = await json_query(json_str=obj, path="missing")
        assert result.startswith("error:")


# ---------------------------------------------------------------------------
# jwt_decode
# ---------------------------------------------------------------------------


class TestJwtDecode:
    @pytest.mark.asyncio
    async def test_decode_hs256_payload(self) -> None:
        import jwt

        from backend.tools.native.crypto_utils import jwt_decode

        token = jwt.encode(
            {"sub": "user1", "role": "admin"}, "secret", algorithm="HS256"
        )
        result = await jwt_decode(token=token)
        data = json.loads(result)
        assert data.get("sub") == "user1"
        assert data.get("role") == "admin"

    @pytest.mark.asyncio
    async def test_malformed_token_returns_error(self) -> None:
        from backend.tools.native.crypto_utils import jwt_decode

        result = await jwt_decode(token="not.a.token.at.all")
        assert result.startswith("error:")


# ---------------------------------------------------------------------------
# http_request
# ---------------------------------------------------------------------------


class TestHttpRequest:
    @pytest.mark.asyncio
    async def test_get_returns_body_text(self, monkeypatch) -> None:
        import httpx

        from backend.tools.native.http_request import http_request

        class _FakeResponse:
            text = '{"ok": true}'

        class _FakeClient:
            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *_: object) -> None:
                pass

            async def request(self, *_: object, **__: object) -> _FakeResponse:
                return _FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _FakeClient())
        result = await http_request(method="GET", url="https://example.com/api")
        assert result == '{"ok": true}'

    @pytest.mark.asyncio
    async def test_connection_error_returns_error(self, monkeypatch) -> None:
        import httpx

        from backend.tools.native.http_request import http_request

        class _ErrorClient:
            async def __aenter__(self) -> _ErrorClient:
                return self

            async def __aexit__(self, *_: object) -> None:
                pass

            async def request(self, *_: object, **__: object) -> None:
                raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _ErrorClient())
        result = await http_request(method="GET", url="https://bad-host.invalid/")
        assert result.startswith("error:")


# ---------------------------------------------------------------------------
# Registro no TOOL_REGISTRY — caminho real de chamada por nome (LLM)
# ---------------------------------------------------------------------------


class TestNativeToolsRegisteredByName:
    """Nome exposto ao LLM precisa continuar idêntico ao pré-migração —
    quem já chama por nome (dispatch, testes de integração) não pode
    quebrar com a troca do decorator legado para `@vtool` (nativo)."""

    @pytest.mark.parametrize(
        "name",
        [
            "time_now",
            "time_parse",
            "hash_text",
            "base64_encode",
            "base64_decode",
            "jwt_decode",
            "regex_test",
            "json_query",
            "http_request",
        ],
    )
    def test_tool_registrada_com_nome_estavel(self, name: str) -> None:
        from backend.tools.registry import TOOL_REGISTRY

        spec = TOOL_REGISTRY.get(name)
        assert spec is not None, f"tool {name!r} não registrada em TOOL_REGISTRY"
        assert spec.name == name

    @pytest.mark.asyncio
    async def test_ainvoke_via_registry_devolve_string(self) -> None:
        from backend.tools.context import ToolContext
        from backend.tools.registry import TOOL_REGISTRY

        spec = TOOL_REGISTRY.get("hash_text")
        assert spec is not None
        result = await spec.ainvoke(
            {"text": "oi", "algorithm": "sha256"}, ctx=ToolContext()
        )

        assert result == hashlib.sha256(b"oi").hexdigest()

    @pytest.mark.asyncio
    async def test_ainvoke_via_registry_argumento_invalido_nao_propaga(self) -> None:
        """Erro/borda: algoritmo inexistente no hashlib chega ao handler
        (passa na validação Pydantic — é `str`), mas o handler captura e
        devolve string de erro tipada, nunca uma exceção crua."""
        from backend.tools.context import ToolContext
        from backend.tools.registry import TOOL_REGISTRY

        spec = TOOL_REGISTRY.get("hash_text")
        assert spec is not None
        result = await spec.ainvoke(
            {"text": "oi", "algorithm": "nao-existe"}, ctx=ToolContext()
        )

        assert result.startswith("error:")
