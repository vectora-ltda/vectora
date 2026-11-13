"""Hooks determinísticos do harness (``pre_tool`` e ``post_tool``)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import create_model

from backend.engine import hooks
from backend.engine.tool_batch import (
    _redact_secrets,
    drain_hook_outcomes,
    execute_tool_batch,
)
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, ToolRegistry, ToolSpec
from backend.vtypes.message import ToolCall
from backend.workspace.workspace_config import (
    HookRule,
    HooksSection,
    hook_rules_digest,
    load_workspace_config,
)

PY = json.dumps(sys.executable)


def _rule(command: list[str], **overrides: object) -> HookRule:
    data: dict[str, object] = {
        "id": "regra",
        "event": "pre_tool",
        "command": command,
        **overrides,
    }
    return HookRule.model_validate(data)


def _script(tmp_path: Path, body: str) -> list[str]:
    path = tmp_path / "hook.py"
    path.write_text(body, encoding="utf-8")
    return [sys.executable, str(path)]


def _ctx() -> ToolContext:
    return ToolContext(user_id="u1", thread_id="t1")


async def _run(rule: HookRule, tmp_path: Path, **overrides: Any) -> hooks.HookOutcome:
    params: dict[str, Any] = {
        "ctx": _ctx(),
        "tool_name": "run_command",
        "tool_args": {"command": "ls"},
        "result": None,
        "root": tmp_path,
        "approved": True,
        "sandboxed": False,
        "workspace_id": "w1",
        "redact": _redact_secrets,
        **overrides,
    }
    return await hooks.run_hook(rule, **params)


# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------


def test_regra_valida_tem_padroes_seguros() -> None:
    rule = _rule(["python", "check.py"])
    assert rule.mode == "blocking"
    assert rule.timeout_s == 10.0
    assert rule.tools == []


@pytest.mark.parametrize(
    "campos",
    [
        {"id": "Inválido!"},
        {"event": "on_save"},
        {"command": []},
        {"timeout_s": 0.1},
        {"timeout_s": 600},
        {"mode": "talvez"},
        {"extra": 1},
    ],
)
def test_regra_invalida_e_recusada(campos: dict[str, object]) -> None:
    base: dict[str, object] = {
        "id": "ok",
        "event": "pre_tool",
        "command": ["python", "x.py"],
    }
    with pytest.raises(ValueError, match="validation error"):
        HookRule.model_validate({**base, **campos})


def test_secao_descarta_regras_invalidas_sem_perder_as_validas() -> None:
    section = HooksSection.model_validate(
        {
            "rules": [
                {"id": "ok", "event": "post_tool", "command": ["python", "x.py"]},
                {"id": "ruim", "event": "nenhum", "command": ["python"]},
                "não é um objeto",
            ]
        }
    )
    assert [rule.id for rule in section.rules] == ["ok"]
    assert HooksSection.model_validate({"rules": "texto"}).rules == []


# --------------------------------------------------------------------------
# Resposta do hook
# --------------------------------------------------------------------------


def test_parse_decision() -> None:
    assert hooks.parse_decision("") == ("allow", "")
    assert hooks.parse_decision('{"decision": "allow"}') == ("allow", "")
    assert hooks.parse_decision('{"decision": "deny", "reason": "não"}') == (
        "deny",
        "não",
    )
    for invalida in ("texto", "[1]", '{"decision": "talvez"}', "{}"):
        with pytest.raises(ValueError, match=r"hook|decision"):
            hooks.parse_decision(invalida)


# --------------------------------------------------------------------------
# Execução
# --------------------------------------------------------------------------


async def test_hook_que_permite(tmp_path: Path) -> None:
    rule = _rule(_script(tmp_path, "import sys; sys.stdin.read()"))
    outcome = await _run(rule, tmp_path)
    assert outcome.outcome == "allowed"
    assert outcome.blocked is False


async def test_hook_que_nega_bloqueia_com_o_motivo(tmp_path: Path) -> None:
    body = 'import json,sys; sys.stdin.read(); print(json.dumps({"decision":"deny","reason":"rm proibido"}))'
    outcome = await _run(_rule(_script(tmp_path, body)), tmp_path)
    assert outcome.outcome == "denied"
    assert outcome.blocked is True
    assert outcome.reason == "rm proibido"
    assert "hook_denied: regra: rm proibido" in hooks.block_message(outcome)


@pytest.mark.parametrize(
    ("body", "esperado"),
    [
        ("import sys; sys.exit(3)", "código 3"),
        ("print('isto não é json')", "não é JSON"),
        ('print("{\\"decision\\": \\"talvez\\"}")', "allow ou deny"),
    ],
)
async def test_hook_bloqueante_com_falha_ou_resposta_invalida_falha_fechado(
    tmp_path: Path, body: str, esperado: str
) -> None:
    outcome = await _run(_rule(_script(tmp_path, body)), tmp_path)
    assert outcome.outcome == "error"
    assert outcome.blocked is True
    assert esperado in outcome.reason


async def test_comando_inexistente_bloqueia_sem_vazar_detalhes(tmp_path: Path) -> None:
    outcome = await _run(_rule(["/nao/existe/hook"]), tmp_path)
    assert outcome.outcome == "error"
    assert outcome.blocked is True
    assert "/nao/existe" not in outcome.reason


async def test_timeout_bloqueia_e_mata_o_processo(tmp_path: Path) -> None:
    rule = _rule(_script(tmp_path, "import time; time.sleep(30)"), timeout_s=0.5)
    outcome = await _run(rule, tmp_path)
    assert outcome.outcome == "timeout"
    assert outcome.blocked is True
    assert outcome.duration_ms < 5_000


async def test_timeout_encerra_tambem_os_processos_filhos(tmp_path: Path) -> None:
    marker = tmp_path / "filho-rodou"
    body = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', "
        f"\"import time, pathlib; time.sleep(1); pathlib.Path({str(marker)!r}).write_text('x')\"])\n"
        "time.sleep(30)\n"
    )
    outcome = await _run(_rule(_script(tmp_path, body), timeout_s=0.5), tmp_path)
    assert outcome.outcome == "timeout"
    await asyncio.sleep(1.6)
    assert not marker.exists()


async def test_hook_no_sandbox_recebe_ambiente_minimo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "segredo-do-backend")
    captured: dict[str, Any] = {}

    class FakeWorker:
        async def request(self, op: str, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"stdout": "", "exit_code": 0}

    async def get_or_spawn(*_args: Any, **_kwargs: Any) -> FakeWorker:
        return FakeWorker()

    from backend.sandbox.workspace_jail import jail_manager

    monkeypatch.setattr(jail_manager, "get_or_spawn", get_or_spawn)
    rule = _rule(["true"])
    code, _ = await hooks._run_command(
        rule, "{}", tmp_path, sandboxed=True, workspace_id="w1"
    )
    assert code == 0
    assert "OPENAI_API_KEY" not in captured["env"]
    assert captured["env"]["VECTORA_HOOK_ID"] == rule.id


async def test_hook_informativo_nunca_bloqueia(tmp_path: Path) -> None:
    rule = _rule(_script(tmp_path, "import sys; sys.exit(1)"), mode="informative")
    outcome = await _run(rule, tmp_path)
    assert outcome.outcome == "error"
    assert outcome.blocked is False
    negar = 'import json; print(json.dumps({"decision":"deny"}))'
    denied = await _run(_rule(_script(tmp_path, negar), mode="informative"), tmp_path)
    assert denied.outcome == "denied"
    assert denied.blocked is False


async def test_workspace_sem_aprovacao_nao_executa_o_comando(tmp_path: Path) -> None:
    marca = tmp_path / "executou"
    body = f"open({str(marca)!r}, 'w').close()"
    outcome = await _run(_rule(_script(tmp_path, body)), tmp_path, approved=False)
    assert outcome.outcome == "skipped_not_approved"
    assert outcome.blocked is False
    assert not marca.exists()


async def test_hook_recebe_payload_redigido_e_ambiente_minimo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "chave-que-nao-pode-vazar")
    saida = tmp_path / "recebido.json"
    body = (
        "import json, os, sys\n"
        "payload = json.loads(sys.stdin.read())\n"
        f"open({str(saida)!r}, 'w').write(json.dumps({{'payload': payload, 'env': dict(os.environ)}}))\n"
    )
    rule = _rule(_script(tmp_path, body), event="post_tool")
    await _run(
        rule,
        tmp_path,
        tool_args={
            "command": "echo",
            "token": "Bearer abcdefghijklmnopqrstuvwxyz123456",
        },
        result="resultado da tool",
    )
    recebido = json.loads(saida.read_text(encoding="utf-8"))
    assert recebido["payload"]["version"] == hooks.PAYLOAD_VERSION
    assert recebido["payload"]["event"] == "post_tool"
    assert recebido["payload"]["tool"]["name"] == "run_command"
    assert recebido["payload"]["result"] == "resultado da tool"
    assert "abcdefghijklmnopqrstuvwxyz123456" not in json.dumps(recebido["payload"])
    assert "OPENAI_API_KEY" not in recebido["env"]
    assert recebido["env"]["VECTORA_HOOK_ID"] == "regra"


async def test_resultado_muito_grande_e_truncado_no_payload(tmp_path: Path) -> None:
    rule = _rule(["python"], event="post_tool")
    payload = json.loads(
        hooks.build_payload(
            rule,
            ctx=_ctx(),
            tool_name="x",
            tool_args={},
            result="a" * 100_000,
            redact=lambda text: text,
        )
    )
    assert len(payload["result"]) <= hooks.MAX_RESULT_CHARS + 1


# --------------------------------------------------------------------------
# Seleção de regras e integração com o lote de tools
# --------------------------------------------------------------------------


_ACTIVE: dict[str, SimpleNamespace] = {}


def _digest_of(tmp_path: Path) -> str | None:
    config = load_workspace_config(tmp_path)
    if config is None or not config.hooks.rules:
        return None
    return hook_rules_digest(config.hooks.rules)


def _workspace(tmp_path: Path, rules: str) -> None:
    """Grava o ``vectora.toml`` e aprova as regras como o registry faria."""
    (tmp_path / "vectora.toml").write_text(rules, encoding="utf-8")
    state = _ACTIVE.get("ws")
    if state is not None:
        state.hooks_rules_digest = _digest_of(tmp_path)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        cwd=str(tmp_path), hooks_approved=True, hooks_rules_digest=None, id="w1"
    )
    _ACTIVE["ws"] = state
    monkeypatch.setattr(hooks, "_workspace_for", lambda _ctx: state)
    return state


async def test_regra_alterada_depois_da_aprovacao_nao_executa(
    tmp_path: Path, workspace: SimpleNamespace
) -> None:
    allow = tmp_path / "allow.py"
    allow.write_text("import sys; sys.stdin.read()", encoding="utf-8")
    marker = tmp_path / "ran"
    spy = tmp_path / "spy.py"
    spy.write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('x')",
        encoding="utf-8",
    )
    _workspace(tmp_path, _toml_rule("guarda", "pre_tool", allow))
    # Alteração posterior à aprovação: o digest aprovado não bate mais.
    (tmp_path / "vectora.toml").write_text(
        _toml_rule("guarda", "pre_tool", spy), encoding="utf-8"
    )
    outcomes = await hooks.run_hooks(
        "pre_tool", ctx=_ctx(), tool_name="x", tool_args={}, redact=str
    )
    assert [item.outcome for item in outcomes] == ["skipped_not_approved"]
    assert "mudaram" in outcomes[0].reason
    assert not outcomes[0].blocked
    assert not marker.exists()


async def test_aprovacao_sem_digest_nao_cobre_as_regras(
    tmp_path: Path, workspace: SimpleNamespace
) -> None:
    allow = tmp_path / "allow.py"
    allow.write_text("import sys; sys.stdin.read()", encoding="utf-8")
    _workspace(tmp_path, _toml_rule("guarda", "pre_tool", allow))
    workspace.hooks_rules_digest = None  # aprovação anterior ao digest
    outcomes = await hooks.run_hooks(
        "pre_tool", ctx=_ctx(), tool_name="x", tool_args={}, redact=str
    )
    assert [item.outcome for item in outcomes] == ["skipped_not_approved"]


def test_digest_muda_com_qualquer_campo_da_regra() -> None:
    base = HookRule(id="a", event="pre_tool", command=["x"])
    same = HookRule(id="a", event="pre_tool", command=["x"])
    assert hook_rules_digest([base]) == hook_rules_digest([same])
    for other in (
        HookRule(id="a", event="pre_tool", command=["y"]),
        HookRule(id="a", event="post_tool", command=["x"]),
        HookRule(id="a", event="pre_tool", command=["x"], mode="informative"),
        HookRule(id="a", event="pre_tool", command=["x"], tools=["run_*"]),
        HookRule(id="a", event="pre_tool", command=["x"], timeout_s=1),
    ):
        assert hook_rules_digest([base]) != hook_rules_digest([other])


def _toml_rule(rule_id: str, event: str, script: Path, extra: str = "") -> str:
    return (
        "[[hooks.rules]]\n"
        f'id = "{rule_id}"\n'
        f'event = "{event}"\n'
        f"command = [{PY}, {json.dumps(str(script))}]\n"
        f"{extra}\n"
    )


async def test_run_hooks_filtra_por_evento_e_por_padrao_de_tool(
    tmp_path: Path, workspace: SimpleNamespace
) -> None:
    allow = tmp_path / "allow.py"
    allow.write_text("import sys; sys.stdin.read()", encoding="utf-8")
    _workspace(
        tmp_path,
        _toml_rule("so-comandos", "pre_tool", allow, 'tools = ["run_*"]')
        + _toml_rule("depois", "post_tool", allow),
    )
    ctx = _ctx()
    hit = await hooks.run_hooks(
        "pre_tool", ctx=ctx, tool_name="run_command", tool_args={}, redact=str
    )
    assert [item.hook_id for item in hit] == ["so-comandos"]
    assert (
        await hooks.run_hooks(
            "pre_tool", ctx=ctx, tool_name="read_file", tool_args={}, redact=str
        )
        == []
    )
    post = await hooks.run_hooks(
        "post_tool", ctx=ctx, tool_name="read_file", tool_args={}, result="", redact=str
    )
    assert [item.hook_id for item in post] == ["depois"]


async def test_run_hooks_para_no_primeiro_hook_que_bloqueia(
    tmp_path: Path, workspace: SimpleNamespace
) -> None:
    deny = tmp_path / "deny.py"
    deny.write_text(
        'import json; print(json.dumps({"decision":"deny"}))', encoding="utf-8"
    )
    never = tmp_path / "never.py"
    never.write_text("pass", encoding="utf-8")
    _workspace(
        tmp_path,
        _toml_rule("a", "pre_tool", deny) + _toml_rule("b", "pre_tool", never),
    )
    outcomes = await hooks.run_hooks(
        "pre_tool", ctx=_ctx(), tool_name="x", tool_args={}, redact=str
    )
    assert [item.hook_id for item in outcomes] == ["a"]


async def test_sem_workspace_ou_sem_regras_nada_roda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hooks, "_workspace_for", lambda _ctx: None)
    assert (
        await hooks.run_hooks(
            "pre_tool", ctx=_ctx(), tool_name="x", tool_args={}, redact=str
        )
        == []
    )
    state = SimpleNamespace(cwd=str(tmp_path), hooks_approved=True, id="w1")
    monkeypatch.setattr(hooks, "_workspace_for", lambda _ctx: state)
    assert (
        await hooks.run_hooks(
            "pre_tool", ctx=_ctx(), tool_name="x", tool_args={}, redact=str
        )
        == []
    )


def _registry(handler) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="probe",
            description="probe",
            args_model=create_model("HookProbeArgs"),
            handler=handler,
            extras=ToolExtras(),
            needs_ctx=False,
        )
    )
    return registry


async def test_pre_tool_bloqueante_impede_a_tool_e_registra_evidencia(
    tmp_path: Path, workspace: SimpleNamespace
) -> None:
    deny = tmp_path / "deny.py"
    deny.write_text(
        'import json; print(json.dumps({"decision":"deny","reason":"fora da política"}))',
        encoding="utf-8",
    )
    _workspace(tmp_path, _toml_rule("guarda", "pre_tool", deny))
    executed: list[bool] = []

    async def handler() -> str:
        executed.append(True)
        return "executou"

    ctx = _ctx()
    result = await execute_tool_batch(
        [ToolCall(id="c1", name="probe", args={})],
        tool_registry=_registry(handler),
        ctx=ctx,
    )
    assert executed == []
    assert result[0].is_error is True
    assert "hook_denied: guarda: fora da política" in result[0].text()
    evidence = drain_hook_outcomes(ctx)
    assert evidence[0]["type"] == "hook_run"
    assert evidence[0]["outcome"] == "denied"
    assert evidence[0]["blocked"] is True
    assert "args" not in json.dumps(evidence)
    assert drain_hook_outcomes(ctx) == []


async def test_autorizacao_revogada_durante_o_hook_impede_a_tool(
    tmp_path: Path, workspace: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.engine import tool_batch

    allow = tmp_path / "allow.py"
    allow.write_text("import sys; sys.stdin.read()", encoding="utf-8")
    _workspace(tmp_path, _toml_rule("ok", "pre_tool", allow))
    calls: list[int] = []

    def authorize(*_args: Any, **_kwargs: Any) -> tuple[bool, str]:
        calls.append(1)
        # A primeira checagem passa; a revogação chega enquanto o hook roda.
        return (True, "") if len(calls) == 1 else (False, "policy_revoked")

    monkeypatch.setattr(tool_batch, "_apply_pre_execute", authorize)
    executed: list[bool] = []

    async def handler() -> str:
        executed.append(True)
        return "executou"

    result = await execute_tool_batch(
        [ToolCall(id="c1", name="probe", args={})],
        tool_registry=_registry(handler),
        ctx=_ctx(),
    )
    assert executed == []
    assert "policy_denied: policy_revoked" in result[0].text()
    assert len(calls) == 2


async def test_pre_tool_que_permite_deixa_a_tool_rodar(
    tmp_path: Path, workspace: SimpleNamespace
) -> None:
    allow = tmp_path / "allow.py"
    allow.write_text("import sys; sys.stdin.read()", encoding="utf-8")
    _workspace(tmp_path, _toml_rule("ok", "pre_tool", allow))

    async def handler() -> str:
        return "executou"

    ctx = _ctx()
    result = await execute_tool_batch(
        [ToolCall(id="c1", name="probe", args={})],
        tool_registry=_registry(handler),
        ctx=ctx,
    )
    assert result[0].is_error is False
    assert result[0].text() == "executou"
    assert drain_hook_outcomes(ctx)[0]["outcome"] == "allowed"


async def test_post_tool_bloqueante_retem_o_resultado(
    tmp_path: Path, workspace: SimpleNamespace
) -> None:
    deny = tmp_path / "deny.py"
    deny.write_text(
        'import json,sys; sys.stdin.read(); print(json.dumps({"decision":"deny","reason":"vazou dado"}))',
        encoding="utf-8",
    )
    _workspace(tmp_path, _toml_rule("filtro", "post_tool", deny))

    async def handler() -> str:
        return "conteúdo sensível"

    result = await execute_tool_batch(
        [ToolCall(id="c1", name="probe", args={})],
        tool_registry=_registry(handler),
        ctx=_ctx(),
    )
    assert result[0].is_error is True
    assert "conteúdo sensível" not in result[0].text()
    assert "hook_denied: filtro: vazou dado" in result[0].text()
