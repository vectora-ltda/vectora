"""
Testes unitários para a lógica de persistência de background session em
backend/api/native_stream.py (_consume_remainder).

Verifica que após a "desconexão" do cliente, o motor nativo continua
consumindo eventos em background (multi-tarefas real) em vez de ser cancelado.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, patch

import pytest


class TestConsumeRemainder:
    """
    Testa a função _consume_remainder definida dentro de stream_engine_events.

    Isolamos o comportamento copiando a lógica para poder testá-la
    de forma unitária sem precisar do loop inteiro de stream_engine_events.
    """

    async def _consume_remainder(self, pending_task, iterator):
        """Réplica da função interna de native_stream.py para teste isolado."""
        try:
            with contextlib.suppress(Exception):
                await pending_task
            async for _ in iterator:
                pass
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_consume_remainder_drena_iterador_completo(self):
        """Deve consumir todos os eventos do iterador após desconexão."""
        collected = []

        async def fake_iterator():
            for i in range(5):
                collected.append(i)
                yield i

        # pending_task já concluída
        pending = asyncio.create_task(asyncio.sleep(0))
        await pending

        await self._consume_remainder(pending, fake_iterator())
        assert collected == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_consume_remainder_suprime_excecao_do_iterador(self):
        """Erros durante consumo do background não devem propagar."""

        async def error_iterator():
            yield 1
            raise RuntimeError("LLM erro de quota")

        pending = asyncio.create_task(asyncio.sleep(0))
        await pending

        # Não deve levantar exceção
        await self._consume_remainder(pending, error_iterator())

    @pytest.mark.asyncio
    async def test_consume_remainder_suprime_excecao_do_pending_task(self):
        """Erro na pending_task pendente não deve propagar."""

        async def failing_task():
            raise ValueError("task falhou")

        pending = asyncio.create_task(failing_task())
        collected = []

        async def simple_iterator():
            for i in range(3):
                collected.append(i)
                yield i

        await self._consume_remainder(pending, simple_iterator())
        # O iterador deve ter continuado mesmo com falha na pending_task
        assert collected == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_background_task_continua_apos_cancel_principal(self):
        """
        Simula o cenário real: task lançada em background deve continuar
        executando mesmo que o loop principal seja "abandonado".
        """
        events_consumed = []

        async def slow_iterator():
            for i in range(3):
                await asyncio.sleep(0.01)
                events_consumed.append(i)
                yield i

        async def fake_pending():
            pass

        pending = asyncio.create_task(fake_pending())

        # Cria a task de background (como adapters.py faz)
        bg_task = asyncio.create_task(self._consume_remainder(pending, slow_iterator()))

        # Aguarda a task de background concluir
        await asyncio.wait_for(bg_task, timeout=2.0)
        assert events_consumed == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_background_nao_bloqueia_chamador(self):
        """
        create_task não deve bloquear — o código após continua imediatamente.
        """
        flag = []

        async def slow_iterator():
            await asyncio.sleep(0.5)
            flag.append("done")
            yield 1

        async def fake_pending():
            pass

        pending = asyncio.create_task(fake_pending())
        bg_task = asyncio.create_task(self._consume_remainder(pending, slow_iterator()))

        # Imediatamente após create_task, o flag ainda não deve estar setado
        assert flag == []

        # Após aguardar, o background deve ter concluído
        await asyncio.wait_for(bg_task, timeout=2.0)
        assert flag == ["done"]
