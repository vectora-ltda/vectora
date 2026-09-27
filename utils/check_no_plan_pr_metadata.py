#!/usr/bin/env python3
"""Rejeita TÍTULO/DESCRIÇÃO de PR com proveniência de processo: referência de
planejamento (Sprint/Bloco/Fase) ou menção a ferramenta/revisor de IA
(CodeRabbit, Claude, Copilot, ChatGPT, etc). Mesma regra de
`check_no_plan_comments.py` (comentários de código) — CLAUDE.md §1 cobre
"qualquer artefato que entra no repositório", explicitamente incluindo
"mensagens de PR". Diferente do hook de comentários (que roda local no
pre-commit, arquivos staged como argv), este roda no CI, lendo título e
corpo do evento `pull_request` via variáveis de ambiente.

O corpo de um PR real acumula blocos auto-gerados pelo CodeRabbit (resumo,
"Summary by CodeRabbit") que citam o próprio nome da ferramenta — não são
proveniência de processo escrita por um humano, são o rodapé do bot. Esses
blocos são delimitados por marcadores HTML fixos e removidos antes da
varredura, senão todo PR revisado pelo CodeRabbit falharia sempre.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_no_plan_comments import _PATTERN  # noqa: E402

# Blocos que o próprio CodeRabbit insere no corpo do PR (resumo de review,
# "Summary by CodeRabbit") — sempre delimitados por um comentário HTML de
# abertura "<!-- This is an auto-generated comment ... -->" e um de
# fechamento "<!-- end of auto-generated comment ... -->". Non-greedy +
# DOTALL: o corpo pode ter múltiplos blocos desses, cada um contido entre
# seu próprio par de marcadores.
_AUTO_GENERATED_BLOCK = re.compile(
    r"<!--\s*This is an auto-generated comment.*?-->.*?"
    r"<!--\s*end of auto-generated comment.*?-->",
    re.DOTALL,
)


def _strip_bot_blocks(body: str) -> str:
    return _AUTO_GENERATED_BLOCK.sub("", body)


def main() -> int:
    title = os.environ.get("PR_TITLE", "")
    body = _strip_bot_blocks(os.environ.get("PR_BODY", "") or "")

    violations: list[tuple[str, str]] = []
    if _PATTERN.search(title):
        violations.append(("título", title.strip()))
    for line in body.splitlines():
        if _PATTERN.search(line):
            violations.append(("descrição", line.strip()))

    if not violations:
        return 0

    for field, text in violations:
        print(f"PR {field}: proveniência de processo encontrada: {text}")
    print(
        "\nTítulo/descrição do PR não podem referenciar Sprint/Bloco/Fase nem "
        "ferramenta/revisor de IA (CodeRabbit, Claude, Copilot, etc) — mesma "
        "regra dos comentários de código (CLAUDE.md §1, que cobre também "
        "mensagens de PR). Descreva só o que a mudança faz; histórico de "
        "planejamento deve ficar em .claude/plans/; docs/ é reservado ao site de documentação."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
