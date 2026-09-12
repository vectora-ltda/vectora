"""
Testes unitários para a resiliência de reconexão do NATS em mq.py e kv.py.

Garante que:
- Callbacks de erro são configurados no nats.connect()
- max_reconnect_attempts limita os retries (não infinito)
- Após falha permanente (_failed=True), operações lançam RuntimeError
- reset_mq() / reset_kv() são chamados no closed_cb para forçar fallback
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


class TestNatsMQResilience:
    """Testa que NatsMQ é resiliente a quedas do sidecar NATS."""

    @pytest.mark.asyncio
    async def test_connect_usa_max_reconnect_attempts(self):
        """nats.connect() deve ser chamado com max_reconnect_attempts=5."""
        from backend.scheduling.mq import NatsMQ

        mock_nc = AsyncMock()
        mock_nc.jetstream.return_value = AsyncMock()

        with patch(
            "nats.connect", new_callable=AsyncMock, return_value=mock_nc
        ) as mock_connect:
            mq = NatsMQ("nats://127.0.0.1:4222")
            await mq._connect()

            _, kwargs = mock_connect.call_args
            assert kwargs.get("max_reconnect_attempts") == 5, (
                "max_reconnect_attempts deve ser 5, não infinito"
            )

    @pytest.mark.asyncio
    async def test_connect_configura_connect_timeout(self):
        """nats.connect() deve ter connect_timeout definido."""
        from backend.scheduling.mq import NatsMQ

        mock_nc = AsyncMock()
        mock_nc.jetstream.return_value = AsyncMock()

        with patch(
            "nats.connect", new_callable=AsyncMock, return_value=mock_nc
        ) as mock_connect:
            mq = NatsMQ("nats://127.0.0.1:4222")
            await mq._connect()

            _, kwargs = mock_connect.call_args
            assert "connect_timeout" in kwargs, "connect_timeout deve ser configurado"
            assert kwargs["connect_timeout"] > 0

    @pytest.mark.asyncio
    async def test_connect_registra_error_cb(self):
        """nats.connect() deve ter error_cb registrado."""
        from backend.scheduling.mq import NatsMQ

        mock_nc = AsyncMock()
        mock_nc.jetstream.return_value = AsyncMock()

        with patch(
            "nats.connect", new_callable=AsyncMock, return_value=mock_nc
        ) as mock_connect:
            mq = NatsMQ("nats://127.0.0.1:4222")
            await mq._connect()

            _, kwargs = mock_connect.call_args
            assert callable(kwargs.get("error_cb")), "error_cb deve ser um callable"
            assert callable(kwargs.get("disconnected_cb")), (
                "disconnected_cb deve ser um callable"
            )
            assert callable(kwargs.get("closed_cb")), "closed_cb deve ser um callable"

    @pytest.mark.asyncio
    async def test_closed_cb_marca_failed_e_reseta_singleton(self):
        """Quando closed_cb é chamado, _failed=True e reset_mq() é invocado."""
        from backend.scheduling import mq as mq_module
        from backend.scheduling.mq import NatsMQ

        mock_nc = AsyncMock()
        mock_js = AsyncMock()
        mock_nc.jetstream.return_value = mock_js

        closed_cb_ref = None

        async def capture_connect(*args, **kwargs):
            nonlocal closed_cb_ref
            closed_cb_ref = kwargs.get("closed_cb")
            return mock_nc

        with patch("nats.connect", side_effect=capture_connect):
            with patch.object(mq_module, "reset_mq") as mock_reset:
                mq = NatsMQ("nats://127.0.0.1:4222")
                await mq._connect()

                assert closed_cb_ref is not None, "closed_cb não foi capturado"
                # Simula o NATS chamando o closed_cb após esgotar retries
                await closed_cb_ref()

                assert mq._failed is True, "_failed deve ser True após closed_cb"
                mock_reset.assert_called_once()

    @pytest.mark.asyncio
    async def test_operacoes_falham_apos_failed(self):
        """Após _failed=True, _connect() deve lançar RuntimeError imediatamente."""
        from backend.scheduling.mq import NatsMQ

        mq = NatsMQ("nats://127.0.0.1:4222")
        mq._failed = True

        with pytest.raises(RuntimeError, match="NATS permanentemente desconectado"):
            await mq._connect()


class TestNatsKVResilience:
    """Testa que NatsKV é resiliente a quedas do sidecar NATS."""

    @pytest.mark.asyncio
    async def test_connect_usa_max_reconnect_attempts(self):
        """nats.connect() deve ser chamado com max_reconnect_attempts=5."""
        from backend.persistence.kv import NatsKV

        mock_kv_bucket = AsyncMock()
        # mock_js é síncrono (jetstream() não é async na API do nats-py)
        mock_js = MagicMock()
        mock_js.key_value = AsyncMock(return_value=mock_kv_bucket)
        mock_js.create_key_value = AsyncMock(return_value=mock_kv_bucket)

        # mock_nc é MagicMock (não AsyncMock) para que jetstream() retorne MagicMock
        mock_nc = MagicMock()
        mock_nc.jetstream.return_value = mock_js

        # nats.connect é async, por isso patch com AsyncMock
        with patch(
            "nats.connect", new_callable=AsyncMock, return_value=mock_nc
        ) as mock_connect:
            kv = NatsKV("nats://127.0.0.1:4222")
            await kv._connect()

            _, kwargs = mock_connect.call_args
            assert kwargs.get("max_reconnect_attempts") == 5

    @pytest.mark.asyncio
    async def test_closed_cb_marca_failed_e_reseta_singleton(self):
        """Quando closed_cb é chamado, _failed=True e reset_kv() é invocado."""
        from backend.persistence import kv as kv_module
        from backend.persistence.kv import NatsKV

        mock_kv_bucket = AsyncMock()
        mock_js = MagicMock()
        mock_js.key_value = AsyncMock(return_value=mock_kv_bucket)
        mock_js.create_key_value = AsyncMock(return_value=mock_kv_bucket)

        # mock_nc é MagicMock para que jetstream() seja síncrono
        mock_nc = MagicMock()
        mock_nc.jetstream.return_value = mock_js

        closed_cb_ref = None

        async def capture_connect(*args, **kwargs):
            nonlocal closed_cb_ref
            closed_cb_ref = kwargs.get("closed_cb")
            return mock_nc

        with patch("nats.connect", side_effect=capture_connect):
            with patch.object(kv_module, "reset_kv") as mock_reset:
                kv = NatsKV("nats://127.0.0.1:4222")
                await kv._connect()

                assert closed_cb_ref is not None
                await closed_cb_ref()

                assert kv._failed is True
                mock_reset.assert_called_once()

    @pytest.mark.asyncio
    async def test_operacoes_falham_apos_failed(self):
        """Após _failed=True, _connect() deve lançar RuntimeError imediatamente."""
        from backend.persistence.kv import NatsKV

        kv = NatsKV("nats://127.0.0.1:4222")
        kv._failed = True

        with pytest.raises(RuntimeError, match="NATS permanentemente desconectado"):
            await kv._connect()


class TestNatsReconnectDoesNotCrashBackend:
    """
    Testa o cenário de integração: NATS cai → closed_cb dispara → singleton
    reseta → próxima operação usa MemoryMQ (fallback) em vez de crashar.
    """

    @pytest.mark.asyncio
    async def test_get_mq_usa_memory_apos_nats_fechar(self):
        """Após reset_mq(), get_mq() deve retornar MemoryMQ como fallback."""
        from backend.scheduling import mq as mq_module
        from backend.scheduling.mq import MemoryMQ

        # Reseta o singleton
        mq_module.reset_mq()

        # Simula NATS indisponível (ensure_nats_sidecar retorna None)
        with (
            patch(
                "backend.scheduling.nats_sidecar.ensure_nats_sidecar",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("backend.persistence.kv.redis_reachable", return_value=False),
        ):
            result = await mq_module.get_mq()

        assert isinstance(result, MemoryMQ), (
            "Sem NATS disponível, deve cair para MemoryMQ"
        )

        # Limpa singleton pós-teste
        mq_module.reset_mq()
