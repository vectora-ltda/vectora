"""Trilha de auditoria do sandbox: encadeada, sem segredos, verificável."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.sandbox import audit
from backend.sandbox.audit import MAX_FILE_BYTES, SandboxAuditLog, redact


@pytest.fixture
def log(tmp_path: Path) -> SandboxAuditLog:
    return SandboxAuditLog(tmp_path / "audit.jsonl")


def test_registros_ficam_encadeados_pelo_hash(log):
    a = log.append("ws", "worker_started", {"network": "filtered"})
    b = log.append("ws", "egress", {"host": "pypi.org", "allowed": True})

    assert a["seq"] == 1 and b["seq"] == 2
    assert b["prev"] == a["hash"]
    assert a["prev"] == "0" * 64


def test_cadeia_integra_verifica(log):
    for i in range(5):
        log.append("ws", "egress", {"host": f"h{i}.com"})

    verdict = log.verify()

    assert verdict.ok is True
    assert verdict.records == 5


def test_arquivo_inexistente_verifica_vazio(tmp_path):
    verdict = SandboxAuditLog(tmp_path / "nada.jsonl").verify()

    assert verdict.ok is True
    assert verdict.records == 0


def test_alterar_um_registro_quebra_a_cadeia(log):
    for i in range(3):
        log.append("ws", "egress", {"host": f"h{i}.com", "allowed": False})
    linhas = log.path.read_text().splitlines()
    registro = json.loads(linhas[1])
    registro["data"]["allowed"] = True
    linhas[1] = json.dumps(registro, sort_keys=True, ensure_ascii=False)
    log.path.write_text("\n".join(linhas) + "\n")

    verdict = log.verify()

    assert verdict.ok is False
    assert verdict.broken_at == 2
    assert verdict.reason == "hash não confere"


def test_remover_um_registro_quebra_a_cadeia(log):
    for i in range(4):
        log.append("ws", "egress", {"host": f"h{i}.com"})
    linhas = log.path.read_text().splitlines()
    del linhas[1]
    log.path.write_text("\n".join(linhas) + "\n")

    verdict = log.verify()

    assert verdict.ok is False
    assert verdict.broken_at == 2


def test_reordenar_registros_quebra_a_cadeia(log):
    for i in range(3):
        log.append("ws", "egress", {"host": f"h{i}.com"})
    linhas = log.path.read_text().splitlines()
    linhas[0], linhas[1] = linhas[1], linhas[0]
    log.path.write_text("\n".join(linhas) + "\n")

    assert log.verify().ok is False


def test_linha_ilegivel_e_apontada(log):
    log.append("ws", "egress", {})
    with log.path.open("a") as arquivo:
        arquivo.write("isto nao e json\n")

    verdict = log.verify()

    assert verdict.ok is False
    assert verdict.broken_at == 2
    assert verdict.reason == "linha ilegível"


def test_a_cadeia_continua_depois_de_reabrir_o_arquivo(tmp_path):
    caminho = tmp_path / "audit.jsonl"
    SandboxAuditLog(caminho).append("ws", "egress", {"n": 1})

    segundo = SandboxAuditLog(caminho).append("ws", "egress", {"n": 2})

    assert segundo["seq"] == 2
    assert SandboxAuditLog(caminho).verify().ok is True


def test_evento_desconhecido_e_recusado(log):
    with pytest.raises(ValueError, match="desconhecido"):
        log.append("ws", "qualquer_coisa", {})


def test_segredos_sao_mascarados_e_textos_longos_cortados():
    saida = redact(
        {
            "api_key": "sk-123",
            "headers": {"Authorization": "Bearer x", "ok": "visível"},
            "lista": [{"token": "t"}],
            "longo": "x" * 2000,
        }
    )

    assert saida["api_key"] == "***"
    assert saida["headers"]["Authorization"] == "***"
    assert saida["headers"]["ok"] == "visível"
    assert saida["lista"][0]["token"] == "***"
    assert len(saida["longo"]) < 600


def test_o_registro_gravado_nao_contem_o_segredo(log):
    log.append("ws", "worker_started", {"env": {"NPM_TOKEN": "npm_segredo"}})

    assert "npm_segredo" not in log.path.read_text()


def test_read_devolve_os_mais_novos_primeiro_e_filtra_por_workspace(log):
    log.append("a", "egress", {"host": "1"})
    log.append("b", "egress", {"host": "2"})
    log.append("a", "egress", {"host": "3"})

    so_a = log.read(workspace_id="a")

    assert [r["data"]["host"] for r in so_a] == ["3", "1"]
    assert len(log.read(limit=2)) == 2


def test_rotacao_arquiva_o_arquivo_e_a_cadeia_segue(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "MAX_FILE_BYTES", 400)
    log = SandboxAuditLog(tmp_path / "audit.jsonl")
    ultimo = None
    for i in range(6):
        ultimo = log.append("ws", "egress", {"host": f"host-{i}.exemplo.com"})

    assert (tmp_path / "audit.jsonl.1").exists()
    assert ultimo is not None and ultimo["seq"] == 6
    assert SandboxAuditLog(tmp_path / "audit.jsonl").verify().ok is True
    assert MAX_FILE_BYTES > 400  # o teto real continua sendo o do módulo


def test_record_nunca_levanta_mesmo_com_falha_de_disco(monkeypatch, tmp_path):
    monkeypatch.setenv(audit.AUDIT_FILE_ENV, str(tmp_path / "pasta" / "a.jsonl"))
    (tmp_path / "pasta").write_text("sou um arquivo, não uma pasta")
    monkeypatch.setattr(audit, "audit_log", SandboxAuditLog())

    audit.record("ws", "egress", {})  # não levanta
