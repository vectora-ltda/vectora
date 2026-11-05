"""``GET /usage/providers`` — consumo real por provider.

O produto expõe zero consumo hoje: o único medidor é o popover da appbar, que
mostra só a janela de contexto da sessão. Os dados existem e são baratos —
Tavily `GET /usage`, OpenRouter `GET /credits` e `GET /key`.

O Hermes consome exatamente esses dois do OpenRouter em
``agent/account_usage.py:812-881``; pro Tavily ele também não mostra nada.

Invariante: provider que falha aparece **com o erro**, nunca zerado. Um
"0 crédito" falso é pior que um "não consegui consultar".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _sem_credenciais(monkeypatch):
    """Sem credencial e sem cache.

    O cache é module-level: sem limpar, o resultado bom de um teste vaza pro
    seguinte e o caso de erro nunca chega a consultar.
    """
    from backend.api.handlers.usage import clear_usage_cache
    from backend.settings import settings as _s

    clear_usage_cache()
    monkeypatch.setattr(_s, "tavily_api_key", "", raising=False)
    monkeypatch.setattr(_s, "openrouter_api_key", "", raising=False)
    yield
    clear_usage_cache()


class TestAgregador:
    @pytest.mark.asyncio
    async def test_sem_credencial_nenhuma_lista_vem_vazia(self):
        from backend.api.handlers.usage import collect_provider_usage

        assert await collect_provider_usage() == []

    @pytest.mark.asyncio
    async def test_tavily_configurado_aparece_com_o_consumo(self, monkeypatch):
        from backend.api.handlers.usage import collect_provider_usage
        from backend.settings import settings as _s

        monkeypatch.setattr(_s, "tavily_api_key", "tvly-test", raising=False)

        with patch(
            "backend.tools.tavily.client.TavilyClient.usage",
            new=AsyncMock(
                return_value={
                    "key": {"usage": 120, "limit": 1000},
                    "account": {"current_plan": "researcher"},
                }
            ),
        ):
            saida = await collect_provider_usage()

        tavily = next(p for p in saida if p["provider"] == "tavily")
        assert tavily["used"] == 120
        assert tavily["limit"] == 1000
        assert tavily["plan"] == "researcher"
        assert tavily["error"] is None

    @pytest.mark.asyncio
    async def test_openrouter_junta_credits_e_key(self, monkeypatch):
        """São dois endpoints: `/credits` tem o saldo, `/key` tem os limites
        da chave. Mostrar só um deixa o número sem contexto."""
        from backend.api.handlers.usage import collect_provider_usage
        from backend.settings import settings as _s

        monkeypatch.setattr(_s, "openrouter_api_key", "sk-or-test", raising=False)

        async def _get_json(_self, path: str):
            if path.endswith("/credits"):
                return {"data": {"total_credits": 50.0, "total_usage": 12.5}}
            return {"data": {"limit": 100, "limit_remaining": 87.5}}

        with patch(
            "backend.llm.openrouter.client.OpenRouterClient.get_json", new=_get_json
        ):
            saida = await collect_provider_usage()

        openrouter = next(p for p in saida if p["provider"] == "openrouter")
        assert openrouter["used"] == pytest.approx(12.5)
        assert openrouter["limit"] == pytest.approx(50.0)
        assert openrouter["remaining"] == pytest.approx(37.5)

    @pytest.mark.asyncio
    async def test_provider_que_falha_vem_com_erro_e_nao_zerado(self, monkeypatch):
        """Erro/borda crítico: `used: 0` faria o usuário achar que não gastou
        nada, quando na verdade a consulta falhou."""
        from backend.api.handlers.usage import collect_provider_usage
        from backend.settings import settings as _s

        monkeypatch.setattr(_s, "tavily_api_key", "tvly-test", raising=False)

        with patch(
            "backend.tools.tavily.client.TavilyClient.usage",
            new=AsyncMock(side_effect=RuntimeError("rede fora")),
        ):
            saida = await collect_provider_usage()

        tavily = next(p for p in saida if p["provider"] == "tavily")
        assert tavily["error"] == "rede fora"
        assert tavily["used"] is None, "zerou o consumo em vez de sinalizar a falha"

    @pytest.mark.asyncio
    async def test_falha_de_um_nao_derruba_o_outro(self, monkeypatch):
        from backend.api.handlers.usage import collect_provider_usage
        from backend.settings import settings as _s

        monkeypatch.setattr(_s, "tavily_api_key", "tvly-test", raising=False)
        monkeypatch.setattr(_s, "openrouter_api_key", "sk-or-test", raising=False)

        async def _get_json(_self, path: str):
            if path.endswith("/credits"):
                return {"data": {"total_credits": 10.0, "total_usage": 1.0}}
            return {"data": {}}

        with (
            patch(
                "backend.tools.tavily.client.TavilyClient.usage",
                new=AsyncMock(side_effect=RuntimeError("tavily fora")),
            ),
            patch(
                "backend.llm.openrouter.client.OpenRouterClient.get_json", new=_get_json
            ),
        ):
            saida = await collect_provider_usage()

        por_provider = {p["provider"]: p for p in saida}
        assert por_provider["tavily"]["error"] == "tavily fora"
        assert por_provider["openrouter"]["error"] is None
        assert por_provider["openrouter"]["used"] == pytest.approx(1.0)


class TestCache:
    @pytest.mark.asyncio
    async def test_consulta_repetida_usa_o_cache(self, monkeypatch):
        """O popover abre a cada mensagem; bater na API do provider toda vez
        é desperdício e convida rate limit."""
        from backend.api.handlers import usage as mod
        from backend.settings import settings as _s

        mod.clear_usage_cache()
        monkeypatch.setattr(_s, "tavily_api_key", "tvly-test", raising=False)

        chamadas = 0

        async def _usage(_self):
            nonlocal chamadas
            chamadas += 1
            return {"key": {"usage": 1, "limit": 10}}

        with patch("backend.tools.tavily.client.TavilyClient.usage", new=_usage):
            await mod.collect_provider_usage()
            await mod.collect_provider_usage()

        assert chamadas == 1

    @pytest.mark.asyncio
    async def test_cache_nao_guarda_resultado_com_erro(self, monkeypatch):
        """Erro/borda: cachear a falha prenderia o usuário na mensagem de erro
        por minutos, mesmo depois de a rede voltar."""
        from backend.api.handlers import usage as mod
        from backend.settings import settings as _s

        mod.clear_usage_cache()
        monkeypatch.setattr(_s, "tavily_api_key", "tvly-test", raising=False)

        chamadas = 0

        async def _usage(_self):
            nonlocal chamadas
            chamadas += 1
            raise RuntimeError("instável")

        with patch("backend.tools.tavily.client.TavilyClient.usage", new=_usage):
            await mod.collect_provider_usage()
            await mod.collect_provider_usage()

        assert chamadas == 2, "cacheou a falha e não tentou de novo"
