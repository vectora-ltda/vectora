"""Testes para backend/api/handlers/provider_routing.py (provider routing Ollama e OpenRouter).

Valida:
- GET /provider-routing/ollama/models: host inacessível -> reachable=False (nunca
  500); host acessível -> lista de modelos.
- POST/GET/DELETE /provider-routing/ollama/registered: CRUD de modelos registrados.
- POST/DELETE /provider-routing/openrouter/key: valida contra /auth/key antes de
  persistir; nunca salva key rejeitada.
- GET /provider-routing/openrouter/models: catálogo cacheado, filtro por `q`, erro de
  rede não vira 500.
- POST/GET/DELETE /provider-routing/openrouter/registered: mesmo CRUD do Ollama.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    """App FastAPI com banco de registro (Ollama/OpenRouter) isolado.

    ``provider_routing._get_db()`` reusa o MESMO singleton de
    ``threads._get_db()`` (``~/.vectora/checkpoints.db`` real por padrão) —
    sem isolar aqui, os testes de registro/duplicata (``dup-model``,
    ``dup/model``) gravam permanentemente no banco real do usuário.
    """
    os.environ["VECTORA_AUTH_REQUIRED"] = "false"

    import aiosqlite

    import backend.api.handlers.threads as threads_mod

    tmp = tmp_path_factory.mktemp("provider_routing")
    db_file = str(tmp / "test_provider_routing.db")
    original_get_db = threads_mod._get_db
    original_db_conn = threads_mod._db_conn
    threads_mod._db_conn = None

    async def _patched_get_db():
        if threads_mod._db_conn is not None:
            return threads_mod._db_conn
        conn = await aiosqlite.connect(db_file)
        await conn.executescript(
            "PRAGMA journal_mode=WAL;"
            "PRAGMA busy_timeout=30000;"
            "PRAGMA synchronous=NORMAL;"
        )
        threads_mod._db_conn = conn
        return conn

    threads_mod._get_db = _patched_get_db  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    from backend.api.server import create_app

    yield create_app(serve_static=False)

    import asyncio

    async def _close():
        if threads_mod._db_conn is not None:
            await threads_mod._db_conn.close()

    asyncio.run(_close())
    # Restaura _get_db original — sem isso, o fake acima (que nunca cria o
    # schema `vectora_sessions`) fica ativo pro resto do processo de teste,
    # quebrando qualquer teste posterior que dependa do `_get_db()` real.
    threads_mod._get_db = original_get_db
    threads_mod._db_conn = original_db_conn


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def clean_openrouter_key():
    """Isola OPENROUTER_API_KEY nos dois sentidos — os testes de
    /provider-routing/openrouter/key escrevem em os.environ de verdade.

    Só limpar DEPOIS (teardown) não bastava: numa máquina com o provider
    já configurado de verdade (uso real do app, não só teste), o teste
    "não configurado por padrão" herdava esse estado antes mesmo de rodar
    — passava isolado (nada configurado ainda) mas falhava na suíte cheia
    assim que outro processo/sessão já tivesse configurado a chave real.
    Snapshot + reset ANTES do yield fecha esse buraco; restaura o valor
    original depois, sem vazar pro resto da suíte nem sujar o ambiente
    real do usuário."""
    from backend.settings import settings

    orig_env = os.environ.pop("OPENROUTER_API_KEY", None)
    orig_setting = settings.openrouter_api_key
    object.__setattr__(settings, "openrouter_api_key", None)
    yield
    if orig_env is not None:
        os.environ["OPENROUTER_API_KEY"] = orig_env
    else:
        os.environ.pop("OPENROUTER_API_KEY", None)
    object.__setattr__(settings, "openrouter_api_key", orig_setting)


@pytest.fixture
def clean_nine_router_config():
    """Idem (ver `clean_openrouter_key`), para NINE_ROUTER_BASE_URL/
    NINE_ROUTER_API_KEY — reset ANTES e DEPOIS do teste, não só depois."""
    from backend.settings import settings

    orig_env_url = os.environ.pop("NINE_ROUTER_BASE_URL", None)
    orig_env_key = os.environ.pop("NINE_ROUTER_API_KEY", None)
    orig_setting_url = settings.nine_router_base_url
    orig_setting_key = settings.nine_router_api_key
    object.__setattr__(settings, "nine_router_base_url", None)
    object.__setattr__(settings, "nine_router_api_key", None)
    yield
    if orig_env_url is not None:
        os.environ["NINE_ROUTER_BASE_URL"] = orig_env_url
    else:
        os.environ.pop("NINE_ROUTER_BASE_URL", None)
    if orig_env_key is not None:
        os.environ["NINE_ROUTER_API_KEY"] = orig_env_key
    else:
        os.environ.pop("NINE_ROUTER_API_KEY", None)
    object.__setattr__(settings, "nine_router_base_url", orig_setting_url)
    object.__setattr__(settings, "nine_router_api_key", orig_setting_key)


class TestOllamaDiscovery:
    def test_host_unreachable_returns_reachable_false_not_500(self, client):
        # Mocka a falha de conexão explicitamente — não depende da ausência
        # de um Ollama real rodando na máquina de dev (ver memória
        # test-hermeticity-ambient-binary.md: contar com a ausência de um
        # binário/serviço externo quebra assim que ele existir de verdade).
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_httpx.return_value = mock_ctx

            resp = client.get("/provider-routing/ollama/models")

        assert resp.status_code == 200
        body = resp.json()
        assert body["reachable"] is False
        assert body["models"] == []

    def test_host_reachable_returns_models(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "models": [
                {"name": "qwen3:8b", "size": 123, "modified_at": "2026-01-01"},
                {"name": "llama3.1:8b", "size": 456},
            ]
        }
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            resp = client.get("/provider-routing/ollama/models")

        assert resp.status_code == 200
        body = resp.json()
        assert body["reachable"] is True
        assert [m["name"] for m in body["models"]] == ["qwen3:8b", "llama3.1:8b"]


class TestOllamaRegisteredModels:
    def test_register_list_and_delete(self, client):
        create = client.post(
            "/provider-routing/ollama/registered", json={"tag": "qwen3:8b"}
        )
        assert create.status_code == 200
        model_id = create.json()["id"]
        assert create.json()["tag"] == "qwen3:8b"

        listing = client.get("/provider-routing/ollama/registered")
        assert listing.status_code == 200
        assert any(m["id"] == model_id for m in listing.json())

        deleted = client.delete(f"/provider-routing/ollama/registered/{model_id}")
        assert deleted.status_code == 200
        listing_after = client.get("/provider-routing/ollama/registered")
        assert all(m["id"] != model_id for m in listing_after.json())

    def test_register_empty_tag_returns_400(self, client):
        resp = client.post("/provider-routing/ollama/registered", json={"tag": "   "})
        assert resp.status_code == 400

    def test_register_duplicate_tag_returns_409(self, client):
        client.post("/provider-routing/ollama/registered", json={"tag": "dup-model"})
        resp = client.post(
            "/provider-routing/ollama/registered", json={"tag": "dup-model"}
        )
        assert resp.status_code == 409


class TestLlamaCppAndHuggingFace:
    def test_llamacpp_discovery_uses_openai_models_endpoint(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [{"id": "Qwen3-8B", "name": "Qwen3-8B"}]
        }
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx
            response = client.get("/provider-routing/llamacpp/models")

        assert response.status_code == 200
        assert response.json() == {
            "reachable": True,
            "models": [{"id": "Qwen3-8B", "name": "Qwen3-8B"}],
        }
        mock_ctx.get.assert_awaited_once()
        assert mock_ctx.get.await_args is not None
        assert mock_ctx.get.await_args.args[0].endswith("/v1/models")

    def test_huggingface_catalog_is_shared_by_runtimes(self, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {
                "id": "org/model-GGUF",
                "pipeline_tag": "text-generation",
                "downloads": 12,
                "cardData": {"license": "apache-2.0"},
            }
        ]
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx
            llama = client.get("/provider-routing/huggingface/models?provider=llamacpp")
            ollama = client.get("/provider-routing/huggingface/models?provider=ollama")

        assert llama.status_code == ollama.status_code == 200
        assert llama.json()["models"][0]["provider"] == "llamacpp"
        assert ollama.json()["models"][0]["provider"] == "ollama"

    def test_huggingface_catalog_rejects_unknown_runtime(self, client):
        response = client.get("/provider-routing/huggingface/models?provider=unknown")
        assert response.status_code == 400

    def test_huggingface_metadata_rejects_invalid_repo(self, client):
        response = client.get("/provider-routing/huggingface/models/not-a-repo")
        assert response.status_code == 400

    def test_huggingface_download_rejects_path_traversal(self, client):
        response = client.post(
            "/provider-routing/huggingface/download",
            json={"repo_id": "org/model", "filename": "../model.gguf"},
        )
        assert response.status_code == 400

    def test_llamacpp_install_rejects_non_official_url(self, client):
        response = client.post(
            "/provider-routing/llamacpp/install",
            json={"asset_url": "https://example.com/llama.zip"},
        )
        assert response.status_code == 400

    def test_llamacpp_install_rejects_invalid_checksum(self, client):
        response = client.post(
            "/provider-routing/llamacpp/install",
            json={
                "asset_url": "https://github.com/ggml-org/llama.cpp/releases/download/b1/llama.zip",
                "sha256": "bad",
            },
        )
        assert response.status_code == 400


class TestOpenRouterKey:
    def test_status_not_configured_by_default(self, client, clean_openrouter_key):
        os.environ.pop("OPENROUTER_API_KEY", None)
        resp = client.get("/provider-routing/openrouter/status")
        assert resp.status_code == 200
        assert resp.json() == {"configured": False, "masked": ""}

    def test_set_key_valid_persists_and_masks(
        self, client, clean_openrouter_key, tmp_path
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with (
            patch("httpx.AsyncClient") as mock_httpx,
            patch(
                "backend.api.handlers.provider_routing._env_file",
                return_value=tmp_path / ".env",
            ),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            resp = client.post(
                "/provider-routing/openrouter/key",
                json={"api_key": "sk-or-v1-abcdef123456"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is True
        assert body["masked"].startswith("sk-or-")
        assert "abcdef123456" not in body["masked"]
        assert os.environ["OPENROUTER_API_KEY"] == "sk-or-v1-abcdef123456"

    def test_set_key_rejected_by_openrouter_returns_400(
        self, client, clean_openrouter_key, tmp_path
    ):
        mock_response = MagicMock()
        mock_response.status_code = 401

        with (
            patch("httpx.AsyncClient") as mock_httpx,
            patch(
                "backend.api.handlers.provider_routing._env_file",
                return_value=tmp_path / ".env",
            ),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            resp = client.post(
                "/provider-routing/openrouter/key", json={"api_key": "bad-key"}
            )

        assert resp.status_code == 400
        assert "OPENROUTER_API_KEY" not in os.environ

    def test_set_key_empty_returns_400_without_network_call(
        self, client, clean_openrouter_key
    ):
        with patch("httpx.AsyncClient") as mock_httpx:
            resp = client.post(
                "/provider-routing/openrouter/key", json={"api_key": "   "}
            )
        assert resp.status_code == 400
        mock_httpx.assert_not_called()

    def test_clear_key_removes_env(self, client, clean_openrouter_key, tmp_path):
        os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-should-be-removed"
        with patch(
            "backend.api.handlers.provider_routing._env_file",
            return_value=tmp_path / ".env",
        ):
            resp = client.delete("/provider-routing/openrouter/key")
        assert resp.status_code == 200
        assert resp.json() == {"configured": False, "masked": ""}
        assert "OPENROUTER_API_KEY" not in os.environ


class TestOpenRouterCatalog:
    @staticmethod
    def _reset_cache() -> None:
        from backend.api.handlers.provider_routing import _catalog_cache

        _catalog_cache["fetched_at"] = float("-inf")
        _catalog_cache["models"] = []

    @pytest.fixture(autouse=True)
    def _isolated_cache(self):
        # Reseta antes E depois — o fixture `client` deste arquivo é
        # module-scoped (mesmo TestClient/app pra todos os testes), então
        # qualquer estado deixado em `_catalog_cache` por este teste não
        # pode vazar pro próximo, seja qual for a ordem de execução.
        self._reset_cache()
        yield
        self._reset_cache()

    @contextmanager
    def _mocked_http_client(
        self, app, handler: Callable[[httpx.Request], httpx.Response]
    ) -> Iterator[None]:
        """Troca o client HTTP do endpoint via dependency override do
        FastAPI — mais correto/idiomático que `unittest.mock.patch
        ("httpx.AsyncClient", ...)`, resolvido pelo próprio FastAPI dentro
        do mesmo contexto async da request em vez de mutar um atributo
        global. (O flake real do catálogo em CI era outro — ver
        `test_catalog_stale_sentinel_survives_low_monotonic_clock` — mas
        dependency override continua a forma certa de mockar isso.)
        """
        from backend.api.handlers.provider_routing import _get_http_client

        async def _fake_client():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                yield client

        app.dependency_overrides[_get_http_client] = _fake_client
        try:
            yield
        finally:
            app.dependency_overrides.pop(_get_http_client, None)

    def test_catalog_returns_models(self, app, client):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "openai/gpt-4o",
                            "name": "GPT-4o",
                            "context_length": 128000,
                        },
                        {
                            "id": "anthropic/claude-3.5-sonnet",
                            "name": "Claude 3.5 Sonnet",
                        },
                    ]
                },
            )

        with self._mocked_http_client(app, handler):
            resp = client.get("/provider-routing/openrouter/models")

        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["models"]]
        assert ids == ["openai/gpt-4o", "anthropic/claude-3.5-sonnet"]

    def test_catalog_filters_by_q(self, app, client):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "openai/gpt-4o", "name": "GPT-4o"},
                        {
                            "id": "anthropic/claude-3.5-sonnet",
                            "name": "Claude 3.5 Sonnet",
                        },
                    ]
                },
            )

        with self._mocked_http_client(app, handler):
            resp = client.get(
                "/provider-routing/openrouter/models", params={"q": "claude"}
            )

        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["models"]]
        assert ids == ["anthropic/claude-3.5-sonnet"]

    def test_catalog_network_error_cai_no_fallback_e_nao_500(self, app, client):
        """Erro de rede nunca vira 500 — e, desde o cliente nativo, também
        não vira lista vazia: cai na lista embutida (ver
        `OPENROUTER_FALLBACK_MODELS`), senão o seletor de modelo aparece sem
        opção nenhuma e parece falta de suporte, não falta de internet."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network down")

        with self._mocked_http_client(app, handler):
            resp = client.get("/provider-routing/openrouter/models")
        assert resp.status_code == 200
        assert resp.json()["models"], "catálogo vazio com a rede fora"

    def test_catalog_stale_sentinel_survives_low_monotonic_clock(
        self, app, client, monkeypatch
    ):
        """Bug real reproduzido só em CI (Linux, VM efêmera recém-bootada),
        nunca localmente: `time.monotonic()` não conta a partir de zero —
        reflete o uptime da máquina. Numa VM com menos de
        `_OPENROUTER_CATALOG_TTL_S` (3600s) de uptime, `now` já é menor que
        o TTL sozinho; um sentinela `fetched_at=0.0` faz `now - 0.0 > TTL`
        dar falso, então o cache "resetado" parece recém-buscado e o
        endpoint devolve a lista vazia direto, sem nunca tentar buscar —
        exatamente o `assert [] == [...]` visto na CI. Simula esse uptime
        baixo aqui: se o sentinela correto (`-inf`) estiver em uso, o fetch
        acontece de qualquer forma."""
        import time as time_mod

        monkeypatch.setattr(time_mod, "monotonic", lambda: 45.0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"data": [{"id": "openai/gpt-4o", "name": "GPT-4o"}]}
            )

        with self._mocked_http_client(app, handler):
            resp = client.get("/provider-routing/openrouter/models")

        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["models"]]
        assert ids == ["openai/gpt-4o"]


class TestOpenRouterRegisteredModels:
    def test_register_list_and_delete(self, client):
        create = client.post(
            "/provider-routing/openrouter/registered", json={"tag": "openai/gpt-4o"}
        )
        assert create.status_code == 200
        model_id = create.json()["id"]
        assert create.json()["tag"] == "openai/gpt-4o"

        listing = client.get("/provider-routing/openrouter/registered")
        assert listing.status_code == 200
        assert any(m["id"] == model_id for m in listing.json())

        deleted = client.delete(f"/provider-routing/openrouter/registered/{model_id}")
        assert deleted.status_code == 200
        listing_after = client.get("/provider-routing/openrouter/registered")
        assert all(m["id"] != model_id for m in listing_after.json())

    def test_register_empty_tag_returns_400(self, client):
        resp = client.post(
            "/provider-routing/openrouter/registered", json={"tag": "   "}
        )
        assert resp.status_code == 400

    def test_register_duplicate_tag_returns_409(self, client):
        client.post(
            "/provider-routing/openrouter/registered", json={"tag": "dup/model"}
        )
        resp = client.post(
            "/provider-routing/openrouter/registered", json={"tag": "dup/model"}
        )
        assert resp.status_code == 409


class TestCatalogoOpenRouterComFallback:
    """Rede fora não pode devolver catálogo vazio.

    Lista vazia faz o seletor de modelo aparecer sem nenhuma opção, o que o
    usuário lê como "o Vectora não suporta OpenRouter" em vez de "estou sem
    internet". O Hermes resolve com uma lista embutida
    (`hermes_cli/models.py:1478`) — mesmo padrão aqui.
    """

    @staticmethod
    def _reset_cache() -> None:
        from backend.api.handlers import provider_routing as pr

        pr._catalog_cache["fetched_at"] = float("-inf")
        pr._catalog_cache["models"] = []

    @pytest.mark.asyncio
    async def test_rede_ok_devolve_o_catalogo_real(self):
        from unittest.mock import AsyncMock, MagicMock

        from backend.api.handlers.provider_routing import discover_openrouter_models

        self._reset_cache()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(
            return_value={
                "data": [
                    {
                        "id": "algum/modelo-novo",
                        "name": "Novo",
                        "context_length": 128000,
                    }
                ]
            }
        )
        client = MagicMock()
        client.get = AsyncMock(return_value=resp)

        resultado = await discover_openrouter_models(client)

        assert [m.id for m in resultado.models] == ["algum/modelo-novo"]

    @pytest.mark.asyncio
    async def test_rede_fora_com_cache_vazio_cai_na_lista_embutida(self):
        """Erro/borda: é o caso que hoje devolve `models=[]`."""
        from unittest.mock import AsyncMock, MagicMock

        from backend.api.handlers.provider_routing import (
            OPENROUTER_FALLBACK_MODELS,
            discover_openrouter_models,
        )

        self._reset_cache()
        client = MagicMock()
        client.get = AsyncMock(side_effect=OSError("sem rede"))

        resultado = await discover_openrouter_models(client)

        assert resultado.models, "catálogo vazio com a rede fora"
        assert {m.id for m in resultado.models} == {
            m["id"] for m in OPENROUTER_FALLBACK_MODELS
        }

    @pytest.mark.asyncio
    async def test_busca_filtra_tambem_no_fallback(self):
        from unittest.mock import AsyncMock, MagicMock

        from backend.api.handlers.provider_routing import discover_openrouter_models

        self._reset_cache()
        client = MagicMock()
        client.get = AsyncMock(side_effect=OSError("sem rede"))

        resultado = await discover_openrouter_models(client, q="anthropic")

        assert resultado.models
        assert all("anthropic" in m.id.lower() for m in resultado.models)

    @pytest.mark.asyncio
    async def test_fallback_nao_sobrescreve_cache_valido(self):
        """Erro/borda: uma falha momentânea não pode substituir o catálogo
        real já em cache pela lista curta embutida."""
        from unittest.mock import AsyncMock, MagicMock

        from backend.api.handlers import provider_routing as pr
        from backend.api.handlers.provider_routing import (
            OpenRouterModelInfo,
            discover_openrouter_models,
        )

        self._reset_cache()
        pr._catalog_cache["models"] = [
            OpenRouterModelInfo(id="cacheado/modelo", name="Cacheado")
        ]
        client = MagicMock()
        client.get = AsyncMock(side_effect=OSError("sem rede"))

        resultado = await discover_openrouter_models(client)

        assert [m.id for m in resultado.models] == ["cacheado/modelo"]

    @pytest.mark.asyncio
    async def test_catalogo_extrai_input_modalities_por_modelo(self):
        """`input_modalities` varia por modelo — não é um campo fixo do
        provider, é o que permite checar vision por modelo em vez de por
        provider inteiro."""
        from unittest.mock import AsyncMock, MagicMock

        from backend.api.handlers.provider_routing import discover_openrouter_models

        self._reset_cache()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(
            return_value={
                "data": [
                    {
                        "id": "openai/gpt-4o",
                        "name": "GPT-4o",
                        "architecture": {"input_modalities": ["text", "image"]},
                    },
                    {
                        "id": "deepseek/deepseek-r1",
                        "name": "DeepSeek R1",
                        "architecture": {"input_modalities": ["text"]},
                    },
                ]
            }
        )
        client = MagicMock()
        client.get = AsyncMock(return_value=resp)

        resultado = await discover_openrouter_models(client)

        by_id = {m.id: m for m in resultado.models}
        assert by_id["openai/gpt-4o"].input_modalities == ["text", "image"]
        assert by_id["deepseek/deepseek-r1"].input_modalities == ["text"]


class TestOpenRouterModelSupportsImage:
    """`openrouter_model_supports_image` — checagem de vision por modelo
    real, não por "openrouter" como bloco único."""

    @staticmethod
    def _reset_cache() -> None:
        from backend.api.handlers import provider_routing as pr

        pr._catalog_cache["fetched_at"] = float("-inf")
        pr._catalog_cache["models"] = []

    @pytest.mark.asyncio
    async def test_modelo_com_image_no_catalogo_retorna_true(self):
        from backend.api.handlers import provider_routing as pr
        from backend.api.handlers.provider_routing import (
            OpenRouterModelInfo,
            openrouter_model_supports_image,
        )

        self._reset_cache()
        pr._catalog_cache["fetched_at"] = 0.0
        pr._catalog_cache["models"] = [
            OpenRouterModelInfo(
                id="openai/gpt-4o", name="GPT-4o", input_modalities=["text", "image"]
            )
        ]

        assert await openrouter_model_supports_image("openai/gpt-4o") is True

    @pytest.mark.asyncio
    async def test_modelo_com_modalidade_image_com_espacos_retorna_true(self):
        from backend.api.handlers import provider_routing as pr
        from backend.api.handlers.provider_routing import (
            CapabilityState,
            OpenRouterModelInfo,
            openrouter_model_image_state,
        )

        self._reset_cache()
        pr._catalog_cache["fetched_at"] = 0.0
        pr._catalog_cache["models"] = [
            OpenRouterModelInfo(
                id="openai/gpt-4o",
                name="GPT-4o",
                input_modalities=["text", " image ", "vision "],
            )
        ]

        assert (
            await openrouter_model_image_state("openai/gpt-4o")
            is CapabilityState.SUPPORTED
        )

    @pytest.mark.asyncio
    async def test_modelo_sem_image_no_catalogo_retorna_false(self):
        """Erro/borda central deste bug: nem todo modelo do OpenRouter
        processa imagem — o catálogo é quem decide, não o provider."""
        from backend.api.handlers import provider_routing as pr
        from backend.api.handlers.provider_routing import (
            OpenRouterModelInfo,
            openrouter_model_supports_image,
        )

        self._reset_cache()
        pr._catalog_cache["fetched_at"] = 0.0
        pr._catalog_cache["models"] = [
            OpenRouterModelInfo(
                id="deepseek/deepseek-r1",
                name="DeepSeek R1",
                input_modalities=["text"],
            )
        ]

        assert await openrouter_model_supports_image("deepseek/deepseek-r1") is False

    @pytest.mark.asyncio
    async def test_modelo_ausente_do_catalogo_falha_aberto(self, monkeypatch):
        """Modelo não encontrado (id incomum, catálogo indisponível) não
        pode bloquear preventivamente — fail-open deixa a chamada real ao
        provider decidir."""
        from backend.api.handlers import provider_routing as pr
        from backend.api.handlers.provider_routing import (
            OpenRouterModelInfo,
            openrouter_model_supports_image,
        )

        self._reset_cache()
        pr._catalog_cache["fetched_at"] = 0.0
        pr._catalog_cache["models"] = [
            OpenRouterModelInfo(id="outro/modelo", name="Outro", input_modalities=[])
        ]

        assert await openrouter_model_supports_image("id/inexistente") is True

    @pytest.mark.asyncio
    async def test_cache_vazio_tenta_popular_antes_de_checar(self, monkeypatch):
        from backend.api.handlers import provider_routing as pr
        from backend.api.handlers.provider_routing import (
            openrouter_model_supports_image,
        )

        self._reset_cache()

        async def _fake_ensure(client):
            pr._catalog_cache["models"] = [
                pr.OpenRouterModelInfo(
                    id="openai/gpt-4o",
                    name="GPT-4o",
                    input_modalities=["text", "image"],
                )
            ]
            pr._catalog_cache["fetched_at"] = 0.0

        monkeypatch.setattr(pr, "_ensure_openrouter_catalog_cached", _fake_ensure)

        assert await openrouter_model_supports_image("openai/gpt-4o") is True


class TestNineRouterConfig:
    def test_status_not_configured_by_default(self, client, clean_nine_router_config):
        resp = client.get("/provider-routing/nine-router/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is False
        assert body["masked"] == ""

    def test_set_config_persists_both_fields(
        self, client, clean_nine_router_config, tmp_path
    ):
        with patch(
            "backend.api.handlers.provider_routing._env_file",
            return_value=tmp_path / ".env",
        ):
            resp = client.post(
                "/provider-routing/nine-router/config",
                json={
                    "base_url": "http://localhost:20128/v1",
                    "api_key": "9r-abcdef123456",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is True
        assert body["base_url"] == "http://localhost:20128/v1"
        assert body["masked"].startswith("9r-abc")
        assert "abcdef123456" not in body["masked"]
        assert os.environ["NINE_ROUTER_BASE_URL"] == "http://localhost:20128/v1"
        assert os.environ["NINE_ROUTER_API_KEY"] == "9r-abcdef123456"

    def test_set_config_empty_base_url_returns_400(
        self, client, clean_nine_router_config, tmp_path
    ):
        with patch(
            "backend.api.handlers.provider_routing._env_file",
            return_value=tmp_path / ".env",
        ):
            resp = client.post(
                "/provider-routing/nine-router/config",
                json={"base_url": "   ", "api_key": "key"},
            )
        assert resp.status_code == 400
        assert "NINE_ROUTER_BASE_URL" not in os.environ

    def test_set_config_empty_api_key_returns_400(
        self, client, clean_nine_router_config, tmp_path
    ):
        with patch(
            "backend.api.handlers.provider_routing._env_file",
            return_value=tmp_path / ".env",
        ):
            resp = client.post(
                "/provider-routing/nine-router/config",
                json={"base_url": "http://localhost:20128/v1", "api_key": "   "},
            )
        assert resp.status_code == 400
        assert "NINE_ROUTER_API_KEY" not in os.environ

    def test_clear_config_removes_both(
        self, client, clean_nine_router_config, tmp_path
    ):
        with patch(
            "backend.api.handlers.provider_routing._env_file",
            return_value=tmp_path / ".env",
        ):
            client.post(
                "/provider-routing/nine-router/config",
                json={"base_url": "http://localhost:20128/v1", "api_key": "9r-key"},
            )
            resp = client.delete("/provider-routing/nine-router/config")
        assert resp.status_code == 200
        assert resp.json() == {"configured": False, "base_url": None, "masked": ""}
        assert "NINE_ROUTER_BASE_URL" not in os.environ
        assert "NINE_ROUTER_API_KEY" not in os.environ


class TestNineRouterDiscovery:
    def test_not_configured_returns_reachable_false_not_500(
        self, client, clean_nine_router_config
    ):
        resp = client.get("/provider-routing/nine-router/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["reachable"] is False
        assert body["models"] == []

    def test_host_unreachable_returns_reachable_false_not_500(
        self, client, clean_nine_router_config, tmp_path
    ):
        with patch(
            "backend.api.handlers.provider_routing._env_file",
            return_value=tmp_path / ".env",
        ):
            client.post(
                "/provider-routing/nine-router/config",
                json={"base_url": "http://localhost:20128/v1", "api_key": "9r-key"},
            )
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_httpx.return_value = mock_ctx

            resp = client.get("/provider-routing/nine-router/models")

        assert resp.status_code == 200
        body = resp.json()
        assert body["reachable"] is False
        assert body["models"] == []

    def test_host_reachable_returns_models(
        self, client, clean_nine_router_config, tmp_path
    ):
        with patch(
            "backend.api.handlers.provider_routing._env_file",
            return_value=tmp_path / ".env",
        ):
            client.post(
                "/provider-routing/nine-router/config",
                json={"base_url": "http://localhost:20128/v1", "api_key": "9r-key"},
            )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "cc/claude-opus-4-7", "name": "Claude Opus 4.7"},
                {"id": "openai/gpt-4o"},
            ]
        }
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            resp = client.get("/provider-routing/nine-router/models")

        assert resp.status_code == 200
        body = resp.json()
        assert body["reachable"] is True
        assert [m["id"] for m in body["models"]] == [
            "cc/claude-opus-4-7",
            "openai/gpt-4o",
        ]


class TestNineRouterRegisteredModels:
    def test_register_list_and_delete(self, client):
        create = client.post(
            "/provider-routing/nine-router/registered",
            json={"tag": "cc/claude-opus-4-7"},
        )
        assert create.status_code == 200
        model_id = create.json()["id"]
        assert create.json()["tag"] == "cc/claude-opus-4-7"

        listing = client.get("/provider-routing/nine-router/registered")
        assert listing.status_code == 200
        assert any(m["id"] == model_id for m in listing.json())

        deleted = client.delete(f"/provider-routing/nine-router/registered/{model_id}")
        assert deleted.status_code == 200
        listing_after = client.get("/provider-routing/nine-router/registered")
        assert all(m["id"] != model_id for m in listing_after.json())

    def test_register_empty_tag_returns_400(self, client):
        resp = client.post(
            "/provider-routing/nine-router/registered", json={"tag": "   "}
        )
        assert resp.status_code == 400

    def test_register_duplicate_tag_returns_409(self, client):
        client.post(
            "/provider-routing/nine-router/registered", json={"tag": "dup/nine-model"}
        )
        resp = client.post(
            "/provider-routing/nine-router/registered", json={"tag": "dup/nine-model"}
        )
        assert resp.status_code == 409
