"""``run_conversation`` — loop de conversa nativo. `ChatClient`
mockado com uma sequência de turnos controlada (`finish_reason` por
chamada): parada normal, `max_iterations`, tool_calls sequenciais, tool_calls
paralelos não-destrutivos, e interrupção HITL no meio de um lote misto.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from backend.engine.conversation_loop import (
    LoopConfig,
    resume_conversation,
    run_conversation,
)
from backend.engine.guardrails import LoopCapConfig
from backend.engine.hitl import ApprovalGate
from backend.engine.stream_events import (
    EngineEvent,
    ErrorSignal,
    EventSink,
    HitlRequested,
    MessageBreak,
    MessageChunk,
    TodosUpdated,
    ToolActivity,
    ToolCallStarted,
    ToolResult,
    WorkbenchInvalidate,
)
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools import media as _media_module
from backend.tools import planning as _planning_module
from backend.tools.context import ToolContext
from backend.tools.registry import TOOL_REGISTRY, ToolExtras, ToolRegistry, vtool
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    ToolCallChunk,
    VMessage,
    VMessageChunk,
)


def _register(registry: ToolRegistry, nome: str) -> None:
    spec = TOOL_REGISTRY.get(nome)
    assert spec is not None
    registry.register(spec)


class _ScriptedChatClient:
    """`astream` devolve um turno por chamada, na ordem do script — cada
    turno é a lista de `VMessageChunk` que o iteração daquela volta do loop
    deve produzir."""

    def __init__(self, turnos: list[list[VMessageChunk]]) -> None:
        self._turnos = turnos
        self.chamadas = 0

    async def astream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        turno = self._turnos[self.chamadas]
        self.chamadas += 1
        for chunk in turno:
            yield chunk

    async def agenerate(
        self, messages, *, tools=None, temperature=None, max_tokens=None
    ):
        msg = "não usado pelo loop (astream-only)"
        raise NotImplementedError(msg)


@pytest.fixture
async def session_store(tmp_path):
    pool = AsyncConnectionPool(str(tmp_path / "loop.db"), min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    await store.create_session("thread-1", user_id="alice")
    try:
        yield store
    finally:
        await pool.close()


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id="alice", thread_id="thread-1")


def _texto_chunk(texto: str) -> VMessageChunk:
    return VMessageChunk(delta_text=texto)


def _tool_call_chunk(*, index: int, id: str, name: str, args: str) -> VMessageChunk:  # noqa: A002
    return VMessageChunk(
        tool_call_chunks=[
            ToolCallChunk(index=index, id=id, name=name, args_fragment=args)
        ]
    )


class TestParadaNormal:
    async def test_sem_tool_calls_para_no_primeiro_turno(self, session_store, ctx):
        client = _ScriptedChatClient([[_texto_chunk("olá!")]])
        resultado = await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=ToolRegistry(),
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
        )

        assert resultado.stopped_reason == "stop"
        assert resultado.final_message is not None
        assert resultado.final_message.text() == "olá!"
        assert client.chamadas == 1

        historico = await session_store.get_history("thread-1")
        assert [m.text() for m in historico] == ["olá!"]


class TestMaxIterations:
    async def test_estoura_o_teto_sem_nunca_parar_naturalmente(
        self, session_store, ctx
    ):
        @vtool(extras=ToolExtras())
        async def loop_tool(ctx: ToolContext) -> str:
            """tool que sempre é chamada de novo."""
            return "ok"

        registry = ToolRegistry()
        _register(registry, "loop_tool")

        turno = [_tool_call_chunk(index=0, id="call_1", name="loop_tool", args="{}")]
        client = _ScriptedChatClient([turno, turno, turno])
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        resultado = await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(max_iterations=3),
            on_event=on_event,
        )

        assert resultado.stopped_reason == "max_iterations"
        assert client.chamadas == 3
        sinais = [e for e in eventos if isinstance(e, ErrorSignal)]
        assert any(e.code == "RECURSION_LIMIT" for e in sinais)


class TestGuardrailDeRepeticao:
    async def test_chamadas_identicas_disparam_aviso_diferentes_nao(
        self, session_store, ctx
    ):
        @vtool(extras=ToolExtras())
        async def buscar(query: str, ctx: ToolContext) -> str:
            """busca algo.
            Args:
                query: termo de busca
            """
            return "resultado"

        registry = ToolRegistry()
        _register(registry, "buscar")

        # Caminho feliz: 3 chamadas seguidas com args idênticos disparam o aviso.
        turno_repetido = [
            _tool_call_chunk(index=0, id="call_1", name="buscar", args='{"query":"x"}')
        ]
        client = _ScriptedChatClient(
            [turno_repetido, turno_repetido, turno_repetido, [_texto_chunk("fim")]]
        )
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            on_event=on_event,
        )
        sinais = [e for e in eventos if isinstance(e, ErrorSignal)]
        assert any(e.code == "TOOL_CALL_REPEATED" for e in sinais)

        # Borda: mesma tool, args diferentes a cada chamada — nunca dispara.
        await session_store.create_session("thread-2", user_id="alice")
        client2 = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0, id="c1", name="buscar", args='{"query":"a"}'
                    )
                ],
                [
                    _tool_call_chunk(
                        index=0, id="c2", name="buscar", args='{"query":"b"}'
                    )
                ],
                [
                    _tool_call_chunk(
                        index=0, id="c3", name="buscar", args='{"query":"c"}'
                    )
                ],
                [_texto_chunk("fim")],
            ]
        )
        eventos2: list[EngineEvent] = []

        async def on_event2(event: EngineEvent) -> None:
            eventos2.append(event)

        await run_conversation(
            session_store=session_store,
            chat_client=client2,
            tool_registry=registry,
            ctx=ToolContext(user_id="alice", thread_id="thread-2"),
            thread_id="thread-2",
            config=LoopConfig(),
            on_event=on_event2,
        )
        sinais2 = [e for e in eventos2 if isinstance(e, ErrorSignal)]
        assert not any(e.code == "TOOL_CALL_REPEATED" for e in sinais2)


class TestToolCallsSequenciais:
    async def test_chama_tool_depois_para_no_turno_seguinte(self, session_store, ctx):
        @vtool(extras=ToolExtras())
        async def somar(a: int, b: int, ctx: ToolContext) -> str:
            """soma dois números.
            Args:
                a: primeiro
                b: segundo
            """
            return str(a + b)

        registry = ToolRegistry()
        _register(registry, "somar")

        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0, id="call_1", name="somar", args='{"a":1,"b":2}'
                    )
                ],
                [_texto_chunk("a soma é 3")],
            ]
        )

        resultado = await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
        )

        assert resultado.stopped_reason == "stop"
        assert resultado.final_message is not None
        assert resultado.final_message.text() == "a soma é 3"
        assert client.chamadas == 2

        historico = await session_store.get_history("thread-1")
        assert historico[0].tool_calls[0].name == "somar"
        assert historico[1].role.value == "tool"
        assert historico[1].text() == "3"
        assert historico[2].text() == "a soma é 3"


class TestToolCallsParalelos:
    async def test_duas_tools_nao_destrutivas_executam_e_retornam(
        self, session_store, ctx
    ):
        @vtool(extras=ToolExtras(destructive=False))
        async def ler_a(ctx: ToolContext) -> str:
            """lê a."""
            return "conteudo-a"

        @vtool(extras=ToolExtras(destructive=False))
        async def ler_b(ctx: ToolContext) -> str:
            """lê b."""
            return "conteudo-b"

        registry = ToolRegistry()
        _register(registry, "ler_a")
        _register(registry, "ler_b")

        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(index=0, id="call_1", name="ler_a", args="{}"),
                    _tool_call_chunk(index=1, id="call_2", name="ler_b", args="{}"),
                ],
                [_texto_chunk("terminei")],
            ]
        )

        resultado = await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
        )

        assert resultado.stopped_reason == "stop"
        historico = await session_store.get_history("thread-1")
        resultados_tool = [m.text() for m in historico if m.role.value == "tool"]
        assert set(resultados_tool) == {"conteudo-a", "conteudo-b"}


class TestHitl:
    async def test_lote_misto_com_uma_tool_sensivel_pausa_sem_executar_nenhuma(
        self, session_store, ctx
    ):
        @vtool(extras=ToolExtras(destructive=False))
        async def ler_arquivo(ctx: ToolContext) -> str:
            """lê um arquivo."""
            return "nunca deveria rodar"

        @vtool(extras=ToolExtras(destructive=True))
        async def escrever_arquivo(ctx: ToolContext) -> str:
            """escreve um arquivo."""
            return "nunca deveria rodar"

        registry = ToolRegistry()
        _register(registry, "ler_arquivo")
        _register(registry, "escrever_arquivo")

        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0, id="call_1", name="ler_arquivo", args="{}"
                    ),
                    _tool_call_chunk(
                        index=1, id="call_2", name="escrever_arquivo", args="{}"
                    ),
                ]
            ]
        )

        def should_require_approval(nome, _ctx, _args, _history):
            return nome == "escrever_arquivo"

        resultado = await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            should_require_approval=should_require_approval,
        )

        assert resultado.stopped_reason == "interrupted"
        # Nenhuma tool do lote foi executada — só a mensagem do assistente
        # (com as tool_calls propostas) está no histórico.
        historico = await session_store.get_history("thread-1")
        assert [m.role.value for m in historico] == ["assistant"]
        assert len(historico[0].tool_calls) == 2

    async def test_sem_should_require_approval_nunca_pausa(self, session_store, ctx):
        @vtool(extras=ToolExtras(destructive=True))
        async def deletar(ctx: ToolContext) -> str:
            """deleta algo."""
            return "deletado"

        registry = ToolRegistry()
        _register(registry, "deletar")

        client = _ScriptedChatClient(
            [
                [_tool_call_chunk(index=0, id="call_1", name="deletar", args="{}")],
                [_texto_chunk("feito")],
            ]
        )

        resultado = await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
        )

        assert resultado.stopped_reason == "stop"
        assert client.chamadas == 2


class TestEmissaoDeEventos:
    async def test_token_e_message_break_emitidos_na_ordem(self, session_store, ctx):
        client = _ScriptedChatClient([[_texto_chunk("oi"), _texto_chunk(" tudo bem")]])
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=ToolRegistry(),
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            on_event=on_event,
        )

        assert eventos == [
            MessageChunk(content="oi"),
            MessageChunk(content=" tudo bem"),
            MessageBreak(),
        ]

    async def test_tool_result_emitido_apos_execucao(self, session_store, ctx):
        @vtool(extras=ToolExtras())
        async def adicionar(a: int, b: int, ctx: ToolContext) -> str:
            """soma dois números.
            Args:
                a: primeiro
                b: segundo
            """
            return str(a + b)

        registry = ToolRegistry()
        _register(registry, "adicionar")

        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0, id="call_1", name="adicionar", args='{"a":1,"b":2}'
                    )
                ],
                [_texto_chunk("pronto")],
            ]
        )
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            on_event=on_event,
        )

        resultados_tool = [e for e in eventos if isinstance(e, ToolResult)]
        assert resultados_tool == [
            ToolResult(tool_call_id="call_1", content_json="3", is_error=False)
        ]

    async def test_hitl_requested_emitido_com_argumentos_da_tool(
        self, session_store, ctx
    ):
        @vtool(extras=ToolExtras(destructive=True))
        async def escrever(ctx: ToolContext) -> str:
            """escreve algo."""
            return "nunca roda"

        registry = ToolRegistry()
        _register(registry, "escrever")

        client = _ScriptedChatClient(
            [[_tool_call_chunk(index=0, id="call_1", name="escrever", args="{}")]]
        )
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            on_event=on_event,
            should_require_approval=lambda nome, _ctx, _args, _history: (
                nome == "escrever"
            ),
        )

        hitl_eventos = [e for e in eventos if isinstance(e, HitlRequested)]
        assert len(hitl_eventos) == 1
        assert hitl_eventos[0].tool_name == "escrever"
        assert hitl_eventos[0].args_json == "{}"
        assert hitl_eventos[0].interrupt_id  # gerado, não vazio

    async def test_write_terminal_preserva_entrada_bruta_ao_aprovar(
        self, session_store, ctx, monkeypatch
    ) -> None:
        import backend.tools.terminal_sessions

        registry = ToolRegistry()
        _register(registry, "write_terminal")
        segredo = "sk-test ghp_secret AKIAEXAMPLE"
        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0,
                        id="call-terminal",
                        name="write_terminal",
                        args=json.dumps(
                            {
                                "terminal_id": "term-1",
                                "input_data": segredo,
                                "request_id": "req-1",
                            }
                        ),
                    )
                ]
            ]
        )
        gate = ApprovalGate(session_store)

        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            approval_gate=gate,
            should_require_approval=lambda name, *_args: name == "write_terminal",
        )

        pending = await session_store.get_pending_approval("thread-1")
        history = await session_store.get_history("thread-1")
        assert pending is not None
        assert segredo not in json.dumps(pending)
        assert segredo not in json.dumps([message.to_dict() for message in history])
        assert pending["args"]["input_preview"] == "<redacted>"

        chamadas: list[dict[str, object]] = []

        async def fake_execute(
            tool_call: ToolCall,
            *,
            tool_registry: ToolRegistry,
            ctx: ToolContext,
            on_event: EventSink | None = None,
        ) -> VMessage:
            chamadas.append(dict(tool_call.args))
            return VMessage(
                role=MessageRole.TOOL,
                content=[ContentBlock(kind="text", text="written")],
                tool_call_id=tool_call.id,
                name=tool_call.name,
            )

        monkeypatch.setattr(
            "backend.engine.conversation_loop._execute_single_call", fake_execute
        )
        assert await resume_conversation(
            session_store=session_store,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            decision="approve",
            approval_gate=gate,
        )
        assert chamadas == [
            {
                "terminal_id": "term-1",
                "input_data": segredo,
                "request_id": "req-1",
            }
        ]

    async def test_tool_call_started_e_activity_emitidos_antes_e_depois(
        self, session_store, ctx
    ):
        @vtool(extras=ToolExtras(category="general", icon="wrench"))
        async def somar3(a: int, b: int, ctx: ToolContext) -> str:
            """soma dois números.
            Args:
                a: primeiro
                b: segundo
            """
            return str(a + b)

        registry = ToolRegistry()
        _register(registry, "somar3")

        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0, id="call_1", name="somar3", args='{"a":1,"b":2}'
                    )
                ],
                [_texto_chunk("pronto")],
            ]
        )
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            on_event=on_event,
        )

        started = [e for e in eventos if isinstance(e, ToolCallStarted)]
        assert started == [
            ToolCallStarted(
                tool_name="somar3",
                tool_call_id="call_1",
                args_json='{"a": 1, "b": 2}',
                render_hint="default",
                category="general",
                destructive=False,
                icon="wrench",
            )
        ]
        activities = [e for e in eventos if isinstance(e, ToolActivity)]
        # Um ToolActivity de início (sem elapsed_ms) e um de fim (com elapsed_ms).
        assert len(activities) == 2
        assert activities[0].elapsed_ms is None
        assert activities[1].elapsed_ms is not None

        # A tool não declara `invalidates` — nenhum WorkbenchInvalidate emitido.
        assert not [e for e in eventos if isinstance(e, WorkbenchInvalidate)]

    async def test_tool_com_invalidates_emite_workbench_invalidate(
        self, session_store, ctx
    ):
        @vtool(extras=ToolExtras(invalidates=["files", "git"]))
        async def escrever2(ctx: ToolContext) -> str:
            """escreve algo."""
            return "ok"

        registry = ToolRegistry()
        _register(registry, "escrever2")

        client = _ScriptedChatClient(
            [
                [_tool_call_chunk(index=0, id="call_1", name="escrever2", args="{}")],
                [_texto_chunk("pronto")],
            ]
        )
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            on_event=on_event,
        )

        invalidates = [e for e in eventos if isinstance(e, WorkbenchInvalidate)]
        assert invalidates == [
            WorkbenchInvalidate(tabs=["files", "git"], tool_name="escrever2")
        ]


class TestArgsPreview:
    async def test_campo_semantico_vira_preview_no_inicio_e_no_fim(
        self, session_store, ctx
    ):
        @vtool(extras=ToolExtras())
        async def ler_arquivo(path: str, ctx: ToolContext) -> str:
            """lê um arquivo.
            Args:
                path: caminho do arquivo
            """
            return "conteudo"

        registry = ToolRegistry()
        _register(registry, "ler_arquivo")

        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0,
                        id="call_1",
                        name="ler_arquivo",
                        args='{"path":"backend/auth.py"}',
                    )
                ],
                [_texto_chunk("pronto")],
            ]
        )
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            on_event=on_event,
        )

        activities = [e for e in eventos if isinstance(e, ToolActivity)]
        assert len(activities) == 2
        assert activities[0].args_preview == "backend/auth.py"
        assert activities[1].args_preview == "backend/auth.py"

    async def test_preview_trunca_em_80_chars(self, session_store, ctx):
        @vtool(extras=ToolExtras())
        async def buscar(query: str, ctx: ToolContext) -> str:
            """busca algo.
            Args:
                query: termo de busca
            """
            return "achou"

        registry = ToolRegistry()
        _register(registry, "buscar")

        termo_longo = "x" * 120
        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0,
                        id="call_1",
                        name="buscar",
                        args=json.dumps({"query": termo_longo}),
                    )
                ],
                [_texto_chunk("pronto")],
            ]
        )
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            on_event=on_event,
        )

        activities = [e for e in eventos if isinstance(e, ToolActivity)]
        assert all(len(a.args_preview) == 80 for a in activities)
        assert activities[0].args_preview == "x" * 80

    async def test_sem_campo_semantico_usa_fallback_k_igual_v(self, session_store, ctx):
        @vtool(extras=ToolExtras())
        async def somar4(a: int, b: int, ctx: ToolContext) -> str:
            """soma dois números.
            Args:
                a: primeiro
                b: segundo
            """
            return str(a + b)

        registry = ToolRegistry()
        _register(registry, "somar4")

        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0, id="call_1", name="somar4", args='{"a":1,"b":2}'
                    )
                ],
                [_texto_chunk("pronto")],
            ]
        )
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            on_event=on_event,
        )

        activities = [e for e in eventos if isinstance(e, ToolActivity)]
        assert activities[0].args_preview == "a=1, b=2"


class TestTodosUpdated:
    async def test_write_todos_emite_todos_updated_com_lista_completa(
        self, session_store, ctx
    ):
        registry = ToolRegistry()
        _register(registry, "write_todos")

        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0,
                        id="call_1",
                        name="write_todos",
                        args=json.dumps(
                            {
                                "todos": [
                                    {"content": "passo 1", "status": "completed"},
                                    {"content": "passo 2", "status": "in_progress"},
                                ]
                            }
                        ),
                    )
                ],
                [_texto_chunk("pronto")],
            ]
        )
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            on_event=on_event,
        )

        todos_eventos = [e for e in eventos if isinstance(e, TodosUpdated)]
        assert len(todos_eventos) == 1
        assert [(t.content, t.status) for t in todos_eventos[0].todos] == [
            ("passo 1", "completed"),
            ("passo 2", "in_progress"),
        ]

    async def test_tool_diferente_de_write_todos_nunca_emite_todos_updated(
        self, session_store, ctx
    ):
        @vtool(extras=ToolExtras())
        async def outra_tool(ctx: ToolContext) -> str:
            """não é write_todos."""
            return json.dumps([{"content": "x", "status": "pending"}])

        registry = ToolRegistry()
        _register(registry, "outra_tool")

        client = _ScriptedChatClient(
            [
                [_tool_call_chunk(index=0, id="call_1", name="outra_tool", args="{}")],
                [_texto_chunk("pronto")],
            ]
        )
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            on_event=on_event,
        )

        assert not [e for e in eventos if isinstance(e, TodosUpdated)]

    async def test_write_todos_com_args_invalidos_nao_emite_todos_updated(
        self, session_store, ctx
    ):
        """Borda: status fora da taxonomia é rejeitado na validação de args
        (Error: argumentos inválidos) antes mesmo do handler rodar — o loop
        não deve emitir TodosUpdated a partir de um resultado de erro."""
        registry = ToolRegistry()
        _register(registry, "write_todos")

        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0,
                        id="call_1",
                        name="write_todos",
                        args=json.dumps(
                            {"todos": [{"content": "x", "status": "done"}]}
                        ),
                    )
                ],
                [_texto_chunk("pronto")],
            ]
        )
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            on_event=on_event,
        )

        resultados_tool = [e for e in eventos if isinstance(e, ToolResult)]
        assert resultados_tool[0].is_error
        assert not [e for e in eventos if isinstance(e, TodosUpdated)]


class TestLoopCapGuardrail:
    async def test_estoura_teto_de_tool_calls_encerra_turno_sem_esgotar_max_iterations(
        self, session_store, ctx
    ):
        @vtool(extras=ToolExtras())
        async def contar(n: int, ctx: ToolContext) -> str:
            """conta.
            Args:
                n: número
            """
            return "ok"

        registry = ToolRegistry()
        _register(registry, "contar")

        # 5 turnos chamando a tool com args diferentes (não repetidos —
        # não confunde com o guardrail de repetição), mas o teto de volume
        # do turno é 2: a 3ª chamada já deveria encerrar o loop.
        turnos = [
            [_tool_call_chunk(index=0, id=f"c{i}", name="contar", args=f'{{"n":{i}}}')]
            for i in range(5)
        ]
        client = _ScriptedChatClient(turnos)
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        resultado = await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(
                max_iterations=50,
                loop_caps=LoopCapConfig(max_tool_calls_per_turn=2),
            ),
            on_event=on_event,
        )

        assert resultado.stopped_reason == "loop_cap_exceeded"
        # Encerrou muito antes de esgotar as 50 iterações permitidas.
        assert client.chamadas < 5
        sinais = [e for e in eventos if isinstance(e, ErrorSignal)]
        assert any(e.code == "LOOP_CAP_EXCEEDED" for e in sinais)

    async def test_dentro_do_teto_nunca_dispara_o_guardrail(self, session_store, ctx):
        @vtool(extras=ToolExtras())
        async def contar2(n: int, ctx: ToolContext) -> str:
            """conta.
            Args:
                n: número
            """
            return "ok"

        registry = ToolRegistry()
        _register(registry, "contar2")

        turnos = [
            [_tool_call_chunk(index=0, id=f"c{i}", name="contar2", args=f'{{"n":{i}}}')]
            for i in range(2)
        ] + [[_texto_chunk("fim")]]
        client = _ScriptedChatClient(turnos)
        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        resultado = await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(loop_caps=LoopCapConfig(max_tool_calls_per_turn=10)),
            on_event=on_event,
        )

        assert resultado.stopped_reason == "stop"
        sinais = [e for e in eventos if isinstance(e, ErrorSignal)]
        assert not any(e.code == "LOOP_CAP_EXCEEDED" for e in sinais)


class TestResumeConversation:
    async def test_aprovar_executa_a_tool_sinalizada_e_libera_a_pendencia(
        self, session_store, ctx
    ):
        @vtool(extras=ToolExtras(destructive=True))
        async def escrever_arquivo2(ctx: ToolContext) -> str:
            """escreve um arquivo."""
            return "escrito!"

        registry = ToolRegistry()
        _register(registry, "escrever_arquivo2")

        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0, id="call_1", name="escrever_arquivo2", args="{}"
                    )
                ]
            ]
        )
        gate = ApprovalGate(session_store)
        resultado = await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            should_require_approval=lambda *_a: True,
            approval_gate=gate,
        )
        assert resultado.stopped_reason == "interrupted"
        assert await session_store.get_pending_approval("thread-1") is not None

        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        resumiu = await resume_conversation(
            session_store=session_store,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            decision="approve",
            approval_gate=gate,
            on_event=on_event,
        )

        assert resumiu is True
        assert await session_store.get_pending_approval("thread-1") is None
        historico = await session_store.get_history("thread-1")
        assert historico[-1].role.value == "tool"
        assert historico[-1].text() == "escrito!"
        assert historico[-1].is_error is False
        resultados = [e for e in eventos if isinstance(e, ToolResult)]
        assert resultados == [
            ToolResult(tool_call_id="call_1", content_json="escrito!", is_error=False)
        ]

    async def test_aprovar_classifica_erro_mcp_localizado_e_registra_uso(
        self, session_store, ctx, monkeypatch
    ):
        chamadas: list[tuple[str, str, str]] = []

        async def registrar(user_id: str, tool_name: str, status: str) -> None:
            chamadas.append((user_id, tool_name, status))

        monkeypatch.setattr("backend.services.tool_usage.record_tool_usage", registrar)

        @vtool(extras=ToolExtras(destructive=True))
        async def falhar_mcp(ctx: ToolContext) -> str:
            """simula falha localizada de uma ferramenta MCP."""
            return "Erro ao invocar tool MCP 'demo': conexão recusada"

        registry = ToolRegistry()
        _register(registry, "falhar_mcp")
        client = _ScriptedChatClient(
            [[_tool_call_chunk(index=0, id="call_mcp", name="falhar_mcp", args="{}")]]
        )
        gate = ApprovalGate(session_store)
        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            should_require_approval=lambda *_a: True,
            approval_gate=gate,
        )

        eventos: list[EngineEvent] = []

        async def on_event(event: EngineEvent) -> None:
            eventos.append(event)

        assert await resume_conversation(
            session_store=session_store,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            decision="approve",
            approval_gate=gate,
            on_event=on_event,
        )

        resultado = next(event for event in eventos if isinstance(event, ToolResult))
        assert resultado.is_error is True
        assert chamadas == [(ctx.user_id, "falhar_mcp", "error")]

    async def test_rejeitar_nao_executa_a_tool_e_persiste_mensagem_de_erro(
        self, session_store, ctx
    ):
        @vtool(extras=ToolExtras(destructive=True))
        async def deletar_tudo(ctx: ToolContext) -> str:
            """deleta tudo — nunca deveria rodar quando rejeitado."""
            return "NUNCA DEVERIA APARECER"

        registry = ToolRegistry()
        _register(registry, "deletar_tudo")

        client = _ScriptedChatClient(
            [[_tool_call_chunk(index=0, id="call_1", name="deletar_tudo", args="{}")]]
        )
        gate = ApprovalGate(session_store)
        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            config=LoopConfig(),
            should_require_approval=lambda *_a: True,
            approval_gate=gate,
        )

        resumiu = await resume_conversation(
            session_store=session_store,
            tool_registry=registry,
            ctx=ctx,
            thread_id="thread-1",
            decision="reject",
            approval_gate=gate,
        )

        assert resumiu is True
        historico = await session_store.get_history("thread-1")
        assert historico[-1].role.value == "tool"
        assert historico[-1].is_error is True
        assert "rejeitou" in historico[-1].text().lower()

    async def test_sem_pendencia_nao_faz_nada_e_devolve_false(self, session_store, ctx):
        resumiu = await resume_conversation(
            session_store=session_store,
            tool_registry=ToolRegistry(),
            ctx=ctx,
            thread_id="thread-1",
            decision="approve",
        )

        assert resumiu is False
        assert await session_store.get_history("thread-1") == []

    @pytest.mark.parametrize(
        ("tool_name", "args"),
        [
            ("generate_image", {"prompt": "segredo da imagem"}),
            ("text_to_speech", {"text": "segredo do áudio", "voice": "alloy"}),
            ("generate_video", {"prompt": "segredo do vídeo"}),
        ],
    )
    async def test_midia_aprovada_no_loop_preserva_payload_somente_em_memoria(
        self,
        session_store: SessionStore,
        ctx: ToolContext,
        monkeypatch: pytest.MonkeyPatch,
        tool_name: str,
        args: dict[str, str],
    ) -> None:
        """O loop persiste apenas metadados e executa o payload efêmero após aprovação."""
        from backend.persistence.telemetry import telemetry
        from backend.services.media_quota import media_quota

        chamadas: list[dict[str, object]] = []
        eventos_quota: list[tuple[str, dict[str, object]]] = []

        async def fake_summary(_user_id: str) -> dict[str, int]:
            return {"remaining": 100, "used": 0, "limit": 100}

        def record_quota(event: str, **fields: object) -> None:
            eventos_quota.append((event, fields))

        async def fake_media_handler(**kwargs: object) -> str:
            chamadas.append(kwargs)
            return "mídia gerada"

        monkeypatch.setattr(media_quota, "summary", fake_summary)
        monkeypatch.setattr(telemetry, "record_media_quota", record_quota)
        spec = TOOL_REGISTRY.get(tool_name)
        assert spec is not None
        registry = ToolRegistry()
        registry.register(replace(spec, handler=fake_media_handler))
        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0, id="media-call", name=tool_name, args=json.dumps(args)
                    )
                ]
            ]
        )
        gate = ApprovalGate(session_store)

        resultado = await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=replace(ctx, model="openai:gpt-image"),
            thread_id="thread-1",
            config=LoopConfig(),
            should_require_approval=lambda *_args: True,
            approval_gate=gate,
        )

        assert resultado.stopped_reason == "interrupted"
        historico = await session_store.get_history("thread-1")
        persisted_calls = [
            call.args for message in historico for call in message.tool_calls
        ]
        assert args["prompt" if "prompt" in args else "text"] not in json.dumps(
            persisted_calls, ensure_ascii=False
        )
        pending = await session_store.get_pending_approval("thread-1")
        assert pending is not None
        assert all(
            secret not in json.dumps(pending["args"], ensure_ascii=False)
            for secret in args.values()
        )
        assert set(pending["args"]) <= {
            "operation",
            "provider",
            "model",
            "estimate_version",
            "billable_unit",
            "currency",
            "estimated_units",
            "idempotency_key",
            "balance_after_reservation",
        }

        assert await resume_conversation(
            session_store=session_store,
            tool_registry=registry,
            ctx=replace(ctx, model="openai:gpt-image"),
            thread_id="thread-1",
            decision="approve",
            approval_gate=gate,
        )
        assert len(chamadas) == 1
        assert {
            key: value for key, value in chamadas[0].items() if key != "ctx"
        } == args
        assert await session_store.get_pending_approval("thread-1") is None
        decisoes = [
            fields for event, fields in eventos_quota if event == "hitl_decision"
        ]
        assert len(decisoes) == 1
        assert set(decisoes[0]) <= {
            "operation",
            "provider",
            "model",
            "units",
            "idempotency_key",
            "result",
        }

    @pytest.mark.parametrize(
        ("tool_name", "args"),
        [
            ("generate_image", {"prompt": "segredo da imagem"}),
            ("text_to_speech", {"text": "segredo do áudio"}),
            ("generate_video", {"prompt": "segredo do vídeo"}),
        ],
    )
    async def test_midia_aprovacao_apos_restart_nao_executa_payload(
        self,
        session_store: SessionStore,
        ctx: ToolContext,
        monkeypatch: pytest.MonkeyPatch,
        tool_name: str,
        args: dict[str, str],
    ) -> None:
        """Após restart, a pendência continua segura e não reidrata o payload."""
        from backend.services.media_quota import media_quota

        chamadas: list[dict[str, object]] = []

        async def fake_summary(_user_id: str) -> dict[str, int]:
            return {"remaining": 100, "used": 0, "limit": 100}

        async def fake_media_handler(**kwargs: object) -> str:
            chamadas.append(kwargs)
            return "mídia gerada"

        monkeypatch.setattr(media_quota, "summary", fake_summary)
        spec = TOOL_REGISTRY.get(tool_name)
        assert spec is not None
        registry = ToolRegistry()
        registry.register(replace(spec, handler=fake_media_handler))
        gate = ApprovalGate(session_store)
        client = _ScriptedChatClient(
            [
                [
                    _tool_call_chunk(
                        index=0,
                        id="media-restart",
                        name=tool_name,
                        args=json.dumps(args),
                    )
                ]
            ]
        )
        await run_conversation(
            session_store=session_store,
            chat_client=client,
            tool_registry=registry,
            ctx=replace(ctx, model="openai:gpt-image"),
            thread_id="thread-1",
            config=LoopConfig(),
            should_require_approval=lambda *_args: True,
            approval_gate=gate,
        )

        restarted_gate = ApprovalGate(session_store)
        assert not await resume_conversation(
            session_store=session_store,
            tool_registry=registry,
            ctx=replace(ctx, model="openai:gpt-image"),
            thread_id="thread-1",
            decision="approve",
            approval_gate=restarted_gate,
        )
        assert chamadas == []
        assert await session_store.get_pending_approval("thread-1") is None
