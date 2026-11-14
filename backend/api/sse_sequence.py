"""Identidade e ordem dos eventos do stream de chat.

Cada evento do stream de um run recebe ``seq`` (inteiro crescente a partir de
1) e ``event_id`` (``<run_id>:<seq>``) no JSON. O cliente usa o par para
descartar repetição e reordenação e para reconciliar depois de uma queda; o
``done`` final continua sendo o evento terminal. O quadro SSE segue começando
por ``data: ``, como antes: nenhum consumidor existente precisa mudar.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

_DATA_PREFIX = "data: {"


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


async def sequence_stream(stream: AsyncGenerator[str]) -> AsyncGenerator[str]:
    """Numera os eventos de ``stream`` e propaga o encerramento ao gerador interno."""
    seq = 0
    run_id = ""
    try:
        async for chunk in stream:
            if seq == 0:
                run_id = _run_id_of(chunk)
            if not chunk.startswith(_DATA_PREFIX):
                yield chunk
                continue
            seq += 1
            yield stamp_event(chunk, run_id, seq)
    finally:
        await stream.aclose()
