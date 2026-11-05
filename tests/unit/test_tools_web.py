"""Tests for backend/tools/web.py — Tavily + fallback via Chromium."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tools.web import _fetch_via_http, fetch_url, web_search


@pytest.mark.asyncio
class TestWebSearchFallback:
    async def test_sem_tavily_api_key_usa_fallback_via_chromium(self, monkeypatch):
        """Sem chave nenhuma o roteador elege o DuckDuckGo. O patch vai em
        `backend.settings.settings`, a fonte que o roteador lê: mockar só o
        alias do módulo da tool deixaria o roteador vendo a chave real do
        ambiente."""
        from backend.settings import settings as _s

        monkeypatch.setattr(_s, "tavily_api_key", "", raising=False)
        monkeypatch.setattr(_s, "ollama_api_key", "", raising=False)

        with patch(
            "backend.browser.search_fallback.search_fallback",
            return_value=[{"title": "t", "content": "c", "url": "u"}],
        ) as mock_fallback:
            result = await web_search(query="python")

        mock_fallback.assert_called_once_with("python", max_results=5)
        data = json.loads(result)
        assert data == [{"title": "t", "content": "c", "url": "u"}]

    async def test_sem_tavily_e_fallback_tambem_falha_retorna_erro_textual(
        self, monkeypatch
    ):
        # Par de erro: nem Tavily nem o fallback disponíveis — a tool nunca
        # propaga a exceção, sempre devolve JSON de erro pro agente.
        from backend.settings import settings as _s

        monkeypatch.setattr(_s, "tavily_api_key", "", raising=False)
        monkeypatch.setattr(_s, "ollama_api_key", "", raising=False)
        with (
            patch("backend.tools.web.settings") as ms,
            patch(
                "backend.browser.search_fallback.search_fallback",
                side_effect=RuntimeError("chromium ausente"),
            ),
        ):
            ms.tavily_api_key = None
            result = await web_search(query="python")

        data = json.loads(result)
        assert data["status"] == "error"

    async def test_tavily_configurado_nao_usa_fallback(self, monkeypatch):
        """Com key válida a busca vai pelo cliente nativo, e o contrato de
        saída (`json.dumps(results)`) continua o mesmo — é o contrato com o
        LLM, não muda com a troca de backend."""
        from backend.settings import settings

        monkeypatch.setattr(settings, "tavily_api_key", "real-key")
        monkeypatch.setattr(settings, "ollama_api_key", "")
        mock_client = MagicMock()
        mock_client.search = AsyncMock(return_value=[{"title": "tavily result"}])
        with (
            patch(
                "backend.tools.web._get_search_tool",
                return_value=type("TavilyBackend", (), {"name": "tavily"})(),
            ),
            patch("backend.tools.web._tavily_client", return_value=mock_client),
            patch("backend.browser.search_fallback.search_fallback") as mock_fallback,
        ):
            result = await web_search(query="python")

        mock_fallback.assert_not_called()
        data = json.loads(result)
        assert data == [{"title": "tavily result"}]


@pytest.mark.asyncio
class TestFetchUrlFallback:
    async def test_sem_tavily_api_key_usa_fallback_via_chromium(self):
        with (
            patch("backend.tools.web.settings") as ms,
            patch(
                "backend.tools.web._check_egress_url", new=AsyncMock(return_value=None)
            ),
            patch("backend.browser.ssrf_guard.is_url_ssrf_safe", return_value=True),
            patch(
                "backend.browser.search_fallback.fetch_fallback",
                return_value="conteúdo extraído",
            ) as mock_fallback,
        ):
            ms.tavily_api_key = None
            result = await fetch_url(url="https://example.com")

        mock_fallback.assert_called_once_with("https://example.com")
        # Conteúdo de fetch_url chega envelopado como não-confiável.
        assert "conteúdo extraído" in result
        assert result.startswith('<untrusted_content source="https://example.com">')

    @pytest.mark.parametrize(
        "provider_error",
        [
            "429 Too Many Requests",
            "quota exceeded",
            "rate limit exceeded",
            "invalid credentials",
        ],
    )
    async def test_configured_provider_quota_uses_chromium_only_on_quota(
        self, provider_error: str
    ) -> None:
        backend = MagicMock()
        backend.invoke = AsyncMock(side_effect=RuntimeError(provider_error))
        with (
            patch("backend.tools.web.settings") as settings,
            patch(
                "backend.tools.web._check_egress_url", new=AsyncMock(return_value=None)
            ) as preflight,
            patch("backend.browser.ssrf_guard.is_url_ssrf_safe", return_value=True),
            patch("backend.tools.web._get_extract_tool", return_value=backend),
            patch(
                "backend.browser.search_fallback.fetch_fallback",
                return_value="fallback content",
            ) as fallback,
        ):
            settings.tavily_api_key = "configured-key"
            result = await fetch_url(url="https://example.com")
        preflight.assert_awaited_once_with("https://example.com", None)
        backend.invoke.assert_awaited_once_with({"url": "https://example.com"})
        if provider_error == "invalid credentials":
            fallback.assert_not_called()
            assert result.startswith("Error")
        else:
            fallback.assert_called_once_with("https://example.com")
            assert '<untrusted_content source="https://example.com">' in result
            assert "fallback content" in result

    async def test_localhost_allowlist_does_not_bypass_fetch_ssrf_guard(self) -> None:
        from backend.services.egress_policy import EgressPolicy

        policy = EgressPolicy(allow_localhost_dev_ports=[3000])
        with (
            patch("backend.tools.web._egress_policy", return_value=policy),
            patch(
                "backend.services.egress_policy.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("127.0.0.1", 3000))],
            ),
            patch("backend.tools.web._get_extract_tool") as extract,
            patch("backend.tools.web._fetch_via_fallback", new=AsyncMock()) as fallback,
        ):
            from backend.tools.web import _check_egress_url

            assert await _check_egress_url("http://localhost:3000", None) is None
            result = await fetch_url(url="http://localhost:3000")
        assert "SSRF" in result
        extract.assert_not_called()
        fallback.assert_not_awaited()

    async def test_sem_tavily_e_fallback_tambem_falha_retorna_erro_textual(self):
        with (
            patch("backend.tools.web.settings") as ms,
            patch(
                "backend.tools.web._check_egress_url", new=AsyncMock(return_value=None)
            ),
            patch("backend.browser.ssrf_guard.is_url_ssrf_safe", return_value=True),
            patch(
                "backend.browser.search_fallback.fetch_fallback",
                side_effect=RuntimeError("chromium ausente"),
            ),
        ):
            ms.tavily_api_key = None
            result = await fetch_url(url="https://example.com")

        assert "Error" in result


@pytest.mark.asyncio
class TestFetchUrlSsrfGuard:
    """fetch_url recusa URLs que resolvem pra IP privado/loopback/link-local/
    metadata, antes de tentar Tavily ou o fallback Chromium."""

    async def test_refuses_metadata_url_without_calling_tavily_or_fallback(self):
        with (
            patch("backend.tools.web.settings") as ms,
            patch("backend.browser.ssrf_guard.is_url_ssrf_safe", return_value=False),
            patch("backend.tools.web._get_extract_tool") as mock_extract,
            patch("backend.browser.search_fallback.fetch_fallback") as mock_fallback,
        ):
            ms.tavily_api_key = "real-key"
            result = await fetch_url(url="http://169.254.169.254/latest/")

        assert "Error" in result
        mock_extract.assert_not_called()
        mock_fallback.assert_not_called()

    async def test_allows_public_url_with_tavily_configured(self):
        mock_client = MagicMock()
        with (
            patch("backend.tools.web.settings") as ms,
            patch(
                "backend.tools.web._check_egress_url", new=AsyncMock(return_value=None)
            ),
            patch("backend.browser.ssrf_guard.is_url_ssrf_safe", return_value=True),
            patch("backend.tools.web._get_extract_tool", return_value=mock_client),
            patch(
                "backend.tools.web._invoke_backend",
                new=AsyncMock(return_value=[{"content": "ok", "raw_content": ""}]),
            ),
        ):
            ms.tavily_api_key = "real-key"
            result = await fetch_url(url="https://example.com")

        # Conteúdo de fetch_url chega envelopado como não-confiável.
        assert "ok" in result
        assert result.startswith('<untrusted_content source="https://example.com">')

    async def test_backend_sem_extract_usa_http_direto(self) -> None:
        """Um SearchBackend só com `.search` não pode parecer repo privado."""
        backend = MagicMock(name="tavily")
        with (
            patch("backend.tools.web.settings") as ms,
            patch("backend.browser.ssrf_guard.is_url_ssrf_safe", return_value=True),
            patch("backend.tools.web._get_extract_tool", return_value=backend),
            patch(
                "backend.tools.web._invoke_backend",
                new=AsyncMock(side_effect=AttributeError("sem extract")),
            ),
            patch(
                "backend.tools.web._fetch_via_http",
                new=AsyncMock(
                    return_value="<untrusted_content>github</untrusted_content>"
                ),
            ) as direct,
        ):
            ms.tavily_api_key = "real-key"
            result = await fetch_url(
                url="https://github.com/vectora-ltda/vectora/issues/317"
            )

        direct.assert_awaited_once()
        assert "github" in result

    async def test_fetch_recusa_egress_antes_de_qualquer_backend(self) -> None:
        client = MagicMock()
        with (
            patch("backend.tools.web.settings") as ms,
            patch(
                "backend.tools.web._check_egress_url",
                new=AsyncMock(return_value="destino privado"),
            ),
            patch("backend.tools.web._get_extract_tool", return_value=client) as get,
            patch("backend.tools.web._fetch_via_fallback", new=AsyncMock()) as fallback,
        ):
            ms.tavily_api_key = "real-key"
            result = await fetch_url(url="https://internal.example")

        assert "política de egress" in result
        get.assert_not_called()
        fallback.assert_not_awaited()

    async def test_http_direto_conecta_no_ip_validado_em_cada_redirect(self) -> None:
        """Regressão: o cliente não pode resolver novamente após o preflight."""

        class _Response:
            status_code = 200
            headers: dict[str, str] = {}
            encoding = "utf-8"

            def raise_for_status(self) -> None:
                return None

            async def aiter_bytes(self):
                yield b"conteudo"

        class _Stream:
            async def __aenter__(self) -> _Response:
                return _Response()

            async def __aexit__(self, *args: object) -> None:
                return None

        transports: list[object] = []

        class _Client:
            def __init__(self, *, transport: object, **_: object) -> None:
                transports.append(transport)

            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            def stream(self, *_args: object, **_kwargs: object) -> _Stream:
                return _Stream()

        with (
            patch("backend.tools.web.httpx.AsyncClient", _Client),
            patch(
                "backend.tools.native.http_request.PinIPTransport",
                side_effect=lambda ip: ("pinned", ip),
            ),
            patch(
                "backend.tools.web.async_is_url_allowed",
                new=AsyncMock(return_value=(True, "", "93.184.216.34")),
            ) as allowed,
        ):
            result = await _fetch_via_http("https://example.com")

        assert "conteudo" in result
        assert transports == [("pinned", "93.184.216.34")]
        allowed.assert_awaited_once()


@pytest.mark.asyncio
class TestCrawlEMapExigemKey:
    """`web_crawl`/`web_map` não têm fallback: varredura de site não dá pra
    fazer com a API JSON do DuckDuckGo nem com uma sessão de browser."""

    async def test_crawl_sem_key_devolve_erro_claro(self):
        from backend.tools.web import web_crawl

        with (
            patch("backend.tools.web.settings") as ms,
            patch(
                "backend.tools.web._check_egress_url", new=AsyncMock(return_value=None)
            ),
        ):
            ms.tavily_api_key = None
            resultado = json.loads(await web_crawl(url="https://x.test"))

        assert "TAVILY_API_KEY" in resultado["error"]

    async def test_crawl_com_url_invalida_nem_chega_a_checar_a_key(self):
        """Erro/borda: validar a URL antes evita mensagem enganosa sobre
        credencial quando o problema é o argumento."""
        from backend.tools.web import web_crawl

        with patch("backend.tools.web.settings") as ms:
            ms.tavily_api_key = "real-key"
            resultado = json.loads(await web_crawl(url="ftp://x.test"))

        assert "http" in resultado["error"]

    async def test_map_com_key_chama_o_cliente(self):
        from backend.tools.web import web_map

        mock_client = MagicMock()
        mock_client.map = AsyncMock(return_value={"results": ["/a", "/b"]})
        with (
            patch("backend.tools.web.settings") as ms,
            patch("backend.tools.web._tavily_client", return_value=mock_client),
            patch(
                "backend.tools.web._check_egress_url", new=AsyncMock(return_value=None)
            ),
        ):
            ms.tavily_api_key = "real-key"
            resultado = json.loads(await web_map(url="https://x.test"))

        assert resultado == ["/a", "/b"]

    async def test_crawl_recusa_dns_privado_antes_de_chamar_tavily(self):
        from backend.tools.web import web_crawl

        client = MagicMock()
        with (
            patch("backend.tools.web.settings") as ms,
            patch("backend.tools.web._tavily_client", return_value=client),
            patch(
                "backend.tools.web._check_egress_url",
                new=AsyncMock(return_value="endereço privado bloqueado"),
            ),
        ):
            ms.tavily_api_key = "real-key"
            resultado = json.loads(await web_crawl(url="https://rebind.example"))

        assert "política de egress" in resultado["error"]
        client.crawl.assert_not_called()

    async def test_map_recusa_dns_privado_antes_de_chamar_tavily(self):
        from backend.tools.web import web_map

        client = MagicMock()
        with (
            patch("backend.tools.web.settings") as ms,
            patch("backend.tools.web._tavily_client", return_value=client),
            patch(
                "backend.tools.web._check_egress_url",
                new=AsyncMock(return_value="endereço privado bloqueado"),
            ),
        ):
            ms.tavily_api_key = "real-key"
            resultado = json.loads(await web_map(url="https://rebind.example"))

        assert "política de egress" in resultado["error"]
        client.map.assert_not_called()
