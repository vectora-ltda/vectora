"""Reprodução isolada e hermética de um suposto hang de `lancedb` no Windows.

NÃO é um teste pytest — de propósito. Escrito para investigar um hang
observado em `GetQueuedCompletionStatus`/IOCP com `lancedb==0.37.1`, que na
época parecia desaparecer voltando pra `0.36.0`. Diagnóstico upstream
posterior (lancedb/lancedb#3983, #4001) mostrou que a causa real era um
vazamento de threads no NOSSO próprio código (conexões SQLite nunca
fechadas entre testes) — não um bug do LanceDB. `pyproject.toml` já não
tem mais teto de versão por causa disso. Mantido como ferramenta de
diagnóstico caso um hang parecido reapareça (abre/fecha N conexões
`connect_async` em sequência, num processo Python nu, fora de qualquer
fixture pytest).

Uso:
    uv run python utils/repro_lancedb_windows_hang.py [--n N] [--timeout-s S]

Se travar, o watchdog (thread separada com timeout) despeja a stack de
todas as threads (`faulthandler.dump_traceback`) antes de sair com código
1, já que o processo trancado não retornaria ao shell sozinho.
"""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path


def _watchdog(timeout_s: float, done: threading.Event) -> None:
    """Se o processo principal não terminar em `timeout_s`, despeja a stack
    de TODAS as threads (não só a principal) e mata o processo — é
    exatamente essa informação (qual thread está presa e onde) que falta
    pra abrir um issue upstream acionável."""
    if done.wait(timeout=timeout_s):
        return
    print(
        f"\n!!! HANG detectado — sem terminar após {timeout_s}s. "
        "Stack de todas as threads:\n",
        file=sys.stderr,
    )
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    sys.stderr.flush()
    # Watchdog não consegue interromper um thread nativo (Rust) preso em
    # syscall — a única saída limpa é matar o processo pelo SO.
    import os

    os._exit(1)


async def _open_close_once(db_dir: Path, index: int) -> None:
    import lancedb
    import pyarrow as pa

    db = await lancedb.connect_async(str(db_dir))
    schema = pa.schema(
        [pa.field("id", pa.string()), pa.field("vector", pa.list_(pa.float32(), 4))]
    )
    table_name = f"t{index}"
    table = await db.create_table(table_name, schema=schema)
    await table.add([{"id": "1", "vector": [0.1, 0.2, 0.3, 0.4]}])
    await table.count_rows()
    await db.drop_table(table_name)


async def _run(n: int, db_dir: Path) -> None:
    for i in range(n):
        await _open_close_once(db_dir, i)
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{n} conexões abertas/fechadas sem travar...")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--n",
        type=int,
        default=200,
        help="Número de conexões connect_async em sequência (default: 200).",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=120.0,
        help="Timeout do watchdog em segundos (default: 120).",
    )
    args = parser.parse_args()

    import lancedb

    print(f"lancedb {lancedb.__version__} — reproduzindo com N={args.n}")

    done = threading.Event()
    watchdog = threading.Thread(
        target=_watchdog, args=(args.timeout_s, done), daemon=True
    )
    watchdog.start()

    tmp_dir = Path(tempfile.mkdtemp(prefix="lancedb_hang_repro_"))
    start = time.monotonic()
    try:
        asyncio.run(_run(args.n, tmp_dir))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    elapsed = time.monotonic() - start
    done.set()
    print(f"OK — {args.n} conexões concluídas sem travar em {elapsed:.1f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
