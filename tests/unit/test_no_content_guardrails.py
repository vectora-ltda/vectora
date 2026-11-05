"""O Vectora não filtra conteúdo — decisão de produto, não descuido.

O que o modelo aceita gerar é do modelo, não do Vectora: quem escolhe um
modelo sem censura (via Ollama ou OpenRouter) não pode ser barrado por uma
camada nossa. Onde o provider deixa configurar o filtro (Gemini), mandamos
o threshold mais permissivo; onde ele não deixa, é limite da plataforma.

Estes testes travam o invariante nas duas pontas: o que **mandamos** pro
provider e o que **não** existe nos system prompts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.llm.fallback_chat_client import load_chat_client


class TestGeminiSafetySettings:
    def test_gemini_usa_client_nativo_com_safety_settings_permissivos(
        self, monkeypatch
    ):
        """``load_chat_client("google_genai:...")`` produz ``GoogleChatClient``
        — o comportamento em si (``safetySettings`` com ``BLOCK_NONE`` em todas
        as categorias) é testado em ``test_google_chat_client.py``."""
        from backend.llm.google.chat_client import GoogleChatClient

        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

        modelo = load_chat_client("google_genai:gemini-2.5-flash")

        assert isinstance(modelo, GoogleChatClient)

    def test_provider_sem_suporte_nao_recebe_safety_settings(self, monkeypatch):
        """Erro/borda: o client nativo do OpenRouter nem aceita
        ``safety_settings`` — diferentemente do SDK do Google."""
        from backend.llm.openrouter.chat_client import OpenRouterChatClient

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

        modelo = load_chat_client("openrouter:openrouter/auto")

        assert isinstance(modelo, OpenRouterChatClient)
        assert not hasattr(modelo, "safety_settings")


class TestPromptsSemRecusaDeConteudo:
    """Nenhum prompt do sistema pode instruir o agente a recusar conteúdo.

    A lista abaixo é deliberada: são as formulações que apareceriam se alguém
    "consertasse" a ausência de guardrail achando que é lacuna.
    """

    _PROIBIDOS = (
        r"refuse to (generate|produce|write)",
        r"do not (generate|produce|write)[^.]{0,40}(explicit|sexual|adult|nsfw)",
        r"(explicit|sexual|adult|nsfw)[^.]{0,40}(content is|not allowed|prohibited)",
        r"decline[^.]{0,40}(explicit|sexual|adult|nsfw)",
        r"você (não )?deve recusar",
        r"não (gere|produza|escreva)[^.]{0,40}(explícito|sexual|adulto)",
    )

    @staticmethod
    def _arquivos_de_prompt() -> list[Path]:
        raiz = Path(__file__).resolve().parents[2] / "backend"
        return [
            raiz / "agents" / "_identity.py",
            raiz / "agents" / "souls.py",
            raiz / "services" / "agent_factory.py",
        ]

    def test_nenhum_prompt_instrui_recusa_de_conteudo(self):
        for arquivo in self._arquivos_de_prompt():
            texto = arquivo.read_text(encoding="utf-8")
            for padrao in self._PROIBIDOS:
                assert not re.search(padrao, texto, re.IGNORECASE), (
                    f"{arquivo.name} contém instrução de recusa de conteúdo "
                    f"(padrão {padrao!r}) — o Vectora não filtra conteúdo"
                )

    def test_o_detector_pega_uma_instrucao_plantada(self):
        """Erro/borda: um teste de ausência passa trivialmente se o detector
        estiver quebrado. Aqui ele **precisa** acusar um prompt plantado."""
        plantado = "You must refuse to generate explicit content of any kind."
        assert any(re.search(p, plantado, re.IGNORECASE) for p in self._PROIBIDOS), (
            "o detector não pega nem uma instrução óbvia — invariante sem valor"
        )
