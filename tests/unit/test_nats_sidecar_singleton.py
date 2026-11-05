"""Sidecar NATS — um PID vivo no pid file é um sidecar EM USO, não um órfão.

Regressão de um bug real observado ao vivo: ao subir, o backend lia o pid file
e matava todo PID vivo chamando-o de "órfão de sessão anterior". Com duas
instâncias, a segunda derrubava o `nats-server` da primeira; o cliente da
primeira esgotava os retries e o loop de consumo ficava girando para sempre
("fetch NATS falhou (nats: connection closed) — retry em 2s"), soterrando o
log e escondendo o erro que de fato derrubava o processo.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.scheduling import nats_sidecar as ns


@pytest.fixture(autouse=True)
def _sidecar_limpo(monkeypatch, tmp_path):
    monkeypatch.setattr(ns, "_proc", None)
    monkeypatch.setattr(ns, "_url", None)
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path)


def _registra(store_dir, pid: int, url: str = "") -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {"pids": [pid]}
    if url:
        payload["url"] = url
    (store_dir / ns._PID_FILE_NAME).write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.asyncio
async def test_sidecar_vivo_e_respondendo_e_reusado_nunca_morto(monkeypatch, tmp_path):
    store = tmp_path / "nats"
    _registra(store, 1144, "nats://127.0.0.1:4222")

    monkeypatch.setattr(ns, "_resolve_binary", lambda: "nats-server")
    monkeypatch.setattr(ns, "_pid_is_alive", lambda _p: True)
    monkeypatch.setattr(ns, "_responds", lambda _u, timeout_s=1.0: True)

    mortos: list[int] = []

    def _registra_morte(pid: int) -> None:
        mortos.append(pid)

    monkeypatch.setattr(ns, "_kill_pid", _registra_morte)
    chamou_spawn = {"sim": False}

    async def _nunca_spawna(*_a, **_k):
        chamou_spawn["sim"] = True
        raise OSError("não deveria subir um segundo servidor no mesmo store")

    monkeypatch.setattr(ns.asyncio, "create_subprocess_exec", _nunca_spawna)

    url = await ns.ensure_nats_sidecar()

    # O ponto do teste: o sidecar em uso é REUSADO, não morto — matá-lo
    # derrubaria o JetStream de quem está conectado nele.
    assert url == "nats://127.0.0.1:4222"
    assert mortos == [], f"matou um sidecar vivo e saudável: {mortos}"
    assert chamou_spawn["sim"] is False, "subiu um segundo servidor no mesmo store"


@pytest.mark.asyncio
async def test_pid_vivo_mas_mudo_e_descartado(monkeypatch, tmp_path):
    """Erro/borda: processo no `tasklist` mas sem aceitar conexão (zumbi) — aí
    sim precisa ser descartado, senão o backend ficaria preso a um sidecar
    que nunca responde."""
    store = tmp_path / "nats"
    _registra(store, 999, "nats://127.0.0.1:4222")

    monkeypatch.setattr(ns, "_resolve_binary", lambda: "nats-server")
    monkeypatch.setattr(ns, "_pid_is_alive", lambda _p: True)
    monkeypatch.setattr(ns, "_responds", lambda _u, timeout_s=1.0: False)

    mortos: list[int] = []

    def _registra_morte(pid: int) -> None:
        mortos.append(pid)

    monkeypatch.setattr(ns, "_kill_pid", _registra_morte)

    async def _spawn_falha(*_a, **_k):
        raise OSError("sem binário de verdade neste teste")

    monkeypatch.setattr(ns.asyncio, "create_subprocess_exec", _spawn_falha)

    await ns.ensure_nats_sidecar()

    assert mortos == [999]


@pytest.mark.asyncio
async def test_pid_file_legado_sem_url_nao_reusa_as_cegas(monkeypatch, tmp_path):
    """Erro/borda: pid file de versão antiga não tem `url`. Sem URL não dá pra
    confirmar que o processo é utilizável, então não pode ser reusado às
    cegas — cai no caminho de descarte."""
    store = tmp_path / "nats"
    _registra(store, 777)  # sem url

    monkeypatch.setattr(ns, "_resolve_binary", lambda: "nats-server")
    monkeypatch.setattr(ns, "_pid_is_alive", lambda _p: True)
    monkeypatch.setattr(ns, "_responds", lambda _u, timeout_s=1.0: True)

    mortos: list[int] = []

    def _registra_morte(pid: int) -> None:
        mortos.append(pid)

    monkeypatch.setattr(ns, "_kill_pid", _registra_morte)

    async def _spawn_falha(*_a, **_k):
        raise OSError("sem binário de verdade neste teste")

    monkeypatch.setattr(ns.asyncio, "create_subprocess_exec", _spawn_falha)

    await ns.ensure_nats_sidecar()
    assert mortos == [777]


def test_url_e_persistida_no_pid_file(tmp_path):
    store = tmp_path / "nats"
    store.mkdir(parents=True)
    ns._write_pid_list(store, [42], url="nats://127.0.0.1:5555")

    assert ns._read_stale_pids(store) == [42]
    assert ns._read_registered_url(store) == "nats://127.0.0.1:5555"

    # Erro/borda: arquivo sem `url` (formato antigo) devolve string vazia em
    # vez de estourar.
    ns._write_pid_list(store, [43])
    assert ns._read_registered_url(store) == ""


def test_responds_nega_url_invalida_e_porta_fechada():
    assert ns._responds("") is False
    assert ns._responds("nats://sem-porta") is False
    # Porta certamente fechada no loopback.
    assert ns._responds("nats://127.0.0.1:1", timeout_s=0.2) is False


@pytest.mark.asyncio
async def test_consumo_para_quando_a_conexao_morre_de_vez():
    """O bug do spam: com a conexão encerrada em definitivo, a subscription
    local nunca mais funciona. Sem o corte, o loop repetiria o mesmo warning
    a cada 2s para sempre."""
    from backend.scheduling.mq import NatsMQ

    mq = NatsMQ("nats://127.0.0.1:4222")

    class _SubMorta:
        tentativas = 0

        async def fetch(self, *_a, **_k):
            type(self).tentativas += 1
            raise RuntimeError("nats: connection closed")

    class _JS:
        async def add_stream(self, **_k):
            return None

        async def pull_subscribe(self, *_a, **_k):
            return _SubMorta()

    async def _connect():
        return _JS()

    mq._connect = _connect  # ty: ignore[invalid-assignment]
    mq._failed = True  # `_closed_cb` já rodou

    async def _handler(_msg):
        raise AssertionError("não deveria receber mensagem")

    # Sem o fix isto nunca retornaria; o timeout prova a saída do laço.
    await asyncio.wait_for(
        mq.consume("jobs", group="g", consumer="c", handler=_handler), timeout=5.0
    )
    assert _SubMorta.tentativas == 1, "insistiu numa conexão morta"
