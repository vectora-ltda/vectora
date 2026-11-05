"""sequential_thinking tool.

Segue a spec MCP Anthropic: permite raciocinar passo a passo, revisitar
pensamentos anteriores e chegar a uma conclusão antes de agir.

Tool nativa (`@vtool`) — chamada como função async direta.
"""

from __future__ import annotations

import json

import pytest

from backend.tools.thinking import sequential_thinking


@pytest.mark.asyncio
async def test_sequential_thinking_returns_json():
    """Deve retornar JSON válido."""
    result = await sequential_thinking(
        thought="Preciso analisar o código",
        thought_number=1,
        total_thoughts=3,
    )
    data = json.loads(result)
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_sequential_thinking_includes_thought():
    """O JSON de retorno deve conter o thought e o número."""
    result = await sequential_thinking(
        thought="Analise inicial", thought_number=1, total_thoughts=2
    )
    data = json.loads(result)
    assert data["thought"] == "Analise inicial"
    assert data["thought_number"] == 1


@pytest.mark.asyncio
async def test_sequential_thinking_final_thought_flag():
    """Último pensamento: is_final=True quando thought_number == total_thoughts."""
    result = await sequential_thinking(
        thought="Conclusão", thought_number=3, total_thoughts=3
    )
    data = json.loads(result)
    assert data["is_final"] is True


@pytest.mark.asyncio
async def test_sequential_thinking_non_final_thought():
    """Pensamento intermediário: is_final=False."""
    result = await sequential_thinking(
        thought="Pensando...", thought_number=1, total_thoughts=5
    )
    data = json.loads(result)
    assert data["is_final"] is False


@pytest.mark.asyncio
async def test_sequential_thinking_with_revision():
    """Revisão de pensamento anterior deve ser registrada."""
    result = await sequential_thinking(
        thought="Revisei e corrigi meu entendimento",
        thought_number=2,
        total_thoughts=3,
        is_revision=True,
        revises_thought=1,
    )
    data = json.loads(result)
    assert data.get("is_revision") is True
    assert data.get("revises_thought") == 1


@pytest.mark.asyncio
async def test_sequential_thinking_branching():
    """Branch de raciocínio deve registrar a bifurcação."""
    result = await sequential_thinking(
        thought="Explorar alternativa B",
        thought_number=3,
        total_thoughts=5,
        branch_from_thought=2,
        branch_id="alt-B",
    )
    data = json.loads(result)
    assert data.get("branch_id") == "alt-B"


@pytest.mark.asyncio
async def test_sequential_thinking_invalid_numbers_error():
    """thought_number > total_thoughts deve retornar erro."""
    result = await sequential_thinking(
        thought="Erro", thought_number=5, total_thoughts=3
    )
    data = json.loads(result)
    assert data.get("status") == "error" or "error" in data
