"""Reasoning Reveal & Thinking UX: eventos de stream do chat.

Cobre: SubagentOutputEvent (identidade do subagente delegado via task());
mapeamento node → label humano (node_labels); duration_ms nos NodeEvent
de início/fim; comportamentos de adapters que não podem regredir (tokens,
tool calls/results, eventos thread/done/error).
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _isolated(_no_thread_persistence):
    pass


def _parse(sse: str) -> dict:
    assert sse.startswith("data: ")
    return json.loads(sse[len("data: ") :].strip())


# ===========================================================================
# SubagentOutputEvent: identidade do subagente no card "Subagent Outputs"
# ===========================================================================


class TestSubagentOutputEventSchema:
    """SubagentOutputEvent carrega a identidade do subagente delegado via task()."""

    def test_thinking_event_foi_removido(self):
        """Erro/borda: o ThinkingEvent legado não existe mais no schema."""
        from backend.api import schemas

        assert not hasattr(schemas, "ThinkingEvent")

    def test_campos_e_defaults(self):
        from backend.api.schemas import SubagentOutputEvent

        e = SubagentOutputEvent(subagent_type="coder")
        assert e.subagent_type == "coder"
        assert e.status == "running"  # default
        assert e.content == ""
        assert e.description == ""

        full = SubagentOutputEvent(
            subagent_type="search",
            description="pesquisar X",
            status="complete",
            tool_call_id="call-1",
            content="achei X",
            is_delta=True,
        )
        assert full.status == "complete"
        assert full.content == "achei X"
        assert full.is_delta is True

    def test_encode_tem_type_subagent_output(self):
        from backend.api.schemas import SubagentOutputEvent, encode_event

        line = encode_event(
            SubagentOutputEvent(subagent_type="coder", status="complete")
        )
        data = json.loads(line.removeprefix("data: ").strip())
        assert data["type"] == "subagent_output"
        assert data["subagent_type"] == "coder"

    def test_no_stream_payload_union(self):
        from backend.api.schemas import StreamChatEventPayload, SubagentOutputEvent

        e: StreamChatEventPayload = SubagentOutputEvent(subagent_type="coder")
        assert e is not None

    @pytest.mark.asyncio
    async def test_subagent_output_running_and_complete_via_stream_engine_events(self):
        """A delegação de subagente emite ``subagent_output`` 'running' (início)
        e 'complete' (fim, com o resultado) — identidade do subagente pro
        card. Cobertura da emissão SSE em si (o registro na aba Tarefas é
        testado em ``test_adapters_subagent_delegation.py``)."""
        from backend.api.native_stream import stream_engine_events
        from backend.engine.stream_events import SubagentOutput

        async def run(on_event):
            await on_event(
                SubagentOutput(
                    subagent_type="coder",
                    description="faz X",
                    status="running",
                    tool_call_id="r1",
                    content="feito ",
                    is_delta=True,
                )
            )
            await on_event(
                SubagentOutput(
                    subagent_type="coder",
                    description="faz X",
                    status="complete",
                    tool_call_id="r1",
                    content="feito X",
                )
            )
            return "stop"

        payloads = [
            _parse(s) async for s in stream_engine_events(run, thread_id="t-sub")
        ]
        subs = [p for p in payloads if p.get("type") == "subagent_output"]
        assert len(subs) == 2
        assert subs[0]["status"] == "running"
        assert subs[0]["subagent_type"] == "coder"
        assert subs[0]["tool_call_id"] == "r1"
        assert subs[0]["is_delta"] is True
        assert subs[0]["content"] == "feito "
        assert subs[1]["status"] == "complete"
        assert subs[1]["content"] == "feito X"
        assert subs[1]["is_delta"] is False

    @pytest.mark.asyncio
    async def test_subagent_output_status_error(self):
        """Erro/borda: quando a delegação falha (``SubagentOutput`` final com
        status='error'), o evento sai com status='error' (não 'complete') —
        o card precisa distinguir falha de sucesso."""
        from backend.api.native_stream import stream_engine_events
        from backend.engine.stream_events import SubagentOutput

        async def run(on_event):
            await on_event(
                SubagentOutput(
                    subagent_type="search",
                    description="busca X",
                    status="running",
                    tool_call_id="r-err",
                )
            )
            await on_event(
                SubagentOutput(
                    subagent_type="search",
                    description="busca X",
                    status="error",
                    tool_call_id="r-err",
                    content="falha ao buscar",
                )
            )
            return "stop"

        payloads = [
            _parse(s) async for s in stream_engine_events(run, thread_id="t-sub-err")
        ]
        subs = [p for p in payloads if p.get("type") == "subagent_output"]
        assert len(subs) == 2
        assert subs[1]["status"] == "error"
        assert subs[1]["content"] == "falha ao buscar"


# ===========================================================================
# Progresso semântico: node → label
# ===========================================================================


class TestNodeLabels:
    """node_labels.py mapeia nome interno de nó para label legível."""

    def test_node_labels_module_importable(self):
        from backend.api import node_labels

    def test_get_node_label_returns_string(self):
        from backend.api.node_labels import get_node_label

        label = get_node_label("orchestrator")
        assert isinstance(label, str)
        assert len(label) > 0

    def test_get_node_label_model(self):
        from backend.api.node_labels import get_node_label

        assert get_node_label("model") == "Analisando..."

    def test_get_node_label_search(self):
        from backend.api.node_labels import get_node_label

        assert get_node_label("search") == "Pesquisando…"

    def test_get_node_label_tools(self):
        from backend.api.node_labels import get_node_label

        label = get_node_label("tools")
        assert "ferramenta" in label.lower()

    def test_get_node_label_coder(self):
        from backend.api.node_labels import get_node_label

        label = get_node_label("coder")
        assert (
            "código" in label.lower()
            or "coder" in label.lower()
            or "programa" in label.lower()
        )

    def test_get_node_label_main_agent(self):
        from backend.api.node_labels import get_node_label

        label = get_node_label("vectora")
        assert isinstance(label, str)
        assert len(label) > 0

    def test_get_node_label_unknown_returns_generic(self):
        from backend.api.node_labels import get_node_label

        label = get_node_label("nó_desconhecido_xyz")
        assert isinstance(label, str)
        assert len(label) > 0

    def test_node_labels_dict_exported(self):
        from backend.api.node_labels import NODE_LABELS

        assert isinstance(NODE_LABELS, dict)
        assert "model" in NODE_LABELS
        assert "coder" in NODE_LABELS

    def test_node_label_routing_decision(self):
        """Label especial quando o agente delega ao sub-agent de busca."""
        from backend.api.node_labels import get_routing_label

        label = get_routing_label("search")
        assert (
            "busca" in label.lower()
            or "pesquisa" in label.lower()
            or "web" in label.lower()
        )

    def test_get_routing_label_rag(self):
        from backend.api.node_labels import get_routing_label

        label = get_routing_label("rag_agent")
        assert isinstance(label, str)
        assert len(label) > 0

    def test_get_routing_label_unknown(self):
        from backend.api.node_labels import get_routing_label

        label = get_routing_label("agente_desconhecido")
        assert isinstance(label, str)
        assert len(label) > 0

    def test_every_soul_in_catalog_has_a_real_label_not_generic(self):
        """Achado real: node_labels.py só cobria coder/search, as outras 8
        SOULs caíam no genérico "Processando…"/"Roteando para X…" cru. Toda
        entrada de SOUL_CATALOG precisa de label específico dos dois dicts."""
        from backend.agents.souls import SOUL_CATALOG
        from backend.api.node_labels import _ROUTING_LABELS, NODE_LABELS

        for name in SOUL_CATALOG:
            assert name in NODE_LABELS, f"{name} sem entrada em NODE_LABELS"
            assert NODE_LABELS[name] != "Processando…", (
                f"{name} caindo no label genérico"
            )
            assert name in _ROUTING_LABELS, f"{name} sem entrada em _ROUTING_LABELS"
            assert not _ROUTING_LABELS[name].startswith("Roteando para " + name), (
                f"{name} caindo no fallback genérico de roteamento"
            )

    def test_node_event_with_label_in_sse(self):
        """NodeEvent com started deve emitir label semântico no campo node_label."""
        from backend.api.schemas import NodeEvent, encode_event

        e = NodeEvent(node="search_agent", status="started")
        data = json.loads(encode_event(e).removeprefix("data: ").strip())
        # Após D2, o NodeEvent deve incluir node_label
        assert "node_label" in data
        assert isinstance(data["node_label"], str)
        assert len(data["node_label"]) > 0


# ===========================================================================
# Duration badges: duration_ms no NodeEvent de fim
#
# NodeEvent/NodeStatus modelava nós discretos de um grafo compilado — o
# motor nativo (loop imperativo, sem nós nomeados) nunca
# emite ``NodeStatus`` (confirmado: nenhum ``emit(NodeStatus(...))`` existe
# em ``backend/engine/conversation_loop.py``). Só o contrato do schema em si
# (usado por outros produtores, se algum dia existirem) continua coberto.
# ===========================================================================


class TestNodeEventDuration:
    def test_node_event_duration_schema(self):
        from backend.api.schemas import NodeEvent

        e = NodeEvent(node="n", status="finished", duration_ms=1337)
        assert e.duration_ms == 1337


# ===========================================================================
# Comportamentos existentes do adapters que não devem regredir
# ===========================================================================


class TestAdaptersRegression:
    """Testes de não-regressão: comportamentos correntes do bridge SSE
    (``stream_engine_events``) que não podem regredir. Os testes de mapeamento
    token/tool_call/tool_result já vivem em ``test_adapters_streaming.py`` e
    ``test_adapters_tool_activity.py`` — aqui só o envelope genérico do
    stream (thread/done/error), que qualquer ``run`` precisa respeitar."""

    @pytest.mark.asyncio
    async def test_stream_always_starts_with_thread_event(self):
        from backend.api.native_stream import stream_engine_events

        async def run(on_event):
            return "stop"

        payloads = [
            _parse(s) async for s in stream_engine_events(run, thread_id="t-xyz")
        ]

        assert payloads[0]["type"] == "thread"
        assert payloads[0]["thread_id"] == "t-xyz"

    @pytest.mark.asyncio
    async def test_stream_always_ends_with_done_event(self):
        from backend.api.native_stream import stream_engine_events

        async def run(on_event):
            return "stop"

        payloads = [
            _parse(s) async for s in stream_engine_events(run, thread_id="t-xyz")
        ]

        assert payloads[-1]["type"] == "done"
        assert payloads[-1]["thread_id"] == "t-xyz"

    @pytest.mark.asyncio
    async def test_stream_emits_error_event_on_exception(self):
        from backend.api.native_stream import stream_engine_events

        async def run(on_event):
            raise RuntimeError("erro simulado")

        payloads = [
            _parse(s) async for s in stream_engine_events(run, thread_id="t-err")
        ]

        error_events = [p for p in payloads if p["type"] == "error"]
        assert len(error_events) >= 1
        # O bridge classifica o erro e NÃO vaza a exceção crua ao usuário
        # (ver adapters.classify_stream_error): erro genérico → STREAM_ERROR
        # com mensagem limpa. A mensagem técnica fica só no log do servidor.
        assert error_events[0]["code"] == "STREAM_ERROR"
        assert "erro simulado" not in error_events[0]["message"]
        assert error_events[0]["message"]
