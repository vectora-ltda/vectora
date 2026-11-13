"""Identidade e ordem dos eventos do stream de chat.

Cada evento do stream de um run recebe ``seq`` (inteiro crescente a partir de
1) e ``event_id`` (``<run_id>:<seq>``) no JSON. O cliente usa o par para
descartar repetição e reordenação e para reconciliar depois de uma queda; o
``done`` final continua sendo o evento terminal. O quadro SSE segue começando
por ``data: ``, como antes: nenhum consumidor existente precisa mudar.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator

_DATA_PREFIX = "data: {"

#: Teto de eventos guardados por run para retomada e quantos runs ficam na memória.
MAX_LOG_EVENTS = 5000
MAX_LOGS = 64
#: Quanto tempo um run encerrado continua disponível para retomada.
LOG_RETENTION_S = 600.0


class ReplayUnavailableError(Exception):
    """O log do run não existe mais ou não cobre o cursor pedido."""


class RunEventLog:
    """Eventos já numerados de um run, para retomar de um cursor.

    Vive só na memória do processo: uma queda do servidor perde o log e o cliente
    volta a reconciliar com o histórico persistido. Guarda os últimos
    ``MAX_LOG_EVENTS`` eventos.
    """

    def __init__(self, thread_id: str = "") -> None:
        self.thread_id = thread_id
        self.run_id = ""
        self.seq = 0
        self.finished = False
        #: ``True`` enquanto o run continua sem cliente conectado: o encerramento
        #: do stream HTTP não encerra o log, quem termina o run o fecha.
        self.detached = False
        self.finished_at = 0.0
        self._first_seq = 1
        self._lines: list[str] = []
        self._changed = asyncio.Event()

    def add(self, chunk: str) -> str:
        """Numera ``chunk``, guarda e devolve a linha numerada."""
        if self.seq == 0 and not self.run_id:
            self.run_id = _run_id_of(chunk)
            if self.run_id:
                _register(self)
        if not chunk.startswith(_DATA_PREFIX):
            return chunk
        self.seq += 1
        stamped = stamp_event(chunk, self.run_id, self.seq)
        self._lines.append(stamped)
        if len(self._lines) > MAX_LOG_EVENTS:
            del self._lines[0]
            self._first_seq += 1
        self._wake()
        return stamped

    def finish(self) -> None:
        """Marca o run como encerrado e acorda quem espera por novos eventos."""
        if self.finished:
            return
        self.finished = True
        self.finished_at = time.monotonic()
        self._wake()

    def _wake(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    async def replay(self, after_seq: int) -> AsyncGenerator[str]:
        """Entrega os eventos com ``seq`` maior que ``after_seq`` e acompanha o run."""
        if after_seq + 1 < self._first_seq:
            raise ReplayUnavailableError("cursor anterior ao log guardado")
        if after_seq > self.seq:
            raise ReplayUnavailableError("cursor à frente do run")
        cursor = after_seq
        while True:
            waiter = self._changed
            while cursor < self.seq:
                index = cursor + 1 - self._first_seq
                if index < 0:
                    raise ReplayUnavailableError("log truncado durante a retomada")
                yield self._lines[index]
                cursor += 1
            if self.finished:
                return
            await waiter.wait()


_LOGS: dict[str, RunEventLog] = {}


def _register(log: RunEventLog) -> None:
    now = time.monotonic()
    for run_id, old in tuple(_LOGS.items()):
        if old.finished and now - old.finished_at > LOG_RETENTION_S:
            del _LOGS[run_id]
    while len(_LOGS) >= MAX_LOGS:
        del _LOGS[next(iter(_LOGS))]
    _LOGS[log.run_id] = log


def get_run_log(run_id: str) -> RunEventLog | None:
    """Log de eventos de um run, ou ``None`` se não está (mais) na memória."""
    return _LOGS.get(run_id)


def parse_event_id(event_id: str) -> tuple[str, int] | None:
    """Separa ``<run_id>:<seq>``; ``None`` se não for um cursor válido."""
    run_id, _, raw = event_id.rpartition(":")
    if not run_id or not raw.isdigit():
        return None
    return run_id, int(raw)


def stamp_event(chunk: str, run_id: str, seq: int) -> str:
    """Acrescenta ``seq`` e ``event_id`` ao JSON de um evento SSE.

    Linhas que não são ``data: {...}`` (comentários, keep-alive) passam como
    estão.
    """
    if not chunk.startswith(_DATA_PREFIX):
        return chunk
    event_id = f"{run_id}:{seq}"
    return (
        f'data: {{"seq": {seq}, "event_id": {json.dumps(event_id)}, '
        f"{chunk[len(_DATA_PREFIX) :]}"
    )


def _run_id_of(chunk: str) -> str:
    """``run_id`` do primeiro evento (``thread``), ou vazio se não houver."""
    if not chunk.startswith(_DATA_PREFIX):
        return ""
    try:
        payload = json.loads(chunk[len("data: ") :])
    except ValueError:
        return ""
    value = payload.get("run_id") if isinstance(payload, dict) else None
    return value if isinstance(value, str) else ""


async def sequence_stream(
    stream: AsyncGenerator[str], log: RunEventLog | None = None
) -> AsyncGenerator[str]:
    """Numera os eventos de ``stream`` e propaga o encerramento ao gerador interno.

    Com ``log``, cada evento numerado também é guardado para retomada.
    """
    log = log or RunEventLog()
    try:
        async for chunk in stream:
            if log.detached:
                # Sem cliente: o run segue em segundo plano e quem o conduz
                # alimenta o log, inclusive com o evento terminal; o que o gerador
                # ainda emite aqui já não tem destinatário e não é numerado.
                yield chunk
                continue
            yield log.add(chunk)
    finally:
        await stream.aclose()
        if not log.detached:
            log.finish()
