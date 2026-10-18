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
    # Preserva blocos de guidelines (<guidelines>, ## Contexto, ## Instruções, Regras)
    # ou qualquer linha contendo restrições de comportamento / idioma / aprovação.
    extracted_rules: list[str] = []
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    in_guideline_block = False

    for line in lines:
        lower = line.lower()
        if any(
            tag in lower
            for tag in (
                "<guidelines>",
                "## regras",
                "## diretrizes",
                "## contexto do projeto",
            )
        ):
            in_guideline_block = True
            continue
        if any(tag in lower for tag in ("</guidelines>", "### tools", "## your role")):
            in_guideline_block = False
            continue

        if in_guideline_block:
            if (
                line not in extracted_rules
                and len(line) <= 300
                and not line.startswith("#")
            ):
                extracted_rules.append(line)
        else:
            if any(
                marker in lower
                for marker in (
                    "idioma",
                    "language",
                    "responda",
                    "sempre",
                    "nunca",
                    "não",
                    "not",
                    "never",
                    "regra",
                    "rule",
                    "workspace",
                    "projeto",
                    "solicite",
                    "aprov",
                    "arquivo",
                    "antes de",
                    "proibido",
                )
            ):
                # Ignora linhas da documentação padrão de ferramentas do Workbench
                if not any(
                    doc_marker in lower
                    for doc_marker in (
                        "workbench",
                        "electron",
                        "browser_",
                        "open_terminal",
                        "list_terminals",
                        "attach_terminal",
                        "read_terminal",
                        "write_terminal",
                        "ingest_docs",
                        "vector_search",
                        "manage_retriever",
                        "fire-and-forget",
                    )
                ):
                    if line not in extracted_rules and len(line) <= 300:
                        extracted_rules.append(line)

    contract_parts = [
        "Nome: Vectora. Função: assistente de programação.",
        "Siga diretamente o pedido do usuário e execute as ferramentas necessárias.",
    ]
    if specialization:
        contract_parts.append(specialization)
    if extracted_rules:
        contract_parts.append(
            "Instruções obrigatórias:\n" + "\n".join(extracted_rules[:15])
        )

    return "\n".join(contract_parts)
