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
    # Preserva integralmente blocos explícitos (<guidelines>, ## Contexto, ## Regras, etc.)
    # e linhas com restrições e regras obrigatórias, sem descartar por tamanho de linha,
    # cabeçalho ou presença de nomes de ferramentas em regras (ex: "Nunca use write_terminal sem aprovação").
    extracted_rules: list[str] = []
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    in_guideline_block = False
    in_discardable_doc_block = False

    discardable_doc_headers = (
        "## identity",
        "### tools",
        "### available workbenches",
        "### how vectora works",
        "### tech stack",
        "### supported llm providers",
        "### agent architecture",
        "### general capabilities",
    )

    discardable_header_level: int | None = None

    for line in lines:
        lower = line.lower()

        # Determina se a linha é um cabeçalho Markdown legítimo
        header_level = 0
        if line.startswith("#"):
            stripped_hashes = line.lstrip("#")
            if stripped_hashes.startswith(" "):
                header_level = len(line) - len(stripped_hashes)

        # Se estamos dentro de um bloco descartável e encontramos um cabeçalho Markdown
        # de nível menor ou igual (ex: ### Segurança após ### Tools, ou ## Outra Seção),
        # fechamos o bloco descartável.
        if (
            header_level > 0
            and discardable_header_level is not None
            and header_level <= discardable_header_level
        ):
            in_discardable_doc_block = False
            discardable_header_level = None

        # Identifica cabeçalhos que iniciam blocos de documentação explicitamente descartáveis
        if header_level > 0 and any(
            lower == doc_header
            or lower.startswith((doc_header + " ", doc_header + "\t"))
            for doc_header in discardable_doc_headers
        ):
            in_discardable_doc_block = True
            if discardable_header_level is None:
                discardable_header_level = header_level
            in_guideline_block = False
            continue

        if any(
            tag in lower
            for tag in (
                "<guidelines>",
                "## regras",
                "## diretrizes",
                "## contexto do projeto",
                "### important restrictions",
                "### style",
            )
        ):
            in_guideline_block = True
            in_discardable_doc_block = False
            discardable_header_level = None
            continue

        if any(tag in lower for tag in ("</guidelines>", "## your role")):
            in_guideline_block = False
            continue

        if in_guideline_block:
            if line not in extracted_rules and line not in {
                "<guidelines>",
                "</guidelines>",
            }:
                extracted_rules.append(line)
        elif not in_discardable_doc_block:
            # Fora dos blocos descartáveis de documentação técnica, preserva as regras
            # e diretrizes sem exigir palavras-chave restritivas
            if line not in extracted_rules:
                extracted_rules.append(line)

    contract_parts = [
        "Nome: Vectora. Função: assistente de programação.",
        "Siga diretamente o pedido do usuário e execute as ferramentas necessárias.",
    ]
    if specialization:
        contract_parts.append(specialization)
    if extracted_rules:
        contract_parts.append("Instruções obrigatórias:\n" + "\n".join(extracted_rules))

    return "\n".join(contract_parts)
