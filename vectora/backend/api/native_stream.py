"""Bridge entre o motor nativo (``backend/engine/conversation_loop.py``) e o
contrato SSE de produção — origem do stream de ``StreamChat``/``ResumeChat``.
``backend/engine/sse_adapter.py`` garante o mapeamento 1:1 de cada
``EngineEvent`` pro schema Pydantic servido ao frontend.

Efeitos colaterais do turno (checkpoint de rewind, persistência de conteúdo
parcial no KV, gatilho do Remember, registro de delegação de subagente na
aba Tarefas, streaming ao vivo da tool `terminal`, detecção de desconexão do
cliente) são tratados aqui em cima do vocabulário nativo — a lógica de cada
efeito em si vem de ``backend/api/adapters.py`` (mesmas funções, sem
duplicar).
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from backend.api.adapters import (
    _mark_thread_has_content,
    _pre_approved,
    _record_turn_checkpoint,
    classify_stream_error,
)
from backend.api.schemas import (
    DoneEvent,
    ErrorEvent,
    TerminalLineEvent,
    ThreadEvent,
    TurnFilesChangedEvent,
    encode_event,
)
from backend.engine.sse_adapter import to_sse_line
from backend.engine.stream_events import HitlRequested, MessageChunk, SubagentOutput

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from fastapi import Request

    from backend.engine.stream_events import EngineEvent, EventSink

logger = logging.getLogger(__name__)

#: Delay antes de confirmar uma leitura positiva de is_disconnected() — mesmo
#: valor/motivo de ``backend/api/adapters.py`` (falso-positivo isolado do
#: BaseHTTPMiddleware de auth não deve encerrar um stream real).
_DISCONNECT_CONFIRM_DELAY_S = 0.3

_SENTINEL = object()
"""Marca o fim da fila de eventos — devolvida pelo runner depois que a
chamada ao motor nativo termina (sucesso, pausa HITL, ou exceção)."""


async def _spawn_save_partial(
    thread_id: str, content: str, background_tasks: set[asyncio.Task[None]]
) -> None:
    import asyncio

    from backend.persistence.kv import get_kv

    async def _save() -> None:
        kv = await get_kv()
        await kv.set(f"partial:{thread_id}", content, ttl_s=300)

    def _log_if_failed(task: asyncio.Task[None]) -> None:
        # Task fire-and-forget: sem isso, uma falha (ex.: NATS indisponível)
        # só aparece como "Task exception was never retrieved" no logger
        # padrão do asyncio — nunca no logger estruturado da aplicação, nem
        # no .jsonl de logs (achado real da investigação de 2026-08-30).
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "native_stream: falha ao salvar preview parcial de %s no KV",
                thread_id,
                exc_info=task.exception(),
            )

    task = asyncio.ensure_future(_save())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    task.add_done_callback(_log_if_failed)


async def _clear_partial(thread_id: str) -> None:
    from backend.persistence.kv import get_kv

    kv = await get_kv()
    await kv.delete(f"partial:{thread_id}")


async def _record_subagent(
    thread_id: str,
    user_id: str,
    workspace_id: str | None,
    event: SubagentOutput,
) -> None:
    try:
        from backend.scheduling.background_tasks import record_subagent_delegation

        await record_subagent_delegation(
            session_id=thread_id,
            user_id=user_id,
            subagent_type=event.subagent_type or "desconhecido",
            description=event.description,
            status="error" if event.status == "error" else "done",
            summary=event.content[:400],
            workspace_id=workspace_id,
        )
    except Exception:
        logger.exception("native_stream: falha ao registrar delegação de subagente")


def stream_engine_events(
    run: Callable[[EventSink], Awaitable[str]],
    *,
    thread_id: str,
    workspace_id: str | None = None,
    http_request: Request | None = None,
    user_id: str | None = None,
    run_id: str | None = None,
) -> AsyncGenerator[str]:
    """AsyncGenerator SSE a partir de uma chamada ao motor nativo.

    ``run`` recebe o callback ``on_event`` que deve repassar pro
    ``run_conversation``/``resume_conversation`` (assinatura genérica —
    cobre StreamChat, que só chama ``run_conversation``, e ResumeChat, que
    chama ``resume_conversation`` seguido de ``run_conversation`` na mesma
    função) e devolve o ``stopped_reason`` efetivo do turno (``"stop"`` |
    ``"interrupted"`` | ``"max_iterations"`` | ``"loop_cap_exceeded"`` |
    ``"noop"`` — este último quando um resume não tinha nada pendente).

    Os efeitos de fim-de-turno (checkpoint de rewind, limpar conteúdo
    parcial do KV, gatilho do Remember) só disparam quando
    ``stopped_reason == "stop"`` — uma pausa HITL (aguardando aprovação)
    não é uma conclusão real de turno, então nenhum desses efeitos roda
    até o turno de fato terminar.
    """
    import asyncio

    async def _gen() -> AsyncGenerator[str]:
        from backend.api.turn_files import (
            capture,
            finalize,
        )
        from backend.api.turn_files import (
            encode as encode_turn_files,
        )

        resolved_run_id = run_id or uuid.uuid4().hex
        turn_snapshot = await asyncio.to_thread(
            capture, workspace_id, thread_id, resolved_run_id
        )
        yield encode_event(
            ThreadEvent(
                thread_id=thread_id,
                workspace_id=workspace_id or "",
            )
        )

        queue: asyncio.Queue[Any] = asyncio.Queue()
        background_tasks: set[asyncio.Task[None]] = set()
        run_error: BaseException | None = None
        stopped_reason = "stop"
        turn_files_payload: list[dict[str, Any]] = []

        async def on_event(event: EngineEvent) -> None:
            await queue.put(event)

        async def _runner() -> None:
            nonlocal run_error, stopped_reason
            try:
                stopped_reason = await run(on_event)
            except BaseException as exc:
                run_error = exc
            finally:
                changes = await asyncio.to_thread(finalize, turn_snapshot)
                turn_files_payload.extend(encode_turn_files(changes))
                if changes:
                    try:
                        from backend.services import agent_factory

                        store = await agent_factory.get_session_store()
                        await store.attach_edited_files(
                            thread_id, resolved_run_id, encode_turn_files(changes)
                        )
                    except Exception:
                        logger.warning(
                            "native_stream: falha ao persistir arquivos editados",
                            exc_info=True,
                        )
                await queue.put(_SENTINEL)

        term_queue: asyncio.Queue[str] = asyncio.Queue()

        def _on_terminal_line(line: str) -> None:
            with contextlib.suppress(Exception):
                term_queue.put_nowait(line)

        from backend.services.terminal_stream import (
            register_terminal_output_callback,
            unregister_terminal_output_callback,
        )

        register_terminal_output_callback(_on_terminal_line)

        run_task: asyncio.Task[None] = asyncio.ensure_future(_runner())
        next_task: asyncio.Task[Any] = asyncio.ensure_future(queue.get())
        term_task: asyncio.Task[str] = asyncio.ensure_future(term_queue.get())

        content_started = False
        accumulated_partial_content = ""
        last_kv_flush = 0.0

        disconnected = False

        try:
            while True:
                wait_set: set[asyncio.Task[Any]] = {next_task, term_task}
                disconnect_task: asyncio.Task[bool] | None = None
                request: Request | None = None
                if http_request is not None:
                    request = http_request
                    disconnect_task = asyncio.ensure_future(request.is_disconnected())
                    wait_set.add(disconnect_task)

                done, _ = await asyncio.wait(
                    wait_set, return_when=asyncio.FIRST_COMPLETED
                )

                if disconnect_task is not None and request is not None:
                    if disconnect_task in done and disconnect_task.result():
                        await asyncio.sleep(_DISCONNECT_CONFIRM_DELAY_S)
                        if not await request.is_disconnected():
                            continue

                        async def _consume_remainder(
                            pending: asyncio.Task[Any], q: asyncio.Queue[Any]
                        ) -> None:
                            with contextlib.suppress(Exception):
                                await pending
                            with contextlib.suppress(Exception):
                                while True:
                                    item = await q.get()
                                    if item is _SENTINEL:
                                        break

                        remainder = asyncio.create_task(
                            _consume_remainder(next_task, queue)
                        )
                        background_tasks.add(remainder)
                        remainder.add_done_callback(background_tasks.discard)
                        term_task.cancel()
                        disconnected = True
                        break
                    else:
                        disconnect_task.cancel()

                if term_task in done:
                    line = term_task.result()
                    yield encode_event(TerminalLineEvent(line=line))
                    term_task = asyncio.ensure_future(term_queue.get())
                    continue

                if next_task not in done:
                    continue

                event = next_task.result()
                next_task = asyncio.ensure_future(queue.get())

                if event is _SENTINEL:
                    break

                if isinstance(event, MessageChunk):
                    accumulated_partial_content += event.content
                    now = time.monotonic()
                    if now - last_kv_flush > 0.5:
                        last_kv_flush = now
                        await _spawn_save_partial(
                            thread_id, accumulated_partial_content, background_tasks
                        )
                    if not content_started:
                        content_started = True
                        t = asyncio.ensure_future(_mark_thread_has_content(thread_id))
                        background_tasks.add(t)
                        t.add_done_callback(background_tasks.discard)

                if isinstance(event, HitlRequested):
                    try:
                        args = json.loads(event.args_json or "{}")
                        pre = await _pre_approved(
                            event.tool_name, args, workspace_id or ""
                        )
                    except Exception:
                        logger.debug(
                            "native_stream: pre_approved falhou", exc_info=True
                        )
                        pre = False
                    event = replace(event, pre_approved=pre)

                if (
                    isinstance(event, SubagentOutput)
                    and event.status != "running"
                    and user_id is not None
                ):
                    t = asyncio.ensure_future(
                        _record_subagent(thread_id, user_id, workspace_id, event)
                    )
                    background_tasks.add(t)
                    t.add_done_callback(background_tasks.discard)

                yield to_sse_line(event)

            if turn_files_payload:
                yield encode_event(
                    TurnFilesChangedEvent(
                        run_id=resolved_run_id, files=turn_files_payload
                    )
                )

            if run_error is not None:
                logger.error(
                    "native_stream: erro na execução do agente",
                    exc_info=run_error,
                )
                code, friendly = classify_stream_error(run_error)
                yield encode_event(ErrorEvent(message=friendly, code=code))
            elif stopped_reason == "stop":
                t = asyncio.ensure_future(_clear_partial(thread_id))
                background_tasks.add(t)
                t.add_done_callback(background_tasks.discard)

                if workspace_id:
                    await _record_turn_checkpoint(
                        workspace_id, thread_id, {"run_id": str(uuid.uuid4())}
                    )

                from backend.services.remember_trigger import maybe_trigger_remember

                t2 = asyncio.ensure_future(
                    maybe_trigger_remember(thread_id, user_id or "local")
                )
                background_tasks.add(t2)
                t2.add_done_callback(background_tasks.discard)

        except Exception as exc:
            logger.exception("native_stream: erro no stream nativo")
            code, friendly = classify_stream_error(exc)
            yield encode_event(ErrorEvent(message=friendly, code=code))

        finally:
            unregister_terminal_output_callback()
            if not term_task.done():
                term_task.cancel()
                with contextlib.suppress(BaseException):
                    await term_task
            if not disconnected and not next_task.done():
                next_task.cancel()
                with contextlib.suppress(BaseException):
                    await next_task
            if not run_task.done() and not disconnected:
                # Só alcançável se o loop saiu por outro caminho que não o
                # sentinel nem a desconexão do cliente (ex.: exceção no
                # próprio bridge) — defensivo, `_runner` sempre injeta o
                # sentinel no `finally` dele. Numa desconexão, `run_task`
                # segue rodando de propósito (drenado em background por
                # `_consume_remainder`) — a sessão continua gerando a
                # resposta mesmo sem cliente conectado.
                run_task.cancel()
                with contextlib.suppress(BaseException):
                    await run_task
            yield encode_event(DoneEvent(thread_id=thread_id, run_id=resolved_run_id))

    return _gen()
