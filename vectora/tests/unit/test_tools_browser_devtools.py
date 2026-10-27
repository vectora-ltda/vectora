"""Tools de observabilidade/controle avançado do browser (multi-aba,
console/network log) — mocka backend.browser.session (unit, sem depender de
Chromium instalado; a camada de sessão já é testada contra Chromium real em
test_browser_session_real.py)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.tools import browser_devtools as bd
from backend.tools.context import ToolContext


def _ctx(workspace_id: str = "ws1") -> ToolContext:
    return ToolContext(workspace_id=workspace_id)


# ---------------------------------------------------------------------------
# browser_list_tabs / browser_new_tab / browser_close_tab / browser_select_tab
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_list_tabs_retorna_abas_da_sessao(monkeypatch):
    monkeypatch.setattr(
        bd,
        "list_tabs",
        AsyncMock(
            return_value=[
                {"tab_id": "t1", "url": "https://a.test", "active": True},
                {"tab_id": "t2", "url": "https://b.test", "active": False},
            ]
        ),
    )

    result = json.loads(await bd.browser_list_tabs(ctx=_ctx()))

    assert len(result["tabs"]) == 2
    assert result["tabs"][0]["active"] is True


@pytest.mark.asyncio
async def test_browser_new_tab_cria_aba_e_retorna_id(monkeypatch):
    monkeypatch.setattr(bd, "new_tab", AsyncMock(return_value="tab-abc"))

    result = json.loads(await bd.browser_new_tab(url="https://example.com", ctx=_ctx()))

    assert result == {"status": "ok", "tab_id": "tab-abc"}


@pytest.mark.asyncio
async def test_browser_new_tab_bloqueia_redirect_para_destino_privado(monkeypatch):
    """Erro/regressão: redirects de uma nova aba também são filtrados."""
    monkeypatch.setattr(bd, "new_tab", AsyncMock(return_value="tab-abc"))
    monkeypatch.setattr(
        bd,
        "list_tabs",
        AsyncMock(
            return_value=[{"tab_id": "tab-abc", "url": "http://127.0.0.1:8080/private"}]
        ),
    )
    close_tab = AsyncMock(return_value=True)
    monkeypatch.setattr(bd, "close_tab", close_tab)

    result = json.loads(await bd.browser_new_tab(url="https://example.com", ctx=_ctx()))

    assert result["status"] == "error"
    assert "redirect bloqueado" in result["error"]
    close_tab.assert_awaited_once_with("ws1", "tab-abc")


@pytest.mark.asyncio
async def test_browser_new_tab_falha_devolve_erro_tipado(monkeypatch):
    monkeypatch.setattr(
        bd, "new_tab", AsyncMock(side_effect=RuntimeError("chromium crashed"))
    )

    result = json.loads(await bd.browser_new_tab(ctx=_ctx()))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_close_tab_sem_sessao_retorna_erro(monkeypatch):
    monkeypatch.setattr(bd, "has_browser_session", lambda _wid: False)

    result = json.loads(await bd.browser_close_tab(tab_id="t1", ctx=_ctx()))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_close_tab_id_inexistente_retorna_erro(monkeypatch):
    monkeypatch.setattr(bd, "has_browser_session", lambda _wid: True)
    monkeypatch.setattr(bd, "close_tab", AsyncMock(return_value=False))

    result = json.loads(await bd.browser_close_tab(tab_id="nao-existe", ctx=_ctx()))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_close_tab_sucesso(monkeypatch):
    monkeypatch.setattr(bd, "has_browser_session", lambda _wid: True)
    monkeypatch.setattr(bd, "close_tab", AsyncMock(return_value=True))

    result = json.loads(await bd.browser_close_tab(tab_id="t1", ctx=_ctx()))

    assert result == {"status": "ok", "tab_id": "t1"}


@pytest.mark.asyncio
async def test_browser_select_tab_troca_aba_ativa(monkeypatch):
    monkeypatch.setattr(bd, "has_browser_session", lambda _wid: True)
    monkeypatch.setattr(bd, "select_tab", AsyncMock(return_value=True))

    result = json.loads(await bd.browser_select_tab(tab_id="t2", ctx=_ctx()))

    assert result == {"status": "ok", "tab_id": "t2"}


@pytest.mark.asyncio
async def test_browser_select_tab_inexistente_retorna_erro(monkeypatch):
    monkeypatch.setattr(bd, "has_browser_session", lambda _wid: True)
    monkeypatch.setattr(bd, "select_tab", AsyncMock(return_value=False))

    result = json.loads(await bd.browser_select_tab(tab_id="nao-existe", ctx=_ctx()))

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# browser_list_console_messages / browser_clear_console
# ---------------------------------------------------------------------------


def _fake_tab(console=None, network=None):
    return SimpleNamespace(
        console_log=list(console or []),
        network_log=list(network or []),
    )


@pytest.mark.asyncio
async def test_browser_list_console_messages_respeita_limit(monkeypatch):
    messages = [{"type": "log", "text": f"msg {i}"} for i in range(5)]
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: _fake_tab(messages))

    result = json.loads(await bd.browser_list_console_messages(limit=2, ctx=_ctx()))

    assert len(result["messages"]) == 2
    assert result["messages"][-1]["text"] == "msg 4"


@pytest.mark.asyncio
async def test_browser_list_console_messages_sem_sessao_retorna_erro(monkeypatch):
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: None)

    result = json.loads(await bd.browser_list_console_messages(ctx=_ctx()))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_clear_console_limpa_buffer(monkeypatch):
    tab = _fake_tab(console=[{"type": "log", "text": "x"}])
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)

    result = json.loads(await bd.browser_clear_console(ctx=_ctx()))

    assert result == {"status": "ok"}
    assert tab.console_log == []


# ---------------------------------------------------------------------------
# browser_list_network_requests / browser_get_network_request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_list_network_requests_filtra_por_resource_type(monkeypatch):
    entries = [
        {"request_id": "r1", "url": "https://a", "resource_type": "document"},
        {"request_id": "r2", "url": "https://b", "resource_type": "xhr"},
    ]
    monkeypatch.setattr(
        bd, "get_tab_state", lambda _wid, _tid: _fake_tab(network=entries)
    )

    result = json.loads(
        await bd.browser_list_network_requests(resource_type="xhr", ctx=_ctx())
    )

    assert len(result["requests"]) == 1
    assert result["requests"][0]["request_id"] == "r2"


@pytest.mark.asyncio
async def test_browser_list_network_requests_sem_filtro_lista_todas(monkeypatch):
    entries = [
        {"request_id": "r1", "url": "https://a", "resource_type": "document"},
        {"request_id": "r2", "url": "https://b", "resource_type": "xhr"},
    ]
    monkeypatch.setattr(
        bd, "get_tab_state", lambda _wid, _tid: _fake_tab(network=entries)
    )

    result = json.loads(await bd.browser_list_network_requests(ctx=_ctx()))

    assert len(result["requests"]) == 2


@pytest.mark.asyncio
async def test_browser_get_network_request_encontrada(monkeypatch):
    entries = [{"request_id": "r1", "url": "https://a", "status": 200}]
    monkeypatch.setattr(
        bd, "get_tab_state", lambda _wid, _tid: _fake_tab(network=entries)
    )

    result = json.loads(
        await bd.browser_get_network_request(request_id="r1", ctx=_ctx())
    )

    assert result == entries[0]


@pytest.mark.asyncio
async def test_browser_get_network_request_nao_encontrada_retorna_erro(monkeypatch):
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: _fake_tab(network=[]))

    result = json.loads(
        await bd.browser_get_network_request(request_id="nao-existe", ctx=_ctx())
    )

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# browser_evaluate
# ---------------------------------------------------------------------------


def _fake_tab_with_page(evaluate_result=None, evaluate_error=None):
    async def _evaluate(script):
        if evaluate_error is not None:
            raise evaluate_error
        return evaluate_result

    page = SimpleNamespace(evaluate=_evaluate)
    return SimpleNamespace(page=page)


@pytest.mark.asyncio
async def test_browser_evaluate_retorna_resultado_serializado(monkeypatch):
    monkeypatch.setattr(
        bd, "get_tab_state", lambda _wid, _tid: _fake_tab_with_page(evaluate_result=42)
    )

    result = json.loads(await bd.browser_evaluate(script="1 + 41", ctx=_ctx()))

    assert result == {"status": "ok", "result": 42}


@pytest.mark.asyncio
async def test_browser_evaluate_sem_sessao_retorna_erro(monkeypatch):
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: None)

    result = json.loads(await bd.browser_evaluate(script="1", ctx=_ctx()))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_evaluate_erro_de_script_nao_propaga(monkeypatch):
    monkeypatch.setattr(
        bd,
        "get_tab_state",
        lambda _wid, _tid: _fake_tab_with_page(
            evaluate_error=RuntimeError("SyntaxError: unexpected token")
        ),
    )

    result = json.loads(await bd.browser_evaluate(script="{{{", ctx=_ctx()))

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# browser_set_dialog_policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_set_dialog_policy_action_invalida_retorna_erro():
    result = json.loads(await bd.browser_set_dialog_policy(action="close", ctx=_ctx()))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_set_dialog_policy_sem_sessao_retorna_erro(monkeypatch):
    monkeypatch.setattr(bd, "set_dialog_policy", lambda *a, **k: False)

    result = json.loads(await bd.browser_set_dialog_policy(action="accept", ctx=_ctx()))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_set_dialog_policy_sucesso(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bd,
        "set_dialog_policy",
        lambda wid, action, prompt_text=None, tab_id=None: (
            calls.append((wid, action, prompt_text, tab_id)) or True
        ),
    )

    result = json.loads(
        await bd.browser_set_dialog_policy(
            action="accept", prompt_text="ok", ctx=_ctx()
        )
    )

    assert result == {"status": "ok"}
    assert calls == [("ws1", "accept", "ok", None)]


# ---------------------------------------------------------------------------
# browser_emulate
# ---------------------------------------------------------------------------


def _fake_tab_for_emulate():
    page = SimpleNamespace(set_viewport_size=AsyncMock())
    cdp = SimpleNamespace(send=AsyncMock())
    return SimpleNamespace(page=page, cdp=cdp)


@pytest.mark.asyncio
async def test_browser_emulate_sem_sessao_retorna_erro(monkeypatch):
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: None)

    result = json.loads(
        await bd.browser_emulate(viewport_width=375, viewport_height=812, ctx=_ctx())
    )

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_emulate_aplica_viewport(monkeypatch):
    tab = _fake_tab_for_emulate()
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)

    result = json.loads(
        await bd.browser_emulate(viewport_width=375, viewport_height=812, ctx=_ctx())
    )

    assert result == {"status": "ok", "applied": ["viewport"]}
    tab.page.set_viewport_size.assert_awaited_once_with({"width": 375, "height": 812})


@pytest.mark.asyncio
async def test_browser_emulate_aplica_cpu_throttle_via_cdp(monkeypatch):
    tab = _fake_tab_for_emulate()
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)

    result = json.loads(await bd.browser_emulate(cpu_throttle=4, ctx=_ctx()))

    assert result == {"status": "ok", "applied": ["cpu_throttle"]}
    tab.cdp.send.assert_awaited_once_with("Emulation.setCPUThrottlingRate", {"rate": 4})


@pytest.mark.asyncio
async def test_browser_emulate_network_throttle_invalido_retorna_erro(monkeypatch):
    tab = _fake_tab_for_emulate()
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)

    result = json.loads(
        await bd.browser_emulate(network_throttle="not-a-profile", ctx=_ctx())
    )

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_emulate_network_throttle_valido_chama_cdp(monkeypatch):
    tab = _fake_tab_for_emulate()
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)

    result = json.loads(
        await bd.browser_emulate(network_throttle="slow-3g", ctx=_ctx())
    )

    assert result == {"status": "ok", "applied": ["network_throttle"]}
    tab.cdp.send.assert_awaited_once()
    assert tab.cdp.send.await_args.args[0] == "Network.emulateNetworkConditions"


@pytest.mark.asyncio
async def test_browser_emulate_sem_parametros_nao_aplica_nada(monkeypatch):
    tab = _fake_tab_for_emulate()
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)

    result = json.loads(await bd.browser_emulate(ctx=_ctx()))

    assert result == {"status": "ok", "applied": []}
    tab.page.set_viewport_size.assert_not_awaited()
    tab.cdp.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# browser_start_trace / browser_stop_trace
# ---------------------------------------------------------------------------


class _FakeCDPForTrace:
    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}

        async def _send(method, params=None):
            if method == "Tracing.end":
                for h in self._handlers.get("Tracing.tracingComplete", []):
                    h({})

        self.send = AsyncMock(side_effect=_send)

    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event, params):
        for h in self._handlers.get(event, []):
            h(params)


def _fake_tab_for_trace():
    return SimpleNamespace(cdp=_FakeCDPForTrace())


@pytest.mark.asyncio
async def test_browser_start_trace_sem_sessao_retorna_erro(monkeypatch):
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: None)

    result = json.loads(await bd.browser_start_trace(ctx=_ctx()))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_start_trace_ja_em_andamento_retorna_erro(monkeypatch):
    tab = _fake_tab_for_trace()
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)

    first = json.loads(await bd.browser_start_trace(ctx=_ctx()))
    second = json.loads(await bd.browser_start_trace(ctx=_ctx()))

    assert first["status"] == "ok"
    assert second["status"] == "error"

    # limpa o estado global pra não vazar entre testes
    await bd.browser_stop_trace(ctx=_ctx())


@pytest.mark.asyncio
async def test_stop_trace_sem_start_retorna_erro(monkeypatch):
    tab = _fake_tab_for_trace()
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)

    result = json.loads(await bd.browser_stop_trace(ctx=_ctx()))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_start_e_stop_trace_resume_eventos_coletados(monkeypatch, tmp_path):
    tab = _fake_tab_for_trace()
    ws = SimpleNamespace(cwd=str(tmp_path))
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)
    monkeypatch.setattr(
        "backend.workspace.workspace.workspace_registry",
        SimpleNamespace(get=lambda _id: ws),
    )

    await bd.browser_start_trace(ctx=_ctx())
    tab.cdp.emit(
        "Tracing.dataCollected",
        {"value": [{"cat": "toplevel", "name": "a"}, {"cat": "toplevel", "name": "b"}]},
    )

    result = json.loads(await bd.browser_stop_trace(ctx=_ctx()))

    assert result["status"] == "ok"
    assert result["summary"]["event_count"] == 2
    assert result["summary"]["categories"] == {"toplevel": 2}
    assert result["artifact_path"] is not None
    assert Path(result["artifact_path"]).exists()


# ---------------------------------------------------------------------------
# browser_take_heap_snapshot
# ---------------------------------------------------------------------------


def _sample_heap_snapshot() -> dict:
    return {
        "snapshot": {
            "meta": {
                "node_fields": [
                    "type",
                    "name",
                    "id",
                    "self_size",
                    "edge_count",
                    "trace_node_id",
                    "detachedness",
                ],
                "node_types": [
                    [
                        "hidden",
                        "array",
                        "string",
                        "object",
                        "code",
                        "closure",
                        "regexp",
                        "number",
                        "native",
                        "synthetic",
                        "concatenated string",
                        "sliced string",
                        "symbol",
                        "bigint",
                    ],
                    "string",
                    "number",
                    "number",
                    "number",
                    "number",
                    "number",
                ],
            }
        },
        "nodes": [3, 0, 1, 100, 0, 0, 0, 3, 1, 2, 50, 0, 0, 0],
        "edges": [],
        "strings": ["Foo", "Bar"],
    }


def _fake_tab_for_heap(snapshot: dict):
    cdp = SimpleNamespace()
    payload = json.dumps(snapshot)

    async def _send(method, params=None):
        if method == "HeapProfiler.takeHeapSnapshot":
            for h in cdp._handlers.get("HeapProfiler.addHeapSnapshotChunk", []):
                h({"chunk": payload})

    cdp._handlers = {}
    cdp.on = lambda event, handler: cdp._handlers.setdefault(event, []).append(handler)
    cdp.send = AsyncMock(side_effect=_send)
    return SimpleNamespace(cdp=cdp)


@pytest.mark.asyncio
async def test_browser_take_heap_snapshot_sem_sessao_retorna_erro(monkeypatch):
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: None)

    result = json.loads(await bd.browser_take_heap_snapshot(ctx=_ctx()))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_take_heap_snapshot_resume_top_construtores(
    monkeypatch, tmp_path
):
    tab = _fake_tab_for_heap(_sample_heap_snapshot())
    ws = SimpleNamespace(cwd=str(tmp_path))
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)
    monkeypatch.setattr(
        "backend.workspace.workspace.workspace_registry",
        SimpleNamespace(get=lambda _id: ws),
    )

    result = json.loads(await bd.browser_take_heap_snapshot(ctx=_ctx()))

    assert result["status"] == "ok"
    top = {c["constructor"]: c for c in result["top_constructors"]}
    assert top["object:Foo"]["total_size"] == 100
    assert top["object:Bar"]["total_size"] == 50
    assert result["artifact_path"] is not None


# ---------------------------------------------------------------------------
# browser_lighthouse_audit
# ---------------------------------------------------------------------------


class _FakeLighthouseProc:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_browser_lighthouse_audit_npx_ausente_retorna_erro_tipado(monkeypatch):
    monkeypatch.setattr(
        bd.asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=FileNotFoundError()),
    )

    result = json.loads(
        await bd.browser_lighthouse_audit(url="http://localhost:3000", ctx=_ctx())
    )

    assert result["status"] == "error"
    assert "node" in result["error"].lower() or "npx" in result["error"].lower()


@pytest.mark.asyncio
async def test_browser_lighthouse_audit_parseia_scores_e_oportunidades(monkeypatch):
    report = {
        "categories": {
            "performance": {"score": 0.8},
            "accessibility": {"score": 0.9},
        },
        "audits": {
            "render-blocking-resources": {
                "id": "render-blocking-resources",
                "title": "Eliminate render-blocking resources",
                "numericValue": 300,
                "details": {"type": "opportunity"},
            },
            "not-an-opportunity": {
                "id": "not-an-opportunity",
                "title": "x",
                "details": {"type": "table"},
            },
        },
    }
    monkeypatch.setattr(
        bd.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=_FakeLighthouseProc(json.dumps(report).encode("utf-8"))),
    )

    result = json.loads(
        await bd.browser_lighthouse_audit(url="http://localhost:3000", ctx=_ctx())
    )

    assert result["status"] == "ok"
    assert result["scores"]["performance"] == 0.8
    assert len(result["top_opportunities"]) == 1
    assert result["top_opportunities"][0]["id"] == "render-blocking-resources"


@pytest.mark.asyncio
async def test_browser_lighthouse_audit_exit_code_nao_zero_retorna_erro(monkeypatch):
    monkeypatch.setattr(
        bd.asyncio,
        "create_subprocess_exec",
        AsyncMock(
            return_value=_FakeLighthouseProc(b"", b"chrome not found", returncode=1)
        ),
    )

    result = json.loads(
        await bd.browser_lighthouse_audit(url="http://localhost:3000", ctx=_ctx())
    )

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_lighthouse_audit_sem_url_usa_url_da_aba_ativa(monkeypatch):
    tab = SimpleNamespace(page=SimpleNamespace(url="http://localhost:9999"))
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)

    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeLighthouseProc(
            json.dumps({"categories": {}, "audits": {}}).encode()
        )

    monkeypatch.setattr(bd.asyncio, "create_subprocess_exec", _fake_exec)

    result = json.loads(await bd.browser_lighthouse_audit(ctx=_ctx()))

    assert result["status"] == "ok"
    assert "http://localhost:9999" in captured["args"]


@pytest.mark.asyncio
async def test_browser_lighthouse_audit_sem_url_e_sem_sessao_retorna_erro(monkeypatch):
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: None)

    result = json.loads(await bd.browser_lighthouse_audit(ctx=_ctx()))
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# browser_snapshot / _build_ax_outline
# ---------------------------------------------------------------------------


_AX_NODES = [
    {
        "nodeId": "1",
        "role": {"value": "RootWebArea"},
        "name": {"value": "Página de teste"},
        "backendDOMNodeId": 100,
        "childIds": ["2", "3"],
    },
    {
        "nodeId": "2",
        "role": {"value": "generic"},
        "name": {"value": ""},
        "ignored": True,
        "backendDOMNodeId": 101,
        "childIds": ["4"],
    },
    {
        "nodeId": "3",
        "role": {"value": "button"},
        "name": {"value": "Enviar"},
        "backendDOMNodeId": 102,
        "childIds": [],
    },
    {
        "nodeId": "4",
        "role": {"value": "textbox"},
        "name": {"value": "Email"},
        "backendDOMNodeId": 103,
        "childIds": [],
    },
]


def test_build_ax_outline_inclui_uid_por_no_nao_ignorado():
    outline = bd._build_ax_outline(_AX_NODES)

    assert 'button "Enviar" [uid=102]' in outline
    assert 'textbox "Email" [uid=103]' in outline


def test_build_ax_outline_pula_nos_ignorados_mas_desce_pros_filhos():
    outline = bd._build_ax_outline(_AX_NODES)

    # nó "generic" (ignored=True) não aparece, mas seu filho "Email" sim —
    # senão a árvore perderia o textbox escondido atrás do wrapper.
    assert "generic" not in outline
    assert 'textbox "Email"' in outline


def test_build_ax_outline_arvore_vazia():
    assert bd._build_ax_outline([]) == "(árvore de acessibilidade vazia)"


def test_build_ax_outline_respeita_max_nodes():
    outline = bd._build_ax_outline(_AX_NODES, max_nodes=1)

    assert outline.count("[uid=") == 1


def _fake_tab_for_snapshot(ax_nodes: list[dict]):
    cdp = SimpleNamespace(send=AsyncMock(side_effect=[{}, {"nodes": ax_nodes}]))
    return SimpleNamespace(cdp=cdp)


@pytest.mark.asyncio
async def test_browser_snapshot_sem_sessao_retorna_erro(monkeypatch):
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: None)

    result = await bd.browser_snapshot(ctx=_ctx())

    assert "Nenhuma sess" in result


@pytest.mark.asyncio
async def test_browser_snapshot_retorna_arvore_com_uid(monkeypatch):
    tab = _fake_tab_for_snapshot(_AX_NODES)
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)

    result = await bd.browser_snapshot(ctx=_ctx())

    assert "[uid=102]" in result
    tab.cdp.send.assert_any_await("Accessibility.enable")


@pytest.mark.asyncio
async def test_browser_snapshot_erro_de_cdp_nao_propaga(monkeypatch):
    cdp = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("CDP desconectado")))
    monkeypatch.setattr(
        bd, "get_tab_state", lambda _wid, _tid: SimpleNamespace(cdp=cdp)
    )

    result = json.loads(await bd.browser_snapshot(ctx=_ctx()))

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# browser_analyze_trace / _analyze_trace_events
# ---------------------------------------------------------------------------


def test_analyze_trace_events_arvore_vazia():
    analysis = bd._analyze_trace_events([])

    assert analysis == {
        "total_duration_ms": 0,
        "lcp_ms": None,
        "long_task_count": 0,
        "top_long_tasks": [],
    }


def test_analyze_trace_events_calcula_lcp_relativo_ao_navigation_start():
    events = [
        {"name": "navigationStart", "ts": 1_000_000, "cat": "blink.user_timing"},
        {
            "name": "largestContentfulPaint::Candidate",
            "ts": 2_500_000,
            "cat": "loading",
        },
    ]

    analysis = bd._analyze_trace_events(events)

    assert analysis["lcp_ms"] == 1500.0


def test_analyze_trace_events_identifica_long_tasks_acima_de_50ms():
    events = [
        {"name": "navigationStart", "ts": 0},
        {"name": "RunTask", "ts": 100_000, "dur": 80_000},  # 80ms — long task
        {"name": "RunTask", "ts": 300_000, "dur": 10_000},  # 10ms — não conta
    ]

    analysis = bd._analyze_trace_events(events)

    assert analysis["long_task_count"] == 1
    assert analysis["top_long_tasks"][0]["duration_ms"] == 80.0
    assert analysis["top_long_tasks"][0]["start_ms"] == 100.0


@pytest.mark.asyncio
async def test_browser_analyze_trace_artifact_inexistente_retorna_erro_tipado(
    tmp_path,
):
    result = json.loads(
        await bd.browser_analyze_trace(artifact_path=str(tmp_path / "nao-existe.json"))
    )

    assert result["status"] == "error"
    assert "não encontrado" in result["error"]


@pytest.mark.asyncio
async def test_browser_analyze_trace_le_artifact_e_analisa(tmp_path):
    artifact = tmp_path / "trace-abc.json"
    artifact.write_text(
        json.dumps(
            [
                {"name": "navigationStart", "ts": 0},
                {"name": "RunTask", "ts": 0, "dur": 60_000},
            ]
        ),
        encoding="utf-8",
    )

    result = json.loads(await bd.browser_analyze_trace(artifact_path=str(artifact)))

    assert result["status"] == "ok"
    assert result["analysis"]["long_task_count"] == 1


@pytest.mark.asyncio
async def test_browser_analyze_trace_json_invalido_retorna_erro_tipado(tmp_path):
    artifact = tmp_path / "trace-corrompido.json"
    artifact.write_text("{ nao-eh-json-valido", encoding="utf-8")

    result = json.loads(await bd.browser_analyze_trace(artifact_path=str(artifact)))

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# browser_compare_heap_snapshots / _diff_heap_summaries
# ---------------------------------------------------------------------------


def test_diff_heap_summaries_calcula_delta_positivo_e_negativo():
    before = [{"constructor": "Foo", "total_size": 100, "count": 1}]
    after = [
        {"constructor": "Foo", "total_size": 300, "count": 2},
        {"constructor": "Bar", "total_size": 50, "count": 1},
    ]

    diff = bd._diff_heap_summaries(before, after)
    by_key = {d["constructor"]: d for d in diff}

    assert by_key["Foo"]["size_delta"] == 200
    assert by_key["Foo"]["count_delta"] == 1
    assert by_key["Bar"]["size_delta"] == 50


def test_diff_heap_summaries_construtor_que_sumiu_fica_negativo():
    before = [{"constructor": "Leaked", "total_size": 500, "count": 3}]
    after: list[dict] = []

    diff = bd._diff_heap_summaries(before, after)

    assert diff[0]["constructor"] == "Leaked"
    assert diff[0]["size_delta"] == -500


@pytest.mark.asyncio
async def test_browser_compare_heap_snapshots_artifact_inexistente(tmp_path):
    result = json.loads(
        await bd.browser_compare_heap_snapshots(
            before_path=str(tmp_path / "antes.json"),
            after_path=str(tmp_path / "depois.json"),
        )
    )

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_compare_heap_snapshots_retorna_o_que_cresceu(tmp_path):
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(_sample_heap_snapshot()), encoding="utf-8")

    grown = _sample_heap_snapshot()
    # Duplica o nó "object:Bar" (nodes[7:14]) — mesma memória some do "Foo"
    # e reaparece a mais no "Bar", simulando crescimento real de heap.
    grown["nodes"] = grown["nodes"] + grown["nodes"][7:14]
    after_path.write_text(json.dumps(grown), encoding="utf-8")

    result = json.loads(
        await bd.browser_compare_heap_snapshots(
            before_path=str(before_path), after_path=str(after_path)
        )
    )

    assert result["status"] == "ok"
    growing = {d["constructor"]: d for d in result["top_growing"]}
    assert "object:Bar" in growing
    assert growing["object:Bar"]["size_delta"] > 0


# ---------------------------------------------------------------------------
# browser_screencast_start / browser_screencast_stop
# ---------------------------------------------------------------------------


class _FakeCDPForScreencast:
    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}
        self.send = AsyncMock()

    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event, params):
        for h in self._handlers.get(event, []):
            h(params)


def _fake_tab_for_screencast():
    return SimpleNamespace(cdp=_FakeCDPForScreencast())


@pytest.mark.asyncio
async def test_browser_screencast_start_sem_sessao_retorna_erro(monkeypatch):
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: None)

    result = json.loads(await bd.browser_screencast_start(ctx=_ctx()))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_browser_screencast_start_ja_em_andamento_retorna_erro(monkeypatch):
    tab = _fake_tab_for_screencast()
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)

    first = json.loads(await bd.browser_screencast_start(ctx=_ctx()))
    second = json.loads(await bd.browser_screencast_start(ctx=_ctx()))

    assert first["status"] == "ok"
    assert second["status"] == "error"

    await bd.browser_screencast_stop(ctx=_ctx())


@pytest.mark.asyncio
async def test_browser_screencast_stop_sem_start_retorna_erro(monkeypatch):
    tab = _fake_tab_for_screencast()
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)

    result = json.loads(await bd.browser_screencast_stop(ctx=_ctx()))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_screencast_acumula_frames_e_persiste_artifact(monkeypatch, tmp_path):
    tab = _fake_tab_for_screencast()
    ws = SimpleNamespace(cwd=str(tmp_path))
    monkeypatch.setattr(bd, "get_tab_state", lambda _wid, _tid: tab)
    monkeypatch.setattr(
        "backend.workspace.workspace.workspace_registry",
        SimpleNamespace(get=lambda _id: ws),
    )

    await bd.browser_screencast_start(ctx=_ctx())
    tab.cdp.emit("Page.screencastFrame", {"data": "base64frame1", "sessionId": 1})
    tab.cdp.emit("Page.screencastFrame", {"data": "base64frame2", "sessionId": 1})
    # dá o controle de volta ao loop de eventos pra tasks de ack criadas
    # dentro do handler síncrono terminarem antes do assert abaixo.
    await asyncio.sleep(0)

    result = json.loads(await bd.browser_screencast_stop(ctx=_ctx()))

    assert result["status"] == "ok"
    assert result["frame_count"] == 2
    assert result["artifact_path"] is not None
    saved = json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))
    assert saved["frame_count"] == 2
    assert saved["frames"] == ["base64frame1", "base64frame2"]

    # ack de cada frame foi enviado de volta ao CDP (senão o Chrome real
    # para de mandar frames depois do primeiro).
    ack_calls = [
        c
        for c in tab.cdp.send.await_args_list
        if c.args and c.args[0] == "Page.screencastFrameAck"
    ]
    assert len(ack_calls) == 2
