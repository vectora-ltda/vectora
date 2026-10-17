"""Provider-specific system-prompt projections for constrained local models.

The full Vectora SOUL remains canonical in persistence and in providers with
enough context.  Small local models can otherwise spend their whole context
parsing the identity plus tool schemas and start copying old assistant text.
"""

from __future__ import annotations


def compact_small_model_system_prompt(original: str) -> str:
    """Return a short, actionable identity contract for small local models.

    This is deliberately an explicit projection rather than truncating the
    original prompt: truncation can remove the identity, language and safety
    rules while retaining irrelevant workbench documentation.
    """

    source = original.strip()
    specialization = ""
    # Keep only a role label. Copying the long SOUL section makes tiny models
    # loop on repeated identity phrases (the full SOUL remains canonical).
    if "Coder Agent" in source or "filesystem operations" in source:
        specialization = "Perfil: programação, arquivos, terminal, git e memória."
    elif "Search Agent" in source:
        specialization = "Perfil: pesquisa web e leitura de fontes."

    # Avoid repeating an imperative "Você é Vectora" many times: tiny models
    # commonly copy that phrase instead of answering the user. A declarative
    # role card is both clearer and substantially more stable for SmolLM2.
    contract = (
        "Nome: Vectora. Função: assistente de programação. "
        "Responda em português e siga diretamente o pedido do usuário. "
        "Use ferramentas para alterar arquivos e aguarde o resultado."
    )
    if specialization:
        contract += "\n" + specialization
    return contract
