"""KV distribuído + pub/sub.

Abstração mínima de chave/valor com publish/subscribe para sincronizar caches
in-memory entre réplicas do backend:

- ``MemoryKV`` — default (modo lite / single-process). Pub/sub despacha para
  os subscribers do próprio processo; ``get/set`` usam um dict com TTL.
- ``RedisKV`` — ativado quando ``settings.redis_url`` está configurado. Usa
  ``redis.asyncio``; um reader task único atende todas as inscrições.

Os caches locais (``llm_tools._bound_cache``, ``plugins._mcp_tools_cache``,
``workspace._active``…) continuam sendo o L1 — o KV serve de invalidador L2
via pub/sub (ver ``cache_sync.py``), não de storage primário.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import re
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

#: Charset aceito por chave de bucket JetStream KV — qualquer outro
#: caractere (ex.: `:`, usado como separador em várias chamadas do resto do
#: backend, como `f"partial:{thread_id}"`) faz `kv.put`/`kv.get` do NATS
#: levantar `InvalidKeyError` em runtime. MemoryKV/RedisKV não têm essa
#: restrição — o bug só aparece quando o backend NATS está ativo (default
#: de toda instalação sem Redis configurado).
_NATS_KEY_UNSAFE = re.compile(r"[^-/_=a-zA-Z0-9]")
#: Prefixo de escape — precisa estar no charset aceito (por isso `.`, que
#: sobra livre já que não faz parte de `_NATS_KEY_UNSAFE` acima) e nunca
#: pode aparecer "cru" na saída, senão a codificação deixa de ser injetiva
#: (ver `_nats_safe_key`).
_NATS_KEY_ESCAPE = "."


def _nats_safe_key(key: str) -> str:
    """Codifica `key` pro charset aceito por chave JetStream KV de forma
    INJETIVA — chaves lógicas diferentes nunca colidem no mesmo registro.

    Uma substituição simples (ex.: todo caractere fora do charset vira
    `_`) não é injetiva: `"a:b"` e `"a_b"` colidiriam na mesma chave real
    do bucket, um `set` de uma sobrescrevendo silenciosamente o registro
    da outra. Aqui, cada byte UTF-8 de um caractere fora do charset (ou
    do próprio caractere de escape `.`, que senão criaria a mesma
    ambiguidade) vira `.XX` (hex de 2 dígitos) — só existe uma forma de
    decodificar essa sequência de volta (escaneando da esquerda: `.`
    sempre inicia um grupo de exatos 3 caracteres, qualquer outro
    caractere é literal), o que garante que strings de entrada diferentes
    produzem saídas diferentes.

    `errors="surrogatepass"`: um surrogate isolado (`"\\ud800"`, só
    aparece por engano — payload malformado do lado do Worker, nunca
    gerado por código nosso) faz `str.encode("utf-8")` sem esse argumento
    lançar `UnicodeEncodeError`, derrubando `set`/`get`/`delete` por
    completo em vez de só produzir uma chave esquisita porém válida."""
    out: list[str] = []
    for ch in key:
        if ch == _NATS_KEY_ESCAPE or _NATS_KEY_UNSAFE.match(ch):
            out.extend(
                f"{_NATS_KEY_ESCAPE}{b:02x}"
                for b in ch.encode("utf-8", errors="surrogatepass")
            )
        else:
            out.append(ch)
    return "".join(out)


# Resultado do probe por URL — process-lifetime, igual aos singletons que o
# consomem (get_kv/get_mq). Subir o Redis depois exige reiniciar o servidor.
_reachable_cache: dict[str, bool] = {}


def redis_reachable(url: str, timeout: float = 0.3) -> bool:
    """Probe TCP barato: o Redis em ``url`` está aceitando conexões?

    O ``redis_url`` agora vem preenchido por padrão (defaults.env espelha o
    deploy/compose.dev.yml), então os consumidores (KV, MQ, rate-limit) só
    podem ativar o backend Redis se o serviço estiver realmente de pé —
    caso contrário cada operação falharia em runtime no modo lite.
    """
    if url in _reachable_cache:
        return _reachable_cache[url]
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        with socket.create_connection((host, port), timeout=timeout):
            ok = True
    except OSError:
        ok = False
    _reachable_cache[url] = ok
    return ok


def reset_reachable_cache() -> None:
    """Limpa o cache do probe — usado em testes."""
    _reachable_cache.clear()


#: Callback de subscription — recebe o payload (str) publicado no canal.
Subscriber = Callable[[str], None] | Callable[[str], Awaitable[None]]


async def _dispatch(callback: Subscriber, payload: str) -> None:
    try:
        result = callback(payload)
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.exception("kv: subscriber falhou (payload descartado)")


class MemoryKV:
    """Backend in-memory — válido apenas para single-process (modo lite)."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float | None]] = {}
        self._subs: dict[str, list[Subscriber]] = {}

    async def get(self, key: str) -> str | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires = entry
        if expires is not None and time.monotonic() > expires:
            self._data.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, *, ttl_s: float | None = None) -> None:
        expires = time.monotonic() + ttl_s if ttl_s else None
        self._data[key] = (value, expires)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def publish(self, channel: str, payload: str) -> None:
        for callback in self._subs.get(channel, []):
            await _dispatch(callback, payload)

    def subscribe(self, channel: str, callback: Subscriber) -> None:
        self._subs.setdefault(channel, []).append(callback)

    async def start(self) -> None:  # paridade de interface com RedisKV
        return None

    async def close(self) -> None:
        self._data.clear()
        self._subs.clear()


class RedisKV:
    """Backend Redis — multi-réplica. Um único reader task de pub/sub."""

    def __init__(self, url: str) -> None:
        import redis.asyncio as aredis

        self._redis = aredis.from_url(url, decode_responses=True)
        self._subs: dict[str, list[Subscriber]] = {}
        self._pubsub: Any = None
        self._reader: asyncio.Task | None = None

    async def get(self, key: str) -> str | None:
        value = await self._redis.get(key)
        if value is None:
            return None
        return value if isinstance(value, str) else value.decode()

    async def set(self, key: str, value: str, *, ttl_s: float | None = None) -> None:
        await self._redis.set(key, value, ex=int(ttl_s) if ttl_s else None)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def publish(self, channel: str, payload: str) -> None:
        await self._redis.publish(channel, payload)

    def subscribe(self, channel: str, callback: Subscriber) -> None:
        self._subs.setdefault(channel, []).append(callback)

    async def start(self) -> None:
        """Inscreve nos canais registrados e inicia o reader task."""
        if not self._subs or self._reader is not None:
            return
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(*self._subs.keys())
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                channel = str(message.get("channel", ""))
                payload = str(message.get("data", ""))
                for callback in self._subs.get(channel, []):
                    await _dispatch(callback, payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("kv: reader pub/sub encerrou com erro: %s", exc)

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
        with contextlib.suppress(Exception):
            await self._redis.aclose()


class NatsKV:
    """Backend NATS — bucket JetStream KV (get/set/delete) + pub/sub core.

    Equivalente a ``RedisKV``, mas sobre o sidecar NATS/JetStream embutido —
    disponível pra TODO usuário (não só quem paga Redis), com persistência em
    disco via o bucket JetStream.
    """

    _BUCKET = "vectora_kv"

    def __init__(self, url: str) -> None:
        self._url = url
        self._nc: Any = None
        self._kv: Any = None
        self._subs: dict[str, list[Subscriber]] = {}
        self._reader: asyncio.Task | None = None
        self._closing = False
        self._failed: bool = (
            False  # True após esgotar reconexões — força reset do singleton
        )

    async def _connect(self) -> Any:
        if self._failed:
            raise RuntimeError("kv: NATS permanentemente desconectado — use fallback")
        if self._kv is None:
            import nats

            async def _error_cb(exc: Exception) -> None:
                logger.warning("kv: NATS erro de conexão: %s", exc)

            async def _disconnected_cb() -> None:
                if self._closing:
                    logger.info("kv: NATS desconectado durante shutdown")
                else:
                    logger.warning("kv: NATS desconectado do sidecar")

            async def _reconnected_cb() -> None:
                logger.info("kv: NATS reconectado ao sidecar")

            async def _closed_cb() -> None:
                # Chamado quando os retries se esgotam — marcamos como falho
                # para que a próxima operação force reset do singleton em get_kv().
                if self._closing:
                    logger.info("kv: NATS encerrado durante shutdown")
                else:
                    logger.warning(
                        "kv: NATS conexão encerrada após retries esgotados — fallback para memória"
                    )
                self._failed = True
                self._kv = None
                reset_kv()

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
            js = self._nc.jetstream()
            try:
                self._kv = await js.key_value(self._BUCKET)
            except Exception:
                self._kv = await js.create_key_value(bucket=self._BUCKET)
        return self._kv

    async def get(self, key: str) -> str | None:
        kv = await self._connect()
        try:
            entry = await kv.get(_nats_safe_key(key))
        except Exception:
            return None
        return entry.value.decode("utf-8") if entry and entry.value else None

    async def set(self, key: str, value: str, *, ttl_s: float | None = None) -> None:
        # ttl_s ignorado — JetStream KV usa TTL de bucket, não por chave;
        # os usos atuais (invalidação de cache L2) toleram entradas sem TTL.
        kv = await self._connect()
        await kv.put(_nats_safe_key(key), value.encode("utf-8"))

    async def delete(self, key: str) -> None:
        kv = await self._connect()
        with contextlib.suppress(Exception):
            await kv.delete(_nats_safe_key(key))

    async def publish(self, channel: str, payload: str) -> None:
        await self._connect()
        await self._nc.publish(channel, payload.encode("utf-8"))

    def subscribe(self, channel: str, callback: Subscriber) -> None:
        self._subs.setdefault(channel, []).append(callback)

    async def start(self) -> None:
        if not self._subs or self._reader is not None:
            return
        await self._connect()
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            subs = [await self._nc.subscribe(channel) for channel in self._subs]
            async for msg in _merge_subscriptions(subs):
                payload = msg.data.decode("utf-8")
                for callback in self._subs.get(msg.subject, []):
                    await _dispatch(callback, payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("kv: reader NATS encerrou com erro: %s", exc)

    async def close(self) -> None:
        self._closing = True
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        if self._nc is not None:
            with contextlib.suppress(Exception):
                await self._nc.close()


async def _merge_subscriptions(subs: list[Any]) -> Any:
    """Intercala mensagens de várias subscriptions NATS num único async generator."""
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def _pump(sub: Any) -> None:
        async for msg in sub.messages:
            await queue.put(msg)

    tasks = [asyncio.create_task(_pump(sub)) for sub in subs]
    try:
        while True:
            yield await queue.get()
    finally:
        for t in tasks:
            t.cancel()


KV = MemoryKV | RedisKV | NatsKV

_kv: KV | None = None


async def get_kv() -> KV:
    """Singleton do KV — Redis (Pro) → NATS (sidecar, default de todos) → memória."""
    global _kv
    if _kv is None:
        from backend.settings import settings

        url = (settings.redis_url or "").strip()
        from backend.services.license import get_effective_storage_mode

        if get_effective_storage_mode() == "complete" and url and redis_reachable(url):
            try:
                _kv = RedisKV(url)
                logger.info("kv: backend Redis (%s)", url.split("@")[-1])
            except Exception as exc:
                logger.warning("kv: Redis indisponível (%s) — tentando NATS", exc)

        if _kv is None:
            from backend.scheduling.nats_sidecar import ensure_nats_sidecar

            nats_url = await ensure_nats_sidecar()
            if nats_url:
                try:
                    _kv = NatsKV(nats_url)
                    logger.info("kv: backend NATS (sidecar)")
                except Exception as exc:
                    logger.warning("kv: NATS indisponível (%s) — usando memória", exc)

        if _kv is None:
            _kv = MemoryKV()
    return _kv


def kv_initialized() -> bool:
    """Sem efeito colateral — diferente de `get_kv()`, nunca cria o KV. Usado
    no shutdown: fechar algo que nunca chegou a ser inicializado só subiria
    um sidecar novo (NATS) do zero para imediatamente encerrá-lo."""
    return _kv is not None


def reset_kv() -> None:
    """Descarta o singleton — usado em testes."""
    global _kv
    _kv = None


#: Mantém referências fortes às tasks de publish disparadas em background; sem
#: isso o GC pode coletar a task antes de ela completar (RUF006).
_background_tasks: set[asyncio.Task] = set()


async def _publish_via_kv(channel: str, payload: str) -> None:
    kv = await get_kv()
    await kv.publish(channel, payload)


def publish_soon(channel: str, payload: str) -> None:
    """Publica sem bloquear, a partir de código síncrono.

    Os bumps de versão (plugins/tool_policy/workspace) são funções sync; este
    helper agenda o publish no event loop corrente. Sem loop (CLI puro), o
    publish é um no-op — nesse modo não há outras réplicas para avisar.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_publish_via_kv(channel, payload))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
