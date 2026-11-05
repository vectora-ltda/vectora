"""Tests for backend/cli/keys.py — wizard `vectora config keys`.

Cohere e Tavily são opcionais (o Vectora funciona só com Ollama, sem
nenhuma chave de nuvem — ver backend/browser/search_fallback.py e
backend/storage/factory.py) — o par feliz/erro de cada step confirma que
pular (Enter vazio) não derruba o wizard com `sys.exit`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from rich.console import Console

from backend.cli.keys import _step_cohere_key, _step_tavily_key


class TestStepCohereKey:
    @pytest.mark.asyncio
    async def test_com_key_digitada_retorna_a_key(self):
        with patch("backend.cli.keys.getpass.getpass", return_value="real-cohere-key"):
            result = await _step_cohere_key(Console())
        assert result == "real-cohere-key"

    @pytest.mark.asyncio
    async def test_sem_key_pula_sem_encerrar_o_processo(self):
        # Par de erro/edge case: Enter vazio não deve lançar SystemExit —
        # Cohere é opcional (Ollama cobre embeddings sozinho).
        with patch("backend.cli.keys.getpass.getpass", return_value=""):
            result = await _step_cohere_key(Console())
        assert result == ""


class TestStepTavilyKey:
    @pytest.mark.asyncio
    async def test_com_key_digitada_retorna_a_key(self):
        with patch("backend.cli.keys.getpass.getpass", return_value="real-tavily-key"):
            result = await _step_tavily_key(Console())
        assert result == "real-tavily-key"

    @pytest.mark.asyncio
    async def test_sem_key_pula_sem_encerrar_o_processo(self):
        # Par de erro/edge case: Enter vazio não deve lançar SystemExit —
        # Tavily é opcional (fallback sem chave via search_fallback.py).
        with patch("backend.cli.keys.getpass.getpass", return_value=""):
            result = await _step_tavily_key(Console())
        assert result == ""
