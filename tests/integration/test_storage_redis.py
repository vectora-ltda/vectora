"""Testes de integração — Redis (conectividade, cache LLM, chat history).

Requer Redis rodando (vectora-redis via docker).
Os fixtures de conftest.py sobem o container automaticamente se Docker estiver
disponível; do contrário, todos os testes são pulados.
"""

from __future__ import annotations

import pytest


class TestRedisConnectivity:
    """Conectividade básica ao Redis."""

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_ping(self, redis_client):
        result = await redis_client.ping()
        assert result is True

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_set_get(self, redis_client):
        await redis_client.set("_vtest_key", "hello", ex=30)
        val = await redis_client.get("_vtest_key")
        assert val == b"hello"
        await redis_client.delete("_vtest_key")

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_set_get_error_incr_on_list(self, redis_client):
        """Erro: INCR em chave que é uma lista levanta ResponseError."""
        import redis.asyncio

        await redis_client.delete("_vtest_list")
        await redis_client.rpush("_vtest_list", "item")
        with pytest.raises(redis.asyncio.ResponseError):
            await redis_client.incr("_vtest_list")
        await redis_client.delete("_vtest_list")

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_key_expiry(self, redis_client):
        """Chaves com TTL expiram após o tempo configurado."""
        import asyncio

        await redis_client.set("_vtest_expire", "val", px=150)
        before = await redis_client.get("_vtest_expire")
        assert before == b"val"
        await asyncio.sleep(0.3)
        after = await redis_client.get("_vtest_expire")
        assert after is None

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_delete(self, redis_client):
        await redis_client.set("_vtest_del", "x")
        await redis_client.delete("_vtest_del")
        val = await redis_client.get("_vtest_del")
        assert val is None

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_error_get_nonexistent_returns_none(self, redis_client):
        """GET em chave inexistente retorna None, não levanta exceção."""
        await redis_client.delete("_vtest_nonexistent_xyz")
        val = await redis_client.get("_vtest_nonexistent_xyz")
        assert val is None
