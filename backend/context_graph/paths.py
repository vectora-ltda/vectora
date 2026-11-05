"""Fonte única do diretório de saída do Context Graph.

A saída fica em ``.vectora/context-graph`` por padrão e pode ser sobrescrita pela
env var ``VECTORA_GRAPH_OUT`` (worktrees ou saída compartilhada). Aceita um nome
relativo (``".vectora/context-graph-feature"``) ou um caminho absoluto
(``"/shared/context-graph"``). O valor é lido uma vez no import; defina
``VECTORA_GRAPH_OUT`` antes do processo iniciar e todos os leitores o respeitam.
"""

from __future__ import annotations

import os
from pathlib import Path

GRAPH_OUT = os.environ.get("VECTORA_GRAPH_OUT", ".vectora/context-graph")

# Nome puro do diretório mesmo quando GRAPH_OUT é absoluto. Usado pelos guards de
# caminho que sobem pelos pais procurando o diretório de saída por nome, e pelo
# scan-exclude do detect (a própria saída nunca é re-ingerida como código-fonte).
GRAPH_OUT_NAME = os.path.basename(os.path.normpath(GRAPH_OUT))


def out_path(*parts: str) -> Path:
    """Um caminho dentro do diretório de saída configurado, ex. ``out_path("cache")``.

    ``Path(GRAPH_OUT) / ...`` resolve tanto para um nome relativo
    (``.vectora/context-graph``) quanto para um override absoluto.
    """
    return Path(GRAPH_OUT, *parts)


def default_graph_json() -> str:
    """Caminho padrão de ``graph.json`` dentro do diretório de saída configurado."""
    return str(out_path("graph.json"))
