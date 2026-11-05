"""E2E do pipeline de streaming nativo: run_conversation → stream_engine_events
→ to_sse_line — o caminho de produção real de StreamChat (``backend/api/
handlers/chat.py``), substituindo o adaptador de streaming antigo.

Histórico do bug que esta suíte protege: o dedup de token no streaming já foi
"corrigido" 3 vezes no pipeline antigo antes de ficar de fato resolvido — cada
correção anterior tinha teste unitário verde e ainda assim reintroduzia
duplicação em produção. A lição fixada no projeto é que só um teste E2E do
pipeline real (``ChatClient.astream`` real → ``run_conversation`` real →
``stream_engine_events`` real → linhas SSE) prova a ausência do bug; mocks
isolados de cada camada não pegam a interação entre elas.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from itertools import pairwise

import pytest

from backend.api.native_stream import stream_engine_events
from backend.engine.conversation_loop import LoopConfig, run_conversation
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.context import ToolContext
from backend.tools.registry import ToolRegistry
from backend.vtypes.message import MessageRole, VMessageChunk, text_message


@pytest.fixture
async def session_store(tmp_path):
    pool = AsyncConnectionPool(str(tmp_path / "dedup.db"), min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    try:
        yield store
    finally:
        await pool.close()


class _RealTokenStreamChatClient:
    """``ChatClient`` real (satisfaz o Protocol, não um mock) — streama um
    texto token a token, exatamente como um provider real faria."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens

    async def astream(
        self, messages, *, tools=None, temperature=None, max_tokens=None
    ) -> AsyncIterator[VMessageChunk]:
        for token in self._tokens:
            yield VMessageChunk(delta_text=token)

    async def agenerate(
        self, messages, *, tools=None, temperature=None, max_tokens=None
    ):
        msg = "não usado (astream-only)"
        raise NotImplementedError(msg)


def _tokenize(text: str) -> list[str]:
    """Quebra em tokens do jeito que um provider real fragmenta — por
    palavra, preservando espaços como tokens próprios (não colapsa nada)."""
    tokens: list[str] = []
    buf = ""
    for ch in text:
        if ch == " ":
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(" ")
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    return tokens


def _parse(sse: str) -> dict:
    assert sse.startswith("data: ")
    return json.loads(sse[len("data: ") :].strip())


async def _run_pipeline(session_store: SessionStore, text: str) -> list[dict]:
    """Roda o pipeline de produção completo: persiste a mensagem do usuário,
    chama ``run_conversation`` de verdade com um ``ChatClient`` real (não
    mock), e coleta as linhas SSE via ``stream_engine_events`` — o mesmo
    bridge que ``stream_chat`` usa em produção."""
    thread_id = "thread-dedup-e2e"
    await session_store.create_session(thread_id, user_id="local")
    await session_store.append_message(thread_id, text_message(MessageRole.USER, "oi"))

    chat_client = _RealTokenStreamChatClient(_tokenize(text))
    tool_registry = ToolRegistry()
    ctx = ToolContext(user_id="local")

    async def _run(on_event) -> str:
        result = await run_conversation(
            session_store=session_store,
            chat_client=chat_client,
            tool_registry=tool_registry,
            ctx=ctx,
            thread_id=thread_id,
            config=LoopConfig(max_iterations=5),
            on_event=on_event,
        )
        return result.stopped_reason

    lines = [
        line
        async for line in stream_engine_events(
            _run, thread_id=thread_id, user_id="local"
        )
    ]
    return [_parse(s) for s in lines]


def _token_contents(out: list[dict]) -> list[str]:
    return [e["content"] for e in out if e["type"] == "token"]


class TestNativeStreamDedupE2E:
    async def test_cada_token_aparece_exatamente_uma_vez(
        self, session_store: SessionStore
    ) -> None:
        out = await _run_pipeline(session_store, "Olá tudo bem")
        tokens = _token_contents(out)
        assert "".join(tokens) == "Olá tudo bem"
        assert tokens == ["Olá", " ", "tudo", " ", "bem"], (
            f"tokens duplicados ou fora de ordem: {tokens}"
        )

    async def test_resposta_de_um_unico_token(
        self, session_store: SessionStore
    ) -> None:
        out = await _run_pipeline(session_store, "Oi")
        assert _token_contents(out) == ["Oi"]

    async def test_texto_longo_sem_duplicacao(
        self, session_store: SessionStore
    ) -> None:
        words = " ".join(f"palavra{i}" for i in range(60))
        out = await _run_pipeline(session_store, words)
        joined = "".join(_token_contents(out))
        assert joined == words
        toks = _token_contents(out)
        assert all(a != b for a, b in pairwise(toks) if a.strip())

    async def test_unicode_emoji_acentos_intactos(
        self, session_store: SessionStore
    ) -> None:
        text = "Café ☕ com açúcar 🍬 e pão 🥖 — tudo ótimo!"
        out = await _run_pipeline(session_store, text)
        assert "".join(_token_contents(out)) == text

    async def test_envelope_sse_thread_start_e_done_presentes_uma_vez(
        self, session_store: SessionStore
    ) -> None:
        out = await _run_pipeline(session_store, "resposta simples")
        assert out[0]["type"] == "thread"
        assert sum(1 for e in out if e["type"] == "thread") == 1
        assert sum(1 for e in out if e["type"] == "done") == 1
        assert out[-1]["type"] == "done"

    async def test_resposta_vazia_nao_emite_token(
        self, session_store: SessionStore
    ) -> None:
        out = await _run_pipeline(session_store, "")
        assert _token_contents(out) == []
        assert out[0]["type"] == "thread"
        assert out[-1]["type"] == "done"
