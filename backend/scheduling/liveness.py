"""Liveness semântica de runs de subagente/task em segundo plano.

Diferente do TTL+heartbeat de `kanban.py` (resolve "o worker morreu"), este
classificador resolve "o worker está vivo mas travado sem progredir de
verdade" — um sinal leve (regex sobre o texto final da run, não um
LLM-judge caro) que sinaliza padrões recorrentes de estagnação.

Puramente informativo: `classify_liveness` só rotula, nunca pausa/bloqueia
task nenhuma sozinho — quem decide agir sobre o rótulo (ex.: acionar HITL
mais cedo) é código de mais alto nível, se e quando o produto pedir.
"""

from __future__ import annotations

import re
from typing import Literal

LivenessSignal = Literal["blocked_external", "manager_review", "planning_only"]

#: Ordem importa: o primeiro padrão que casar decide o rótulo — mensagens
#: que casariam mais de um padrão (raro, mas possível) não têm ambiguidade.
_PATTERNS: tuple[tuple[LivenessSignal, re.Pattern[str]], ...] = (
    (
        "blocked_external",
        re.compile(
            r"\b(aguardando|esperando|waiting for|pending)\b[^.]{0,60}"
            r"\b(resposta|approval|credential|credencial|acesso|access|human|humano)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "manager_review",
        re.compile(
            r"\b(precisa(?:r)?\s+de\s+revis[ãa]o|needs?\s+(?:human\s+)?review"
            r"|requer\s+aprova[çc][ãa]o|requires?\s+approval)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "planning_only",
        re.compile(
            r"^\s*(?:apenas\s+|só\s+|only\s+)?"
            r"(?:criei|gerei|elaborei|created|generated|drafted)\s+"
            r"(?:um\s+|uma\s+|a\s+|the\s+)?plano?\b",
            re.IGNORECASE,
        ),
    ),
)


def classify_liveness(text: str | None) -> LivenessSignal | None:
    """Classifica o texto final de uma run — `None` quando nenhum padrão
    conhecido casa (o caso comum: a run progrediu normalmente)."""
    if not text:
        return None
    for signal, pattern in _PATTERNS:
        if pattern.search(text):
            return signal
    return None
