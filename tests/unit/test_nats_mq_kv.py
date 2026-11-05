"""NatsMQ/NatsKV (D3) — mesma interface de RedisMQ/RedisKV, sobre JetStream mockado.

``nats`` (nats-py) não roda contra um servidor real nestes testes — o
cliente/JetStream context são mockados, verificando só o contrato da
interface (mesmo formato que MemoryMQ/RedisMQ e MemoryKV/RedisKV já seguem).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.persistence.kv import NatsKV
from backend.scheduling.mq import NatsMQ, StreamMessage


@pytest.mark.asyncio
async def test_nats_mq_enqueue_publishes_to_jetstream():
    fake_js = MagicMock()
    fake_js.add_stream = AsyncMock()
    fake_js.publish = AsyncMock(return_value=MagicMock(seq=42))
    fake_nc = MagicMock()
    fake_nc.jetstream = MagicMock(return_value=fake_js)

    mq = NatsMQ("nats://127.0.0.1:4222")
    with patch("nats.connect", new=AsyncMock(return_value=fake_nc)):
        msg_id = await mq.enqueue("jobs", {"a": 1})

    assert msg_id == "42"
    fake_js.add_stream.assert_awaited_once()
    fake_js.publish.assert_awaited_once_with(
        "jobs", json.dumps({"a": 1}).encode("utf-8")
    )


@pytest.mark.asyncio
async def test_nats_mq_consume_acks_on_success_and_dispatches_payload():
    fake_msg = MagicMock()
    fake_msg.data = json.dumps({"x": 1}).encode("utf-8")
    fake_msg.reply = "reply-1"
    fake_msg.ack = AsyncMock()

    fake_sub = MagicMock()
    fake_sub.fetch = AsyncMock(side_effect=[[fake_msg], TimeoutError])

    fake_js = MagicMock()
    fake_js.add_stream = AsyncMock()
    fake_js.pull_subscribe = AsyncMock(return_value=fake_sub)
    fake_nc = MagicMock()
    fake_nc.jetstream = MagicMock(return_value=fake_js)

    import asyncio

    received: list[StreamMessage] = []
    stop = asyncio.Event()

    async def handler(msg: StreamMessage) -> None:
        received.append(msg)
        stop.set()

    mq = NatsMQ("nats://127.0.0.1:4222")
    with patch("nats.connect", new=AsyncMock(return_value=fake_nc)):
        await mq.consume("jobs", "g", "c1", handler, stop_event=stop, block_ms=10)

    assert len(received) == 1
    assert received[0].payload == {"x": 1}
    fake_msg.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_nats_kv_get_set_delete_round_trip():
    fake_entry = MagicMock(value=b"valor")
    fake_bucket = MagicMock()
    fake_bucket.get = AsyncMock(return_value=fake_entry)
    fake_bucket.put = AsyncMock()
    fake_bucket.delete = AsyncMock()

    fake_js = MagicMock()
    fake_js.key_value = AsyncMock(return_value=fake_bucket)
    fake_nc = MagicMock()
    fake_nc.jetstream = MagicMock(return_value=fake_js)

    kv = NatsKV("nats://127.0.0.1:4222")
    with patch("nats.connect", new=AsyncMock(return_value=fake_nc)):
        await kv.set("chave", "valor")
        result = await kv.get("chave")
        await kv.delete("chave")

    assert result == "valor"
    fake_bucket.put.assert_awaited_once_with("chave", b"valor")
    fake_bucket.delete.assert_awaited_once_with("chave")


@pytest.mark.asyncio
async def test_nats_kv_get_returns_none_on_miss():
    fake_bucket = MagicMock()
    fake_bucket.get = AsyncMock(side_effect=Exception("key not found"))

    fake_js = MagicMock()
    fake_js.key_value = AsyncMock(return_value=fake_bucket)
    fake_nc = MagicMock()
    fake_nc.jetstream = MagicMock(return_value=fake_js)

    kv = NatsKV("nats://127.0.0.1:4222")
    with patch("nats.connect", new=AsyncMock(return_value=fake_nc)):
        result = await kv.get("nao-existe")

    assert result is None


@pytest.mark.asyncio
async def test_nats_kv_sanitiza_dois_pontos_na_chave() -> None:
    """Bug real: `f"partial:{thread_id}"` (usado em native_stream.py/
    threads.py) e `f"watch:{channel}:{workspace_id}"` (file_watcher.py)
    usam `:` como separador — o bucket JetStream KV real rejeita esse
    caractere com `InvalidKeyError` (charset aceito:
    `[-/_=.a-zA-Z0-9]`), então todo save de preview parcial de streaming
    falhava silenciosamente (só um WARNING no log). A chave chega
    codificada no `put`/`get`/`delete` reais (`:` → `.3a`, hex do byte
    UTF-8), sem precisar caçar cada call-site do resto do backend que
    usa `:`."""
    fake_entry = MagicMock(value=b"valor")
    fake_bucket = MagicMock()
    fake_bucket.get = AsyncMock(return_value=fake_entry)
    fake_bucket.put = AsyncMock()
    fake_bucket.delete = AsyncMock()

    fake_js = MagicMock()
    fake_js.key_value = AsyncMock(return_value=fake_bucket)
    fake_nc = MagicMock()
    fake_nc.jetstream = MagicMock(return_value=fake_js)

    kv = NatsKV("nats://127.0.0.1:4222")
    thread_id = "ae535959-ec7f-41d6-a423-3c4d188c4b9d"
    with patch("nats.connect", new=AsyncMock(return_value=fake_nc)):
        await kv.set(f"partial:{thread_id}", "conteudo parcial")
        await kv.get(f"partial:{thread_id}")
        await kv.delete(f"partial:{thread_id}")

    expected = f"partial.3a{thread_id}"
    fake_bucket.put.assert_awaited_once_with(expected, b"conteudo parcial")
    fake_bucket.get.assert_awaited_once_with(expected)
    fake_bucket.delete.assert_awaited_once_with(expected)


@pytest.mark.asyncio
async def test_erro_borda_chave_sem_caractere_invalido_passa_intacta() -> None:
    """Chaves já válidas E sem o caractere de escape (`.`) não podem ser
    alteradas pela codificação — round-trip continua idêntico. (Chaves
    válidas que já usam `.` são cobertas por
    `test_erro_borda_ponto_literal_tambem_e_escapado`, já que `.` é o
    prefixo de escape aqui.)"""
    fake_entry = MagicMock(value=b"valor")
    fake_bucket = MagicMock()
    fake_bucket.get = AsyncMock(return_value=fake_entry)
    fake_bucket.put = AsyncMock()

    fake_js = MagicMock()
    fake_js.key_value = AsyncMock(return_value=fake_bucket)
    fake_nc = MagicMock()
    fake_nc.jetstream = MagicMock(return_value=fake_js)

    kv = NatsKV("nats://127.0.0.1:4222")
    with patch("nats.connect", new=AsyncMock(return_value=fake_nc)):
        await kv.set("valida-chave_123/xy", "valor")
        await kv.get("valida-chave_123/xy")

    fake_bucket.put.assert_awaited_once_with("valida-chave_123/xy", b"valor")
    fake_bucket.get.assert_awaited_once_with("valida-chave_123/xy")


@pytest.mark.asyncio
async def test_erro_borda_ponto_literal_tambem_e_escapado() -> None:
    """`.` é o prefixo de escape da codificação — se passasse intacto,
    `"a:b"` (`:` → `.3a`, virando `"a.3ab"`) colidiria com a chave
    literal `"a.3ab"` (que já é válida por si só). Escapar `.` também
    (`.2e`) elimina essa ambiguidade."""
    from backend.persistence.kv import _nats_safe_key

    assert _nats_safe_key(".") == ".2e"
    assert _nats_safe_key("a.3ab") != _nats_safe_key("a:b")


@pytest.mark.asyncio
async def test_erro_borda_codificacao_e_injetiva_mesmo_para_chaves_que_colidiriam_com_substituicao_simples() -> (
    None
):
    """Regressão do achado do CodeRabbit: uma substituição simples (todo
    caractere inválido vira `_`) faria `"a:b"` e `"a_b"` colidirem na
    MESMA chave do bucket real — um `set` de uma sobrescreveria
    silenciosamente o registro da outra. A codificação atual (`:` →
    `.3a`) nunca produz esse tipo de colisão."""
    from backend.persistence.kv import _nats_safe_key

    assert _nats_safe_key("a:b") != _nats_safe_key("a_b")
    # Sanidade adicional: chaves visivelmente diferentes nunca colidem.
    keys = ["a:b", "a_b", "a.b", "watch:x:y", "watch_x_y", "partial:1", "partial.1"]
    encoded = [_nats_safe_key(k) for k in keys]
    assert len(set(encoded)) == len(keys)


@pytest.mark.asyncio
async def test_erro_borda_surrogate_isolado_nao_lanca_unicodeencodeerror() -> None:
    """Achado do CodeRabbit: `"\\ud800"` (surrogate isolado — só chega por
    payload malformado do Worker, nunca gerado por código nosso) faz
    `str.encode("utf-8")` sem `errors="surrogatepass"` lançar
    `UnicodeEncodeError`, derrubando `NatsKV.set` por completo (`get`
    passaria a devolver `None` sempre — não porque a chave não existe,
    mas porque a sanitização quebra antes de tentar o bucket real)."""
    from backend.persistence.kv import _nats_safe_key

    encoded = _nats_safe_key("partial:\ud800")
    assert encoded.startswith("partial.3a")
    # 3 bytes UTF-8 do surrogate isolado (via surrogatepass), cada um
    # virando seu próprio grupo ".XX" — determinístico e sem exceção.
    assert encoded == "partial.3a.ed.a0.80"


@pytest.mark.asyncio
async def test_erro_borda_kv_set_com_surrogate_isolado_nao_propaga_excecao() -> None:
    fake_bucket = MagicMock()
    fake_bucket.put = AsyncMock()

    fake_js = MagicMock()
    fake_js.key_value = AsyncMock(return_value=fake_bucket)
    fake_nc = MagicMock()
    fake_nc.jetstream = MagicMock(return_value=fake_js)

    kv = NatsKV("nats://127.0.0.1:4222")
    with patch("nats.connect", new=AsyncMock(return_value=fake_nc)):
        await kv.set("partial:\ud800", "conteudo")  # não deve lançar

    fake_bucket.put.assert_awaited_once()
