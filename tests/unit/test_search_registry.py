"""Roteador de backends de busca.

Resolve o backend de busca (`tavily`, `ollama-web`, `browser`,
`duckduckgo`) por disponibilidade de credencial, com ordem de preferência
por padrão e escolha explícita via `rag_settings.search_backend`.
"""

from __future__ import annotations

import pytest

from backend.tools.search_registry import (
    SearchBackendUnavailableError,
    available_backends,
    resolve_backend,
)


def _escolhe(monkeypatch, valor: str) -> None:
    """`rag_settings` é property — o patch vai na classe, pra exercitar o
    mesmo caminho de leitura que roda em produção."""
    from backend.workspace.runtime_settings import runtime_settings

    monkeypatch.setattr(
        type(runtime_settings),
        "rag_settings",
        property(lambda _self: {"search_backend": valor} if valor else {}),
    )


@pytest.fixture(autouse=True)
def _sem_credenciais(monkeypatch):
    """Nenhum backend com credencial, salvo o que cada teste ligar."""
    from backend.settings import settings as _s

    monkeypatch.setattr(_s, "tavily_api_key", "", raising=False)
    monkeypatch.setattr(_s, "ollama_api_key", "", raising=False)
    _escolhe(monkeypatch, "")


def _liga_tavily(monkeypatch):
    from backend.settings import settings as _s

    monkeypatch.setattr(_s, "tavily_api_key", "tvly-test", raising=False)


def _liga_ollama_cloud(monkeypatch):
    from backend.settings import settings as _s

    monkeypatch.setattr(_s, "ollama_api_key", "oll-test", raising=False)


class TestDisponibilidade:
    def test_backends_sem_credencial_ficam_de_fora(self):
        """`browser` e `duckduckgo` não precisam de chave — sempre estão lá.
        `tavily` e `ollama-web` precisam."""
        nomes = {b.name for b in available_backends()}

        assert "browser" in nomes
        assert "duckduckgo" in nomes
        assert "tavily" not in nomes
        assert "ollama-web" not in nomes

    def test_credencial_configurada_habilita_o_backend(self, monkeypatch):
        _liga_tavily(monkeypatch)
        _liga_ollama_cloud(monkeypatch)

        nomes = {b.name for b in available_backends()}

        assert "tavily" in nomes
        assert "ollama-web" in nomes


class TestResolucaoAutomatica:
    def test_com_tavily_configurado_ele_e_o_preferido(self, monkeypatch):
        _liga_tavily(monkeypatch)
        assert resolve_backend().name == "tavily"

    def test_sem_nenhuma_chave_cai_no_duckduckgo(self):
        """Comportamento atual preservado: sem chave, a busca continua
        funcionando pela API JSON sem credencial."""
        assert resolve_backend().name == "duckduckgo"

    def test_ollama_web_entra_antes_do_duckduckgo(self, monkeypatch):
        """Erro/borda de ordenação: com key do Ollama Cloud e sem Tavily, a
        busca com credencial ganha da sem credencial."""
        _liga_ollama_cloud(monkeypatch)
        assert resolve_backend().name == "ollama-web"


class TestEscolhaExplicita:
    def test_escolha_do_usuario_vence_a_ordem_de_preferencia(self, monkeypatch):
        """Backend escolhido explicitamente vence a ordem de preferência
        automática, mesmo com Tavily configurado."""
        _liga_tavily(monkeypatch)
        _escolhe(monkeypatch, "browser")

        assert resolve_backend().name == "browser"

    def test_escolha_de_backend_indisponivel_levanta_erro_claro(self, monkeypatch):
        """Erro/borda: escolher `tavily` sem chave não pode cair em outro
        backend em silêncio — o usuário pediu aquele, e precisa saber que a
        credencial falta."""
        _escolhe(monkeypatch, "tavily")

        with pytest.raises(SearchBackendUnavailableError, match="tavily"):
            resolve_backend()

    def test_escolha_de_backend_inexistente_tambem_levanta(self, monkeypatch):
        """Erro/borda: nome digitado errado na config não pode virar
        'usa o default' silencioso."""
        _escolhe(monkeypatch, "gogle")

        with pytest.raises(SearchBackendUnavailableError, match="gogle"):
            resolve_backend()

    def test_auto_volta_pra_ordem_de_preferencia(self, monkeypatch):
        _liga_tavily(monkeypatch)
        _escolhe(monkeypatch, "auto")

        assert resolve_backend().name == "tavily"


class TestExecucao:
    @pytest.mark.asyncio
    async def test_backend_do_duckduckgo_devolve_resultados(self, monkeypatch):
        def _fake(query, max_results=5):
            return [{"title": "r", "url": "https://x.test", "content": "c"}]

        monkeypatch.setattr("backend.browser.search_fallback.search_fallback", _fake)

        backend = resolve_backend()
        resultados = await backend.search("q")

        assert resultados[0]["url"] == "https://x.test"

    @pytest.mark.asyncio
    async def test_falha_do_backend_escolhido_nao_vira_lista_vazia(self, monkeypatch):
        """Erro/borda crítico: lista vazia faz o LLM concluir que não há
        resultados, que é diferente de a busca ter falhado."""

        def _explode(query, max_results=5):
            raise RuntimeError("rede fora")

        monkeypatch.setattr("backend.browser.search_fallback.search_fallback", _explode)

        backend = resolve_backend()
        with pytest.raises(RuntimeError, match="rede fora"):
            await backend.search("q")
