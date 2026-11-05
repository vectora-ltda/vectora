"""Otimização periódica de tabelas LanceDB.

LanceDB é um banco de dados baseado em arquivos Lance (formato colunar delta).
Cada inserção/deleção cria novos fragmentos (fragments) no diretório da tabela.
Ao longo do tempo, fragmentos pequenos acumulam e degradam a performance de
leitura. ``optimize_table()`` compacta esses fragmentos e remove versões antigas.

``schedule_optimize()`` agenda otimização periódica em background via
``asyncio.create_task``, sem bloquear o loop principal.

Uso:
    # Otimização única (await)
    await optimize_table(table)

    # Otimização periódica em background (fire-and-forget)
    schedule_optimize(table, interval_s=3600)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Intervalo default entre otimizações periódicas (1 hora)
_DEFAULT_INTERVAL_S = 3600


async def optimize_table(table: Any, *, cleanup_older_than_s: int = 86400) -> bool:
    """Compacta fragmentos e remove versões antigas da ``table``.

    ``table.optimize(cleanup_older_than=...)`` compacta fragmentos pequenos
    E remove snapshots delta antigos numa chamada só — a API unificada da
    lib desde pelo menos 0.36.0. Achado ao estudar a API atual: o código
    fazia isso em duas chamadas, com uma segunda em
    ``table.cleanup_old_versions(older_than_seconds=...)``, método que
    **não existe mais** em `AsyncTable` — o `try/except` ao redor engolia o
    `AttributeError` como um warning genérico a cada chamada, então a
    limpeza de versões antigas nunca rodava de verdade (só a compactação).

    Args:
        table:               Objeto ``lancedb.AsyncTable``.
        cleanup_older_than_s: Versões mais antigas que este valor (segundos) são
                             removidas. Default: 86 400 s (24 h).

    Returns:
        True se a operação foi concluída sem erro; False caso contrário.
    """
    name = getattr(table, "name", "?")

    try:
        stats = await table.optimize(
            cleanup_older_than=timedelta(seconds=cleanup_older_than_s)
        )
        logger.debug(
            "storage/lancedb/optimize: %r — fragmentos %d→%d, %d versões antigas "
            "removidas (%d bytes)",
            name,
            stats.compaction.fragments_removed,
            stats.compaction.fragments_added,
            stats.prune.old_versions_removed,
            stats.prune.bytes_removed,
        )
    except Exception as exc:
        logger.warning("storage/lancedb/optimize: falha ao otimizar %r: %s", name, exc)
        return False

    return True


def schedule_optimize(
    table: Any,
    interval_s: float = _DEFAULT_INTERVAL_S,
    *,
    cleanup_older_than_s: int = 86400,
) -> asyncio.Task[None]:
    """Agenda otimização periódica em background.

    Cria uma ``asyncio.Task`` que roda ``optimize_table()`` a cada
    ``interval_s`` segundos enquanto o event loop estiver ativo.

    A task é "fire-and-forget": falhas individuais são logadas como WARNING
    mas não encerram a task. A task termina silenciosamente quando o event
    loop é encerrado (``CancelledError`` é capturado).

    Args:
        table:               Objeto ``lancedb.AsyncTable``.
        interval_s:          Segundos entre cada otimização. Default 3 600.
        cleanup_older_than_s: Repassado para ``optimize_table()``.

    Returns:
        A ``asyncio.Task`` criada (pode ser cancelada com ``task.cancel()``).
    """
    name = getattr(table, "name", "?")

    async def _loop() -> None:
        logger.debug(
            "storage/lancedb/optimize: task de otimização iniciada para %r "
            "(interval=%ds)",
            name,
            interval_s,
        )
        while True:
            try:
                await asyncio.sleep(interval_s)
                await optimize_table(table, cleanup_older_than_s=cleanup_older_than_s)
            except asyncio.CancelledError:
                logger.debug("storage/lancedb/optimize: task cancelada para %r", name)
                return
            except Exception as exc:
                # Erros inesperados não encerram a task — apenas logamos.
                logger.warning(
                    "storage/lancedb/optimize: erro na task de %r: %s", name, exc
                )

    return asyncio.create_task(_loop(), name=f"lancedb-optimize-{name}")
