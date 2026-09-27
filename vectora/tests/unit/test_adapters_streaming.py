"""Streaming token-a-token de ``stream_engine_events`` — garante que cada
``MessageChunk`` vira um ``TokenEvent`` imediatamente, em ordem, um por
chunk, sem buffering.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.api.native_stream import stream_engine_events
from backend.engine.stream_events import MessageChunk


@pytest.fixture(autouse=True)
def _isolated(_no_thread_persistence):
    pass


def _parse(sse: str) -> dict:
    """Decodifica uma linha SSE ``data: {...}\\n\\n`` em dict."""
    assert sse.startswith("data: ")
    return json.loads(sse[len("data: ") :].strip())


def _token_run(texts: list[str], *, node: str = "model"):
    async def run(on_event):
        for text in texts:
            await on_event(MessageChunk(content=text, node=node))
        return "stop"

    return run


def _empty_run():
    async def run(on_event):
        return "stop"

    return run


@pytest.mark.asyncio
async def test_each_chunk_emits_one_token_in_order():
    run = _token_run(["Hello", " ", "world"])
    out = [_parse(s) async for s in stream_engine_events(run, thread_id="tid")]

    # 1º = thread, último = done; no meio, exatamente 3 tokens em ordem.
    assert out[0]["type"] == "thread"
    assert out[-1]["type"] == "done"
    tokens = [e for e in out if e["type"] == "token"]
    assert [t["content"] for t in tokens] == ["Hello", " ", "world"]


@pytest.mark.asyncio
async def test_thread_event_carries_resolved_workspace_id():
    """Frontend sincroniza o seletor de workspace a partir desse campo (ver
    use-stream-handler.ts) — sem ele, um workspace criado via
    ChatConfig.create_new_workspace nunca aparece na UI. Par de erro: sem
    workspace_id passado, o evento vem com string vazia (nunca None/ausente —
    o frontend faz `if (event.workspace_id)`)."""
    out = [
        _parse(s)
        async for s in stream_engine_events(
            _empty_run(), thread_id="tid", workspace_id="ws-123"
        )
    ]
    assert out[0]["type"] == "thread"
    assert out[0]["thread_id"] == "tid"
    assert out[0]["workspace_id"] == "ws-123"
    assert out[0]["run_id"]

    out_empty = [
        _parse(s) async for s in stream_engine_events(_empty_run(), thread_id="tid")
    ]
    assert out_empty[0]["workspace_id"] == ""


@pytest.mark.asyncio
async def test_tokens_are_incremental_not_buffered():
    """Dirige o gerador passo a passo: token N sai ANTES do chunk N+1 entrar."""
    pulled: list[str] = []

    async def run(on_event):
        for text in ["um", "dois", "tres"]:
            pulled.append(text)
            await on_event(MessageChunk(content=text, node="model"))
        return "stop"

    gen = stream_engine_events(run, thread_id="tid")

    first = _parse(await gen.__anext__())
    assert first["type"] == "thread"

    tok1 = _parse(await gen.__anext__())
    assert tok1 == {"type": "token", "content": "um", "node": "model"}

    tok2 = _parse(await gen.__anext__())
    assert tok2["content"] == "dois"

    tok3 = _parse(await gen.__anext__())
    assert tok3["content"] == "tres"

    done = _parse(await gen.__anext__())
    assert done["type"] == "done"


@pytest.mark.asyncio
async def test_empty_chunk_still_emits_token_event():
    """O bridge SSE não filtra ``MessageChunk`` vazio — mapeia pra
    ``TokenEvent(content="")`` como qualquer outro chunk (o motor nativo não
    filtra deltas vazios do provider antes de emitir; o adaptador antigo
    descartava explicitamente esse caso, o nativo não).
    Inofensivo no frontend (content="" concatenado não muda nada), mas o
    comportamento real precisa ficar documentado aqui."""
    run = _token_run([""])
    out = [_parse(s) async for s in stream_engine_events(run, thread_id="tid")]
    tokens = [e for e in out if e["type"] == "token"]
    assert tokens == [{"type": "token", "content": "", "node": "model"}]


@pytest.mark.asyncio
async def test_first_token_marks_thread_as_having_real_content():
    """Thread só deve virar visível em ListThreads quando o LLM de fato produz
    conteúdo — não quando o turno só começa. Regressão do bug de sessão
    fantasma: `_increment_message_count` disparava antes do primeiro token,
    então uma falha de quota logo no início (429) já deixava a thread
    marcada como "real" (message_count=1) sem nenhuma resposta."""
    run = _token_run(["oi", " tudo bem?"])
    with patch(
        "backend.api.handlers.threads._increment_message_count",
        new=AsyncMock(),
    ) as mock_increment:
        _ = [_parse(s) async for s in stream_engine_events(run, thread_id="tid-123")]
        # A marcação é fire-and-forget (asyncio.ensure_future, não await) pra
        # não introduzir um ponto de suspensão real no meio do streaming de
        # tokens — dar 1 tick ao loop pra deixar a task agendada completar,
        # ainda com o patch ativo.
        await asyncio.sleep(0)

    mock_increment.assert_awaited_once_with("tid-123")


@pytest.mark.asyncio
async def test_no_token_never_marks_thread_as_having_content():
    """Par de erro do teste acima: turno que nunca produz token (ex.: erro de
    quota antes do 1º chunk) NÃO deve marcar a thread como tendo conteúdo
    real — senão ela vira sessão fantasma na sidebar."""
    with patch(
        "backend.api.handlers.threads._increment_message_count",
        new=AsyncMock(),
    ) as mock_increment:
        _ = [
            _parse(s)
            async for s in stream_engine_events(_empty_run(), thread_id="tid-456")
        ]
        await asyncio.sleep(0)

    mock_increment.assert_not_awaited()


@pytest.mark.asyncio
async def test_stops_consuming_events_when_client_disconnects_mid_stream(
    _no_nats_sidecar,
):
    """Regressão: cancelar o fetch no cliente não tinha efeito enquanto o
    modelo estivesse "pensando" sem produzir token — o backend seguia
    rodando até o próximo evento aparecer sozinho. A checagem de desconexão
    corre em paralelo ao consumo de cada evento (asyncio.wait/
    FIRST_COMPLETED), não só depois que um evento chegar.

    A resposta ao cliente termina em ``done`` assim que a desconexão é
    confirmada — mas o generator NÃO é fechado: é transferido pra uma task
    de background, pra manter a sessão viva mesmo depois do cliente sair.
    """
    torn_down = {"value": False}

    async def run(on_event):
        try:
            await on_event(MessageChunk(content="um", node="model"))
            # Modelo "pensando" — nunca produz o próximo token sozinho; só a
            # checagem de desconexão deve tirar o consumidor daqui. Curto de
            # propósito (não 10s reais) pra não deixar uma task pendurada no
            # loop do teste por mais tempo que o necessário — limpa via
            # `pending` logo abaixo de qualquer forma. Folga confortável
            # sobre _DISCONNECT_CONFIRM_DELAY_S (0.3s) pra provar que a
            # desconexão corta o consumo ANTES do sleep terminar sozinho.
            await asyncio.sleep(2)
            await on_event(MessageChunk(content="nunca chega", node="model"))
            return "stop"
        finally:
            torn_down["value"] = True

    request = MagicMock()
    # 1ª chamada (antes do 1º evento): ainda conectado. Da 2ª em diante
    # (enquanto aguarda o evento que nunca chega): desconectado.
    request.is_disconnected = AsyncMock(side_effect=[False, True, True, True])

    tasks_before = asyncio.all_tasks()
    out = [
        _parse(s)
        async for s in stream_engine_events(run, thread_id="tid", http_request=request)
    ]

    tokens = [e for e in out if e["type"] == "token"]
    assert [t["content"] for t in tokens] == ["um"]
    assert out[-1]["type"] == "done"
    # O generator segue vivo em background (não foi fechado) — ainda está
    # bloqueado no `asyncio.sleep(2)`, então o `finally` ainda não rodou.
    assert torn_down["value"] is False

    # Limpeza: espera a task de background (run_task, nunca cancelada de
    # propósito numa desconexão real) terminar sozinha, sem deixar nada
    # pendurado no event loop pro próximo teste.
    for task in asyncio.all_tasks() - tasks_before:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(task), timeout=1)


@pytest.mark.asyncio
async def test_survives_false_positive_disconnect_reading():
    """Regressão: um único ``is_disconnected() == True`` isolado (ruído —
    ex.: possível artefato do BaseHTTPMiddleware de auth envolvendo a
    StreamingResponse) não pode cortar um stream que o cliente nunca largou
    de verdade. A confirmação (2ª leitura) deve negar o falso positivo e o
    stream deve seguir entregando todos os eventos.
    """
    torn_down = {"value": False}

    async def run(on_event):
        try:
            await on_event(MessageChunk(content="um", node="model"))
            await asyncio.sleep(0.05)
            await on_event(MessageChunk(content="dois", node="model"))
            return "stop"
        finally:
            torn_down["value"] = True

    request = MagicMock()
    # 1 leitura positiva isolada seguida de negativas — a confirmação (leitura
    # extra logo depois) deve resolver como "ainda conectado" e o loop deve
    # seguir normalmente.
    request.is_disconnected = AsyncMock(
        side_effect=[False, True, False, False, False, False, False, False]
    )

    out = [
        _parse(s)
        async for s in stream_engine_events(run, thread_id="tid", http_request=request)
    ]

    tokens = [e for e in out if e["type"] == "token"]
    assert [t["content"] for t in tokens] == ["um", "dois"]
    assert out[-1]["type"] == "done"
    # Terminou pelo fim natural do generator, não por cancelamento.
    assert torn_down["value"] is True


@pytest.mark.asyncio
async def test_provider_error_midstream_becomes_clean_rate_limit():
    """429 no meio do stream vira ErrorEvent limpo (RATE_LIMIT), não JSON cru."""

    async def run(on_event):
        await on_event(MessageChunk(content="parcial", node="model"))
        raise RuntimeError(
            "Error calling model 'gemini-2.5-flash' (Too Many Requests): 429 "
            "RESOURCE_EXHAUSTED quota exceeded"
        )

    out = [_parse(s) async for s in stream_engine_events(run, thread_id="tid")]
    errors = [e for e in out if e["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == "RATE_LIMIT"
    assert "RESOURCE_EXHAUSTED" not in errors[0]["message"]
    assert "429" not in errors[0]["message"]


# ============================================================================
# _record_turn_checkpoint
# ============================================================================


@pytest.mark.asyncio
async def test_record_turn_checkpoint_nonexistent_directory_no_error(
    tmp_path, monkeypatch, caplog
):
    """_record_turn_checkpoint não loga ERROR quando diretório do workspace não existe.

    Regressão do NoSuchPathError — o diretório referenciado pelo workspace pode
    não existir em disco (sessão nova ainda não inicializada). Antes do fix, a
    exceção não era capturada pelo inner except (só pegava InvalidGitRepositoryError)
    e chegava ao outer except como ERROR. Após o fix deve ser silenciosa.
    """
    import logging
    from unittest.mock import MagicMock

    from backend.api.adapters import _record_turn_checkpoint

    ws = MagicMock()
    ws.cwd = str(tmp_path / "nonexistent_workspace_dir_xyz")  # não existe

    monkeypatch.setattr(
        "backend.workspace.workspace.workspace_registry",
        MagicMock(get=MagicMock(return_value=ws)),
    )

    with caplog.at_level(logging.ERROR, logger="backend.api.adapters"):
        await _record_turn_checkpoint("ws-1", "thread-1", {"run_id": "r1"})

    error_msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert not error_msgs, (
        f"_record_turn_checkpoint logou ERROR para diretório inexistente: {error_msgs}"
    )


@pytest.mark.asyncio
async def test_record_turn_checkpoint_nonexistent_directory_returns_silently(
    tmp_path, monkeypatch
):
    """_record_turn_checkpoint retorna sem levantar exceção se diretório não existe."""
    from unittest.mock import MagicMock

    from backend.api.adapters import _record_turn_checkpoint

    ws = MagicMock()
    ws.cwd = str(tmp_path / "also_nonexistent_xyz")

    monkeypatch.setattr(
        "backend.workspace.workspace.workspace_registry",
        MagicMock(get=MagicMock(return_value=ws)),
    )

    # Deve retornar sem levantar — best-effort
    await _record_turn_checkpoint("ws-2", "thread-2", {"run_id": "r2"})
