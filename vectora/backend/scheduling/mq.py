"""Message queue distribuída, base para a API externa.

Fila de mensagens sobre **Redis Streams** com consumer groups
(``XADD``/``XREADGROUP``/``XACK``): entrega at-least-once, múltiplos
consumidores em réplicas distintas, redelivery de mensagens não-ackadas.

- ``MemoryMQ`` — fallback in-process (modo lite / testes): ``asyncio.Queue``
  por stream; sem redelivery entre processos (não há outros processos).
- ``RedisMQ`` — produção multi-réplica.

Nota: a fila de **embeddings** continua no ``src/services/queue.py`` (SQLite
no lite; Postgres ``SKIP LOCKED`` no complete — já é multi-réplica seguro).
Este módulo é a fundação para consumidores novos: webhooks de saída,
jobs da REST API v1 e tarefas assíncronas do agente.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Handler de consumo — recebe a mensagem; exceção ⇒ sem ACK (redelivery).
Handler = Callable[["StreamMessage"], Awaitable[None]]


@dataclass(frozen=True)
class StreamMessage:
    """Mensagem entregue ao consumidor."""

    id: str
    payload: dict


class MemoryMQ:
    """Backend in-process — sem persistência nem redelivery cross-process."""

    def __init__(self) -> None:
        self._streams: dict[str, asyncio.Queue[StreamMessage]] = {}

    def _queue(self, stream: str) -> asyncio.Queue[StreamMessage]:
        return self._streams.setdefault(stream, asyncio.Queue())

    async def enqueue(self, stream: str, payload: dict) -> str:
        msg = StreamMessage(id=uuid.uuid4().hex, payload=payload)
        await self._queue(stream).put(msg)
        return msg.id

    async def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        handler: Handler,
        *,
        stop_event: asyncio.Event | None = None,
        block_ms: int = 5000,
    ) -> None:
        """Consome até ``stop_event`` ser setado (ou para sempre)."""
        queue = self._queue(stream)
        while stop_event is None or not stop_event.is_set():
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=block_ms / 1000)
            except TimeoutError:
                continue
            try:
                await handler(msg)
            except Exception:
                logger.exception("mq[%s]: handler falhou — recolocando", stream)
                await queue.put(msg)
                await asyncio.sleep(1)

    async def close(self) -> None:
        self._streams.clear()


class NatsMQ:
    """Backend NATS JetStream — persistência em disco sem exigir Redis.

    Equivalente a ``RedisMQ`` (Redis Streams + consumer group), mas sobre
    JetStream: ``add_stream`` cria o stream sob demanda, ``pull_subscribe``
    com consumer durável dá o mesmo redelivery at-least-once (mensagem sem
    ACK volta a ser entregue).
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._nc: Any = None
        self._js: Any = None
        self._closing = False
        self._failed: bool = (
            False  # True após esgotar reconexões — força reset do singleton
        )

    def _is_connection_dead(self) -> bool:
        """True quando o cliente NATS fechou a conexão de vez.

        `_failed` cobre o caso em que `_closed_cb` já rodou; esta checagem
        cobre a janela entre o fechamento e o callback, em que `is_closed` já
        é verdadeiro mas o estado ainda não foi propagado.
        """
        nc = self._nc
        if nc is None:
            return True
        try:
            return bool(nc.is_closed)
        except Exception:
            return True

    async def _connect(self) -> Any:
        if self._failed:
            raise RuntimeError("mq: NATS permanentemente desconectado — use fallback")
        if self._js is None:
            import nats

            async def _error_cb(exc: Exception) -> None:
                logger.warning("mq: NATS erro de conexão: %s", exc)

            async def _disconnected_cb() -> None:
                if self._closing:
                    logger.info("mq: NATS desconectado durante shutdown")
                else:
                    logger.warning("mq: NATS desconectado do sidecar")

            async def _reconnected_cb() -> None:
                logger.info("mq: NATS reconectado ao sidecar")

            async def _closed_cb() -> None:
                # Chamado quando os retries se esgotam — marcamos como falho
                # para que a próxima operação force reset do singleton em get_mq().
                if self._closing:
                    logger.info("mq: NATS encerrado durante shutdown")
                else:
                    logger.warning(
                        "mq: NATS conexão encerrada após retries esgotados — fallback para memória"
                    )
                self._failed = True
                self._js = None
                reset_mq()

            self._nc = await nats.connect(
                self._url,
                max_reconnect_attempts=5,  # não infinito; após 5 falhas, fecha
                reconnect_time_wait=2,  # segundos entre tentativas
                connect_timeout=5,  # timeout da conexão inicial
                error_cb=_error_cb,
                disconnected_cb=_disconnected_cb,
                reconnected_cb=_reconnected_cb,
                closed_cb=_closed_cb,
            )
            self._js = self._nc.jetstream()
        return self._js

    async def enqueue(self, stream: str, payload: dict) -> str:
        js = await self._connect()
        await js.add_stream(name=stream, subjects=[stream])
        ack = await js.publish(
            stream, json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        return str(ack.seq)

    async def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        handler: Handler,
        *,
        stop_event: asyncio.Event | None = None,
        block_ms: int = 5000,
    ) -> None:
        js = await self._connect()
        await js.add_stream(name=stream, subjects=[stream])
        sub = await js.pull_subscribe(stream, durable=group)
        while stop_event is None or not stop_event.is_set():
            try:
                msgs = await sub.fetch(1, timeout=block_ms / 1000)
            except TimeoutError:
                continue
            except Exception as exc:
                # Conexão encerrada em definitivo (retries do cliente NATS já
                # esgotados, `_closed_cb` marcou `_failed`): a `sub` local está
                # presa a uma conexão morta e nunca mais vai funcionar. Sem
                # este corte o loop gira para sempre repetindo o mesmo warning
                # a cada 2s, o que soterra o log e esconde o erro real.
                if self._failed or self._is_connection_dead():
                    logger.warning(
                        "mq[%s]: conexão NATS encerrada em definitivo — consumo "
                        "parado, fila segue pelo fallback em memória",
                        stream,
                    )
                    return
                logger.warning(
                    "mq[%s]: fetch NATS falhou (%s) — retry em 2s", stream, exc
                )
                await asyncio.sleep(2)
                continue
            for msg in msgs:
                try:
                    payload = json.loads(msg.data.decode("utf-8"))
                except json.JSONDecodeError:
                    payload = {}
                try:
                    await handler(StreamMessage(id=str(msg.reply), payload=payload))
                    await msg.ack()
                except Exception:
                    logger.exception(
                        "mq[%s]: handler falhou (sem ACK, redelivery via NATS)", stream
                    )

    async def close(self) -> None:
        import contextlib

        self._closing = True
        if self._nc is not None:
            with contextlib.suppress(Exception):
                await self._nc.close()


class RedisMQ:
    """Backend Redis Streams com consumer groups (at-least-once)."""

    def __init__(self, url: str, *, maxlen: int = 10_000) -> None:
        import redis.asyncio as aredis

        self._redis = aredis.from_url(url, decode_responses=True)
        self._maxlen = maxlen

    async def enqueue(self, stream: str, payload: dict) -> str:
        msg_id = await self._redis.xadd(
            stream,
            {"payload": json.dumps(payload, ensure_ascii=False)},
            maxlen=self._maxlen,
            approximate=True,
        )
        return str(msg_id)

    async def _ensure_group(self, stream: str, group: str) -> None:
        try:
            await self._redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as exc:  # BUSYGROUP = grupo já existe
            if "BUSYGROUP" not in str(exc):
                raise

    async def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        handler: Handler,
        *,
        stop_event: asyncio.Event | None = None,
        block_ms: int = 5000,
        count: int = 10,
    ) -> None:
        """Loop de consumo: XREADGROUP → handler → XACK.

        Handler que lança exceção deixa a mensagem pendente no grupo —
        redelivery via ``XAUTOCLAIM`` fica a cargo de um reaper futuro ou de
        outro consumidor com o mesmo nome reiniciado.
        """
        await self._ensure_group(stream, group)
        while stop_event is None or not stop_event.is_set():
            try:
                batches: Any = await self._redis.xreadgroup(
                    group, consumer, {stream: ">"}, count=count, block=block_ms
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("mq[%s]: leitura falhou (%s) — retry em 2s", stream, exc)
                await asyncio.sleep(2)
                continue
            for _stream_name, messages in batches or []:
                for msg_id, fields in messages:
                    try:
                        payload = json.loads(fields.get("payload", "{}"))
                    except json.JSONDecodeError:
                        payload = {}
                    try:
                        await handler(StreamMessage(id=str(msg_id), payload=payload))
                        await self._redis.xack(stream, group, msg_id)
                    except Exception:
                        logger.exception(
                            "mq[%s]: handler falhou para %s (sem ACK)", stream, msg_id
                        )

    async def close(self) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            await self._redis.aclose()


MQ = MemoryMQ | RedisMQ | NatsMQ

_mq: MQ | None = None


async def get_mq() -> MQ:
    """Singleton da fila — Redis (Pro) → NATS (sidecar, default de todos) → memória.

    Antes, sem Redis a fila virava puramente em-memória (perde tudo ao
    reiniciar), independente de storage_mode — o sidecar NATS/JetStream dá
    persistência em disco pra TODO usuário, não só quem paga Redis.
    """
    global _mq
    if _mq is None:
        from backend.persistence.kv import redis_reachable
        from backend.settings import settings

        url = (settings.redis_url or "").strip()
        if settings.storage_mode == "complete" and url and redis_reachable(url):
            try:
                _mq = RedisMQ(url)
                logger.info("mq: backend Redis Streams")
            except Exception as exc:
                logger.warning("mq: Redis indisponível (%s) — tentando NATS", exc)

        if _mq is None:
            from backend.scheduling.nats_sidecar import ensure_nats_sidecar

            nats_url = await ensure_nats_sidecar()
            if nats_url:
                try:
                    _mq = NatsMQ(nats_url)
                    logger.info("mq: backend NATS JetStream (sidecar)")
                except Exception as exc:
                    logger.warning("mq: NATS indisponível (%s) — usando memória", exc)

        if _mq is None:
            _mq = MemoryMQ()
    return _mq


def mq_initialized() -> bool:
    """Sem efeito colateral — diferente de `get_mq()`, nunca cria a fila.
    Usado no shutdown: fechar algo que nunca chegou a ser inicializado só
    subiria um sidecar novo (NATS) do zero para imediatamente encerrá-lo."""
    return _mq is not None


def reset_mq() -> None:
    """Descarta o singleton — usado em testes."""
    global _mq
    _mq = None
