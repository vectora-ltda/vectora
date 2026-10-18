"""Provider-specific system-prompt projections for constrained local models.

The full Vectora SOUL remains canonical in persistence and in providers with
enough context.  Small local models can otherwise spend their whole context
parsing the identity plus tool schemas and start copying old assistant text.
"""

from __future__ import annotations


def compact_small_model_system_prompt(original: str) -> str:
    """Return an actionable identity contract for small local models.

    Preserves mandatory rules, language constraints, and custom workspace
    instructions from the original prompt while removing repetitive preambles
    and verbose workbench documentation that exhaust tiny contexts.
    """
    source = original.strip()
    if not source:
        return (
            "Nome: Vectora. Função: assistente de programação. "
            "Responda seguindo diretamente o pedido do usuário. "
            "Use ferramentas para alterar arquivos e aguarde o resultado."
        )

    # 1. Identificar especialização declarativa
    specialization = ""
    if "Coder Agent" in source or "filesystem operations" in source:
        specialization = "Perfil: programação, arquivos, terminal, git e memória."
    elif "Search Agent" in source:
        specialization = "Perfil: pesquisa web e leitura de fontes."

    # 2. Extrair seções essenciais de regras / diretrizes customizadas
    # Se houver instruções explícitas (ex: <guidelines>, regras, idioma), preservá-las.
    extracted_rules: list[str] = []
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    for line in lines:
        lower = line.lower()
        if any(
            marker in lower
            for marker in (
                "idioma:",
                "language:",
                "responda em",
                "sempre responda",
                "sempre use",
                "não use",
                "do not",
                "never",
                "regra:",
                "regras:",
                "rule:",
                "rules:",
                "workspace:",
                "projeto:",
            )
        ):
            if line not in extracted_rules and len(line) <= 250:
                extracted_rules.append(line)

    contract_parts = [
        "Nome: Vectora. Função: assistente de programação.",
        "Siga diretamente o pedido do usuário e execute as ferramentas necessárias.",
    ]
    if specialization:
        contract_parts.append(specialization)
    if extracted_rules:
        contract_parts.append(
            "Instruções obrigatórias:\n" + "\n".join(extracted_rules[:8])
        )

    return "\n".join(contract_parts)
