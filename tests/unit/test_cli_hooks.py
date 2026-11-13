"""``vectora hooks list|test``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.cli.hooks import run_hooks
from backend.workspace import workspace as workspace_module
from backend.workspace.workspace_config import hook_rules_digest, load_workspace_config

PY = json.dumps(sys.executable)


def _args(root: Path, action: str, **extra: str) -> SimpleNamespace:
    base = {
        "action": action,
        "workspace": str(root),
        "output": "json",
        "rule_id": "",
        "tool": "run_command",
        "args": "{}",
        "response": "",
        "result": "",
    }
    return SimpleNamespace(**{**base, **extra})


def _run(root: Path, capsys: pytest.CaptureFixture[str], **kwargs: str):
    with pytest.raises(SystemExit) as exit_info:
        run_hooks(_args(root, **kwargs))
    return exit_info.value.code, json.loads(capsys.readouterr().out)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    gate = tmp_path / "gate.py"
    gate.write_text(
        "import json, sys\n"
        "data = json.load(sys.stdin)\n"
        "bad = 'rm' in json.dumps(data['tool']['args'])\n"
        "print(json.dumps({'decision': 'deny' if bad else 'allow', "
        "'reason': 'comando proibido'}))\n",
        encoding="utf-8",
    )
    (tmp_path / "vectora.toml").write_text(
        "[[hooks.rules]]\n"
        'id = "sem-rm"\n'
        'event = "pre_tool"\n'
        'tools = ["run_*"]\n'
        f"command = [{PY}, {json.dumps(str(gate))}]\n",
        encoding="utf-8",
    )
    return tmp_path


def test_list_mostra_as_regras_e_a_aprovacao(
    root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_workspace_config(root)
    assert config is not None
    ws = SimpleNamespace(
        cwd=str(root),
        hooks_approved=True,
        hooks_rules_digest=hook_rules_digest(config.hooks.rules),
    )
    monkeypatch.setattr(workspace_module.workspace_registry, "list_all", lambda: [ws])
    code, out = _run(root, capsys, action="list")
    assert code == 0
    assert out["data"]["approval"] == "approved"
    assert [rule["id"] for rule in out["data"]["rules"]] == ["sem-rm"]

    ws.hooks_rules_digest = "outro"
    _, out = _run(root, capsys, action="list")
    assert out["data"]["approval"] == "pending"


def test_list_sem_workspace_registrado_ou_sem_regras(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    monkeypatch.setattr(workspace_module.workspace_registry, "list_all", list)
    _, out = _run(root, capsys, action="list")
    assert out["data"]["approval"] == "unregistered"
    empty = tmp_path / "vazio"
    empty.mkdir()
    _, out = _run(empty, capsys, action="list")
    assert out["data"]["approval"] == "no_rules"
    assert out["data"]["rules"] == []


def test_test_executa_a_regra_e_mostra_a_decisao(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(
        root, capsys, action="test", rule_id="sem-rm", args='{"command": "ls"}'
    )
    assert code == 0
    assert out["data"]["outcome"] == "allowed"
    assert out["data"]["blocked"] is False

    code, out = _run(
        root, capsys, action="test", rule_id="sem-rm", args='{"command": "rm -rf x"}'
    )
    assert code == 0
    assert out["data"]["outcome"] == "denied"
    assert out["data"]["blocked"] is True
    assert out["data"]["reason"] == "comando proibido"


def test_test_com_regra_inexistente_ou_args_invalidos_falha(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(root, capsys, action="test", rule_id="nao-existe")
    assert code == 1
    assert "regra não encontrada" in out["error"]
    code, out = _run(root, capsys, action="test", rule_id="sem-rm", args="[1]")
    assert code == 1
    assert "objeto JSON" in out["error"]
