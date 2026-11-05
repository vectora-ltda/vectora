"""Criação de índices IVF_PQ on-demand para tabelas LanceDB.

LanceDB usa busca linear (força-bruta) por padrão. Para coleções com mais de
~10 000 vetores, um índice IVF_PQ (Inverted File + Product Quantization)
reduz a latência de busca de O(n) para O(√n).

Esta função cria o índice somente quando a tabela atinge o tamanho mínimo
(``min_rows``), tornando-a segura para chamar em qualquer ponto sem erro.

Uso:
    db = await get_lancedb()
    table = await db.open_table("articles")
    await create_ivf_index(table)
"""

from __future__ import annotations

import logging
from typing import Any

from lancedb.index import IvfPq

logger = logging.getLogger(__name__)

# Número mínimo de vetores para criar índice IVF_PQ.
# Abaixo disso a busca linear é mais rápida e o índice desperdiçaria memória.
_DEFAULT_MIN_ROWS = 10_000

# Número de partições IVF. Regra de bolso: sqrt(n_rows).
# Para n=10_000 → 100; para n=100_000 → 316.
# Aqui usamos um valor conservador adequado para a faixa 10k-500k.
_DEFAULT_NUM_PARTITIONS = 256

# Subquantizers PQ (16 x 8-bit = 128 bits por vetor comprimido).
_DEFAULT_NUM_SUB_VECTORS = 16


async def create_ivf_index(
    table: Any,
    vector_column: str = "vector",
    *,
    num_partitions: int = _DEFAULT_NUM_PARTITIONS,
    num_sub_vectors: int = _DEFAULT_NUM_SUB_VECTORS,
    min_rows: int = _DEFAULT_MIN_ROWS,
    replace: bool = False,
) -> bool:
    """Cria índice IVF_PQ na ``table`` se ela atingiu ``min_rows`` vetores.

    Idempotente por padrão (``replace=False``): se o índice já existe, não
    faz nada e retorna False. Com ``replace=True``, recria o índice (útil
    após inserção massiva de novos documentos).

    Args:
        table:           Objeto ``lancedb.AsyncTable``.
        vector_column:   Nome da coluna de vetores. Default ``"vector"``.
        num_partitions:  Número de clusters IVF. Default 256.
        num_sub_vectors: Sub-vetores PQ. Default 16.
        min_rows:        Mínimo de linhas para criar o índice. Default 10 000.
        replace:         Se True, recria o índice existente.

    Returns:
        True se o índice foi criado; False se pulado (muito pequeno ou já existe).
    """
    try:
        n = await table.count_rows()
    except Exception as exc:
        logger.warning("storage/lancedb/index: erro ao contar linhas: %s", exc)
        return False

    if n < min_rows:
        logger.debug(
            "storage/lancedb/index: tabela %r tem %d linhas (mín. %d) — índice ignorado",
            getattr(table, "name", "?"),
            n,
            min_rows,
        )
        return False

    try:
        await table.create_index(
            vector_column,
            config=IvfPq(
                num_partitions=num_partitions, num_sub_vectors=num_sub_vectors
            ),
            replace=replace,
        )
        logger.info(
            "storage/lancedb/index: índice IVF_PQ criado em %r "
            "(partitions=%d, sub_vectors=%d, rows=%d)",
            getattr(table, "name", "?"),
            num_partitions,
            num_sub_vectors,
            n,
        )
        return True
    except Exception as exc:
        # Índice já existe e replace=False → não é erro, só avisar em debug
        err_str = str(exc).lower()
        if "already" in err_str or "exists" in err_str:
            logger.debug(
                "storage/lancedb/index: índice já existe em %r (replace=False)",
                getattr(table, "name", "?"),
            )
            return False
        logger.warning(
            "storage/lancedb/index: falha ao criar índice em %r: %s",
            getattr(table, "name", "?"),
            exc,
        )
        return False


async def create_fts_index(
    table: Any,
    text_column: str = "text",
    *,
    replace: bool = False,
    language: str = "English",
) -> bool:
    """Cria índice Full-Text Search nativo do LanceDB na coluna ``text_column``.

    O índice FTS (baseado em Tantivy) permite ``table.search(query,
    query_type="fts")`` para buscas lexicais eficientes, complementar ao
    índice vetorial IVF_PQ. ``language`` controla stemming e remoção de
    stop-words (``FTS.remove_stop_words``/``FTS.stem`` já vêm `True` por
    padrão na lib) — default ``"English"`` preserva o comportamento atual;
    passar ``"Portuguese"`` melhora a qualidade da busca textual em corpus
    em português (stop-words como "de"/"para"/"com" deixam de poluir o
    índice), sem precisar recriar o schema.

    Args:
        table:       Objeto ``lancedb.AsyncTable``.
        text_column: Coluna de texto a indexar. Default ``"text"``.
        replace:     Recria o índice se True.
        language:    Idioma para stemming/stop-words da FTS. Default ``"English"``.

    Returns:
        True se criado; False se pulado ou já existente.
    """
    from lancedb.index import FTS

    try:
        await table.create_index(
            text_column, config=FTS(language=language), replace=replace
        )
        logger.info(
            "storage/lancedb/index: índice FTS criado em %r (coluna=%r, idioma=%r)",
            getattr(table, "name", "?"),
            text_column,
            language,
        )
        return True
    except Exception as exc:
        err_str = str(exc).lower()
        if "already" in err_str or "exists" in err_str:
            logger.debug(
                "storage/lancedb/index: FTS já existe em %r (replace=False)",
                getattr(table, "name", "?"),
            )
            return False
        logger.warning(
            "storage/lancedb/index: falha ao criar FTS em %r: %s",
            getattr(table, "name", "?"),
            exc,
        )
        return False
