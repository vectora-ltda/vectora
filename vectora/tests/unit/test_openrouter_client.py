"""``backend/llm/openrouter/client.py`` — cliente HTTP nativo do OpenRouter.

Fundação das 7 capacidades (chat, embeddings, rerank, imagem, TTS, STT,
vídeo). O tratamento de erro mora aqui uma vez só: cada capacidade herda o
mapeamento status → exceção tipada em vez de repetir.
"""

from __future__ import annotations

import httpx
import pytest

from backend.llm.openrouter.client import (
    OpenRouterAuthError,
    OpenRouterClient,
    OpenRouterCreditError,
    OpenRouterRateLimitError,
    OpenRouterResponseError,
    OpenRouterServerError,
)


def _client(handler) -> OpenRouterClient:
    """Client apontando pra um transporte mockado — nada sai pra rede."""
    transport = httpx.MockTransport(handler)
    return OpenRouterClient(
        api_key="sk-or-test",
        http_client=httpx.AsyncClient(transport=transport),
    )


class TestAuthEHeaders:
    @pytest.mark.asyncio
    async def test_manda_bearer_e_headers_de_atribuicao(self):
        capturado: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            capturado.update(request.headers)
            return httpx.Response(200, json={"ok": True})

        async with _client(handler) as c:
            await c.post_json("/chat/completions", {"model": "x"})

        assert capturado["authorization"] == "Bearer sk-or-test"
        # OpenRouter usa estes dois pra atribuir o tráfego ao app na listagem
        # pública — sem eles o uso aparece como anônimo.
        assert capturado["http-referer"]
        assert capturado["x-title"]

    def test_sem_api_key_levanta_antes_de_qualquer_request(self):
        """Erro/borda: key vazia tem que falhar na construção. Deixar passar
        rende um 401 confuso lá na frente, longe da causa."""
        with pytest.raises(OpenRouterAuthError, match="OPENROUTER_API_KEY"):
            OpenRouterClient(api_key="")

    @pytest.mark.asyncio
    async def test_chave_opcional_omite_autorizacao_e_atribuicao(self):
        capturado: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            capturado.update(request.headers)
            return httpx.Response(200, json={"ok": True})

        client = OpenRouterClient(
            api_key="",
            base_url="http://127.0.0.1:8080/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            require_api_key=False,
            include_attribution=False,
        )
        async with client:
            await client.post_json("/chat/completions", {"model": "x"})

        assert "authorization" not in capturado
        assert "http-referer" not in capturado
        assert "x-title" not in capturado


class TestMapeamentoDeErro:
    @pytest.mark.parametrize(
        ("status", "excecao"),
        [
            (401, OpenRouterAuthError),
            (402, OpenRouterCreditError),
            (429, OpenRouterRateLimitError),
            (500, OpenRouterServerError),
            (503, OpenRouterServerError),
        ],
    )
    @pytest.mark.asyncio
    async def test_status_vira_excecao_tipada(self, status, excecao):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": {"message": "falhou"}})

        async with _client(handler) as c:
            with pytest.raises(excecao):
                await c.post_json("/chat/completions", {"model": "x"})

    @pytest.mark.asyncio
    async def test_corpo_nao_json_vira_erro_tipado_e_nao_json_decode_error(self):
        """Erro/borda: proxy/CDN no meio devolve HTML numa falha. Sem este
        tratamento o `.json()` estoura com JSONDecodeError cru, que não diz
        nada ao usuário sobre o que aconteceu."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>gateway timeout</html>")

        async with _client(handler) as c:
            with pytest.raises(OpenRouterResponseError):
                await c.post_json("/chat/completions", {"model": "x"})

    @pytest.mark.asyncio
    async def test_mensagem_do_erro_do_provider_chega_na_excecao(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                402, json={"error": {"message": "Insufficient credits"}}
            )

        async with _client(handler) as c:
            with pytest.raises(OpenRouterCreditError, match="Insufficient credits"):
                await c.post_json("/chat/completions", {"model": "x"})


class TestStreamNdjsonSse:
    @pytest.mark.asyncio
    async def test_stream_sse_entrega_um_evento_por_data(self):
        corpo = (
            b'data: {"choices":[{"delta":{"content":"Oi"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":" mundo"}}]}\n\n'
            b"data: [DONE]\n\n"
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=corpo, headers={"content-type": "text/event-stream"}
            )

        async with _client(handler) as c:
            eventos = [e async for e in c.stream_sse("/chat/completions", {})]

        assert [e["choices"][0]["delta"]["content"] for e in eventos] == [
            "Oi",
            " mundo",
        ]

    @pytest.mark.asyncio
    async def test_linha_malformada_no_meio_do_stream_nao_derruba_o_turno(self):
        """Erro/borda: um chunk corrompido não pode abortar a resposta inteira
        — o que já chegou é conteúdo válido que o usuário está lendo."""
        corpo = (
            b'data: {"choices":[{"delta":{"content":"antes"}}]}\n\n'
            b"data: {isso nao e json}\n\n"
            b'data: {"choices":[{"delta":{"content":"depois"}}]}\n\n'
            b"data: [DONE]\n\n"
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=corpo, headers={"content-type": "text/event-stream"}
            )

        async with _client(handler) as c:
            eventos = [e async for e in c.stream_sse("/chat/completions", {})]

        assert [e["choices"][0]["delta"]["content"] for e in eventos] == [
            "antes",
            "depois",
        ]


class TestStreamSseRetryDeConexao:
    """S0-3: log real do usuário mostrou `httpx.ConnectError` em
    `stream_sse` — as outras chamadas de rede do mesmo boot funcionaram, o
    que aponta pra uma falha transitória (DNS/roteamento), não um provider
    fora do ar. Reproduzido ao vivo 6/6 sem falha (chamada simples +
    streaming), então não é uma condição permanente reproduzível — mas o
    caminho não tinha NENHUM retry pra esse tipo de erro, então qualquer
    blip de rede de milissegundos derrubava o turno inteiro. O fix é
    reter só a fase de CONEXÃO (antes de qualquer byte do stream chegar ao
    chamador) — depois que o primeiro chunk já foi entregue, retry
    duplicaria conteúdo."""

    @pytest.mark.asyncio
    async def test_connecterror_transitorio_reteta_e_entrega_o_stream(self):
        tentativas = {"n": 0}
        corpo = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'

        def handler(_request: httpx.Request) -> httpx.Response:
            tentativas["n"] += 1
            if tentativas["n"] < 3:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(
                200, content=corpo, headers={"content-type": "text/event-stream"}
            )

        async with _client(handler) as c:
            eventos = [
                e
                async for e in c.stream_sse(
                    "/chat/completions", {}, retry_backoff_s=0.0
                )
            ]

        assert tentativas["n"] == 3
        assert eventos[0]["choices"][0]["delta"]["content"] == "ok"

    @pytest.mark.asyncio
    async def test_erro_persistente_esgota_as_tentativas_e_propaga(self):
        """Erro/borda: falha permanente (provider genuinamente fora do ar)
        não pode ficar retentando pra sempre — esgota o teto e propaga o
        `ConnectError` original, sem mascarar a causa real."""
        tentativas = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            tentativas["n"] += 1
            raise httpx.ConnectError("connection refused")

        async with _client(handler) as c:
            with pytest.raises(httpx.ConnectError):
                async for _ in c.stream_sse(
                    "/chat/completions", {}, retry_backoff_s=0.0
                ):
                    pass

        # 1 tentativa original + teto de retries — nunca infinito.
        assert 1 < tentativas["n"] <= 4

    @pytest.mark.asyncio
    async def test_erro_apos_stream_ja_comecado_nao_reteta(self):
        """Erro/borda: um chunk já entregue ao chamador não pode ser
        retentado — reconectar reenviaria a resposta inteira, duplicando
        conteúdo que o usuário já está lendo."""
        tentativas = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            tentativas["n"] += 1
            # Simula queda de conexão NO MEIO do corpo, via stream que
            # levanta ao iterar em vez de na resposta inicial.
            return httpx.Response(
                200,
                stream=_StreamQueQuebraNoMeio(),
                headers={"content-type": "text/event-stream"},
            )

        async with _client(handler) as c:
            eventos = []
            # Precisa coletar o evento parcial ANTES da exceção estourar —
            # não dá pra reduzir a um único statement (PT012 não se aplica).
            with pytest.raises(httpx.ReadError):  # noqa: PT012
                async for e in c.stream_sse(
                    "/chat/completions", {}, retry_backoff_s=0.0
                ):
                    eventos.append(e)  # noqa: PERF401

        # Só uma tentativa de conexão — o erro veio depois do 1º evento.
        assert tentativas["n"] == 1
        assert eventos[0]["choices"][0]["delta"]["content"] == "parcial"


class _StreamQueQuebraNoMeio(httpx.AsyncByteStream):
    """Entrega 1 chunk válido e então levanta — simula queda de conexão a
    meio do stream (diferente de falhar ao conectar, que é síncrono e
    acontece antes de qualquer `yield`)."""

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"parcial"}}]}\n\n'
        raise httpx.ReadError("connection reset")

    async def aclose(self) -> None:
        return None
