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

import asyncio
import hashlib
import json
import os
import zipfile
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api.handlers.provider_routing import (
    LlamaCppRollbackRequest,
    _confirmation_evidence_sha256,
    _extract_llamacpp_archive,
    llamacpp_runtime_status,
    remove_llamacpp_runtime,
)


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
        """Executa a operação  patched get db."""
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
        """Executa a operação  close."""
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
    """Executa a operação client."""
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


@pytest.fixture
def clean_llamacpp_config():
    """Isola as configurações e variáveis de ambiente do llama.cpp."""
    from backend.settings import settings

    keys = (
        "LLAMACPP_BASE_URL",
        "LLAMACPP_MODEL",
        "LLAMACPP_API_KEY",
        "LLAMACPP_MODE",
    )
    original_env = {key: os.environ.get(key) for key in keys}
    original = (
        settings.llamacpp_base_url,
        settings.llamacpp_model,
        settings.llamacpp_api_key,
        os.environ.get("LLAMACPP_MODE"),
    )
    for key in keys:
        os.environ.pop(key, None)
    object.__setattr__(settings, "llamacpp_base_url", None)
    object.__setattr__(settings, "llamacpp_model", None)
    object.__setattr__(settings, "llamacpp_api_key", None)
    yield
    for key, value in original_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    object.__setattr__(settings, "llamacpp_base_url", original[0])
    object.__setattr__(settings, "llamacpp_model", original[1])
    object.__setattr__(settings, "llamacpp_api_key", original[2])


class TestOllamaDiscovery:
    def test_ollama_release_tag_persists_and_allows_explicit_override(
        self, monkeypatch, tmp_path
    ):
        """Verifica o comportamento de ollama release tag persists and allows explicit override."""
        from backend.api.handlers import provider_routing

        tools_dir = tmp_path / "tools" / "ollama"
        tools_dir.mkdir(parents=True)
        (tools_dir / "release.json").write_text(
            json.dumps({"tag": "v0.12.3"}), encoding="utf-8"
        )
        assert provider_routing._ollama_release_tag(tools_dir) == "v0.12.3"

        monkeypatch.setenv("VECTORA_OLLAMA_RELEASE_TAG", "v0.13.0")
        assert provider_routing._ollama_release_tag(tools_dir) == "v0.13.0"

        monkeypatch.setenv("VECTORA_OLLAMA_RELEASE_TAG", "../unsafe")
        with pytest.raises(ValueError, match="inválida"):
            provider_routing._ollama_release_tag(tools_dir)

    def test_trusted_cli_rejects_relative_path_from_path(self, monkeypatch):
        """Verifica o comportamento de trusted cli rejects relative path from path."""
        from backend.api.handlers import provider_routing

        monkeypatch.setattr(provider_routing.shutil, "which", lambda _: "winget")

        assert provider_routing._trusted_cli_executable("winget") is None

    def test_trusted_cli_accepts_existing_absolute_executable(
        self, monkeypatch, tmp_path
    ):
        """Verifica o comportamento de trusted cli accepts existing absolute executable."""
        from backend.api.handlers import provider_routing

        executable = tmp_path / "winget.exe"
        executable.write_bytes(b"stub")
        monkeypatch.setattr(provider_routing.shutil, "which", lambda _: str(executable))

        assert provider_routing._trusted_cli_executable("winget") == executable

    def test_host_unreachable_returns_reachable_false_not_500(self, client):
        # Mocka a falha de conexão explicitamente — não depende da ausência
        # de um Ollama real rodando na máquina de dev (ver memória
        # test-hermeticity-ambient-binary.md: contar com a ausência de um
        # binário/serviço externo quebra assim que ele existir de verdade).
        """Verifica o comportamento de host unreachable returns reachable false not 500."""
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

    @pytest.mark.asyncio
    async def test_install_ollama_applies_global_deadline(self, monkeypatch):
        """Verifica o comportamento de install ollama applies global deadline."""
        from backend.api.handlers import provider_routing

        monkeypatch.setattr(
            provider_routing,
            "OLLAMA_INSTALL_TOTAL_TIMEOUT_SECONDS",
            0.001,
        )

        async def never_finishes(_):
            """Executa a operação never finishes."""
            await asyncio.sleep(60)

        monkeypatch.setattr(
            provider_routing, "_install_ollama_runtime_impl", never_finishes
        )

        with pytest.raises(HTTPException) as error:
            await provider_routing.install_ollama_runtime(None)
        assert error.value.status_code == 504
        assert "prazo total" in str(error.value.detail)

    @pytest.mark.asyncio
    async def test_ollama_cleanup_kills_and_waits_with_own_deadline(self, monkeypatch):
        """Verifica o comportamento de ollama cleanup kills and waits with own deadline."""
        from backend.api.handlers import provider_routing

        monkeypatch.setattr(provider_routing, "OLLAMA_CLEANUP_TIMEOUT_SECONDS", 0.001)
        process = MagicMock()
        process.kill = MagicMock()
        process.wait = AsyncMock()

        process.communicate = AsyncMock(side_effect=TimeoutError)
        await provider_routing._terminate_ollama_process(process)

        process.kill.assert_called_once_with()
        process.wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ollama_cleanup_returns_when_both_waits_hang(self, monkeypatch):
        """Verifica o comportamento de ollama cleanup returns when both waits hang."""
        from backend.api.handlers import provider_routing

        monkeypatch.setattr(provider_routing, "OLLAMA_CLEANUP_TIMEOUT_SECONDS", 0.01)
        process = MagicMock()
        process.kill = MagicMock()

        async def hangs():
            """Executa a operação hangs."""
            await asyncio.sleep(60)

        process.communicate = AsyncMock(side_effect=TimeoutError)
        process.wait = AsyncMock(side_effect=hangs)
        await provider_routing._terminate_ollama_process(process)
        process.kill.assert_called_once_with()
        process.wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ollama_shutdown_stops_only_managed_process(self, monkeypatch):
        """Verifica o comportamento de ollama shutdown stops only managed process."""
        from backend.api.handlers import provider_routing

        process = MagicMock()
        process.kill = MagicMock()
        process.communicate = AsyncMock(return_value=(b"", b""))
        monkeypatch.setattr(provider_routing, "_managed_ollama_process", process)

        await provider_routing.stop_managed_ollama()

        assert provider_routing._managed_ollama_process is None
        process.kill.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_install_ollama_serializes_concurrent_requests(self, monkeypatch):
        """Verifica o comportamento de install ollama serializes concurrent requests."""
        from backend.api.handlers import provider_routing

        monkeypatch.setattr(provider_routing, "OLLAMA_INSTALL_TOTAL_TIMEOUT_SECONDS", 1)
        active = 0
        maximum = 0

        async def serialized(_):
            """Executa a operação serialized."""
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return "done"

        monkeypatch.setattr(
            provider_routing, "_install_ollama_runtime_impl", serialized
        )
        await asyncio.gather(
            provider_routing.install_ollama_runtime(None),
            provider_routing.install_ollama_runtime(None),
        )
        assert maximum == 1

    @pytest.mark.asyncio
    async def test_ollama_install_reports_ready_phase_after_readiness(
        self, monkeypatch, tmp_path
    ):
        """O wizard só publica ``ready`` depois de confirmar o endpoint."""
        from backend.api.handlers import provider_routing

        executable = tmp_path / "ollama.exe"
        executable.write_bytes(b"stub")
        monkeypatch.setattr(provider_routing, "_ollama_executable", lambda: executable)

        async def reachable():
            """Executa a operação reachable."""
            return True, "0.1.0-test"

        monkeypatch.setattr(provider_routing, "_ollama_http_status", reachable)
        result = await provider_routing._install_ollama_runtime_impl(None)

        assert result.state == "running"
        assert result.phase == "ready"
        assert provider_routing._ollama_install_phase == "ready"

    @pytest.mark.asyncio
    async def test_ollama_install_cancelled_phase_is_observable(self, monkeypatch):
        """Verifica o comportamento de ollama install cancelled phase is observable."""
        from backend.api.handlers import provider_routing

        async def cancelled(_):
            """Executa a operação cancelled."""
            raise asyncio.CancelledError

        monkeypatch.setattr(provider_routing, "_install_ollama_runtime_impl", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await provider_routing.install_ollama_runtime(None)
        assert provider_routing._ollama_install_phase == "cancelled"

    @pytest.mark.asyncio
    async def test_ollama_unexpected_failure_leaves_failed_phase(self, monkeypatch):
        """Verifica o comportamento de ollama unexpected failure leaves failed phase."""
        from backend.api.handlers import provider_routing

        async def broken(_):
            """Executa a operação broken."""
            raise RuntimeError("falha inesperada")

        monkeypatch.setattr(provider_routing, "_install_ollama_runtime_impl", broken)
        with pytest.raises(RuntimeError, match="falha inesperada"):
            await provider_routing.install_ollama_runtime(None)
        assert provider_routing._ollama_install_phase == "failed"

    @pytest.mark.asyncio
    async def test_ollama_status_exposes_failed_phase_as_error(self, monkeypatch):
        """Verifica o comportamento de ollama status exposes failed phase as error."""
        from backend.api.handlers import provider_routing

        monkeypatch.setattr(provider_routing, "_ollama_install_phase", "failed")
        monkeypatch.setattr(provider_routing, "_ollama_executable", lambda: None)

        async def unreachable():
            """Executa a operação unreachable."""
            return False, None

        monkeypatch.setattr(provider_routing, "_ollama_http_status", unreachable)
        status = await provider_routing.ollama_runtime_status()

        assert status.state == "error"
        assert status.phase == "failed"

    @pytest.mark.asyncio
    async def test_ollama_resume_rejects_mismatched_content_range(
        self, monkeypatch, tmp_path
    ):
        """Verifica o comportamento de ollama resume rejects mismatched content range."""
        from backend.api.handlers import provider_routing
        from backend.settings import settings

        original_home = settings.vectora_home
        object.__setattr__(settings, "vectora_home", Path(tmp_path))
        installer = Path(tmp_path) / "tools" / "ollama" / "OllamaSetup.exe"
        partial = installer.with_suffix(".exe.part")
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"abc")
        Path(f"{partial}.json").write_text(
            json.dumps(
                {
                    "tag": "v1",
                    "url": "https://github.com/ollama/ollama/releases/download/v1/OllamaSetup.exe",
                    "size": 4,
                    "digest": None,
                }
            )
        )

        class Response:
            status_code = 206
            headers = {"content-range": "bytes 0-3/4"}

            def raise_for_status(self):
                """Executa a operação raise for status."""
                return

            def json(self):
                """Executa a operação json."""
                return {
                    "tag_name": "v1",
                    "assets": [
                        {
                            "name": "OllamaSetup.exe",
                            "browser_download_url": "https://github.com/ollama/ollama/releases/download/v1/OllamaSetup.exe",
                            "size": 4,
                            "digest": None,
                        }
                    ],
                }

            async def __aenter__(self):
                """Implementa a operação especial __aenter__."""
                return self

            async def __aexit__(self, *_):
                """Implementa a operação especial __aexit__."""
                return False

            async def aiter_bytes(self, _chunk_size):
                """Executa a operação aiter bytes."""
                yield b"d"

        class Client:
            async def __aenter__(self):
                """Implementa a operação especial __aenter__."""
                return self

            async def __aexit__(self, *_):
                """Implementa a operação especial __aexit__."""
                return False

            async def get(self, _url):
                """Executa a operação get."""
                return Response()

            def stream(self, _method, _url, headers=None):
                """Executa a operação stream."""
                return Response()

        monkeypatch.setattr(provider_routing.httpx, "AsyncClient", lambda **_: Client())

        with pytest.raises(HTTPException) as error:
            await provider_routing._install_ollama_from_official_release()

        assert error.value.status_code == 502
        assert not partial.exists()
        assert not Path(f"{partial}.json").exists()
        object.__setattr__(settings, "vectora_home", original_home)

    @pytest.mark.asyncio
    async def test_ollama_download_rejects_digest_mismatch_and_cleans_partial(
        self, monkeypatch, tmp_path
    ):
        """A valid-size installer must still match the publisher digest."""
        from backend.api.handlers import provider_routing
        from backend.settings import settings

        original_home = settings.vectora_home
        object.__setattr__(settings, "vectora_home", Path(tmp_path))
        content = b"installer-bytes"
        installer = Path(tmp_path) / "tools" / "ollama" / "OllamaSetup.exe"

        class Response:
            status_code = 200
            headers: dict[str, str] = {}

            def raise_for_status(self):
                """Executa a operação raise for status."""
                return

            def json(self):
                """Executa a operação json."""
                return {
                    "tag_name": "v1",
                    "assets": [
                        {
                            "name": "OllamaSetup.exe",
                            "browser_download_url": "https://example.invalid/OllamaSetup.exe",
                            "size": len(content),
                            "digest": "sha256:" + ("0" * 64),
                        }
                    ],
                }

            async def __aenter__(self):
                """Implementa a operação especial __aenter__."""
                return self

            async def __aexit__(self, *_):
                """Implementa a operação especial __aexit__."""
                return False

            async def aiter_bytes(self, _chunk_size):
                """Executa a operação aiter bytes."""
                yield content

        class Client:
            async def __aenter__(self):
                """Implementa a operação especial __aenter__."""
                return self

            async def __aexit__(self, *_):
                """Implementa a operação especial __aexit__."""
                return False

            async def get(self, _url):
                """Executa a operação get."""
                return Response()

            def stream(self, _method, _url, headers=None):
                """Executa a operação stream."""
                return Response()

        monkeypatch.setattr(provider_routing.httpx, "AsyncClient", lambda **_: Client())

        with pytest.raises(HTTPException, match="digest"):
            await provider_routing._install_ollama_from_official_release()

        assert not installer.exists()
        assert not installer.with_suffix(".exe.part").exists()
        assert not Path(f"{installer.with_suffix('.exe.part')}.json").exists()
        object.__setattr__(settings, "vectora_home", original_home)

    @pytest.mark.asyncio
    async def test_ollama_download_preserves_partial_on_transport_error(
        self, monkeypatch, tmp_path
    ):
        """A transient HTTP stream failure must remain resumable."""
        from backend.api.handlers import provider_routing
        from backend.settings import settings

        original_home = settings.vectora_home
        object.__setattr__(settings, "vectora_home", Path(tmp_path))
        installer = Path(tmp_path) / "tools" / "ollama" / "OllamaSetup.exe"

        class Response:
            status_code = 200
            headers: dict[str, str] = {}

            def raise_for_status(self):
                """Executa a operação raise for status."""
                return

            def json(self):
                """Executa a operação json."""
                return {
                    "tag_name": "v1",
                    "assets": [
                        {
                            "name": "OllamaSetup.exe",
                            "browser_download_url": "https://example.invalid/OllamaSetup.exe",
                            "size": 100,
                            "digest": "0" * 64,
                        }
                    ],
                }

            async def __aenter__(self):
                """Implementa a operação especial __aenter__."""
                return self

            async def __aexit__(self, *_):
                """Implementa a operação especial __aexit__."""
                return False

            async def aiter_bytes(self, _chunk_size):
                """Executa a operação aiter bytes."""
                yield b"partial"
                raise httpx.ReadError("connection dropped")

        class Client:
            async def __aenter__(self):
                """Implementa a operação especial __aenter__."""
                return self

            async def __aexit__(self, *_):
                """Implementa a operação especial __aexit__."""
                return False

            async def get(self, _url):
                """Executa a operação get."""
                return Response()

            def stream(self, _method, _url, headers=None):
                """Executa a operação stream."""
                return Response()

        monkeypatch.setattr(provider_routing.httpx, "AsyncClient", lambda **_: Client())

        with pytest.raises(HTTPException, match="connection dropped"):
            await provider_routing._install_ollama_from_official_release()

        partial = installer.with_suffix(".exe.part")
        assert partial.read_bytes() == b"partial"
        assert Path(f"{partial}.json").exists()
        object.__setattr__(settings, "vectora_home", original_home)

    @pytest.mark.asyncio
    async def test_ollama_download_rejects_resume_when_server_ignores_range(
        self, monkeypatch, tmp_path
    ):
        """A resumed download must never append a full 200 response."""
        from backend.api.handlers import provider_routing
        from backend.settings import settings

        original_home = settings.vectora_home
        object.__setattr__(settings, "vectora_home", Path(tmp_path))
        installer = Path(tmp_path) / "tools" / "ollama" / "OllamaSetup.exe"
        partial = installer.with_suffix(".exe.part")
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"prefix")
        digest = hashlib.sha256(b"prefixfull-response").hexdigest()
        partial.with_suffix(partial.suffix + ".json").write_text(
            json.dumps(
                {
                    "tag": "v1",
                    "url": "https://example.invalid/OllamaSetup.exe",
                    "size": len(b"prefixfull-response"),
                    "digest": digest,
                }
            )
        )

        class Response:
            status_code = 200
            headers: dict[str, str] = {}

            def raise_for_status(self):
                """Executa a operação raise for status."""
                return

            def json(self):
                """Executa a operação json."""
                return {
                    "tag_name": "v1",
                    "assets": [
                        {
                            "name": "OllamaSetup.exe",
                            "browser_download_url": "https://example.invalid/OllamaSetup.exe",
                            "size": len(b"prefixfull-response"),
                            "digest": digest,
                        }
                    ],
                }

            async def __aenter__(self):
                """Implementa a operação especial __aenter__."""
                return self

            async def __aexit__(self, *_):
                """Implementa a operação especial __aexit__."""
                return False

            async def aiter_bytes(self, _chunk_size):
                """Executa a operação aiter bytes."""
                yield b"full-response"

        class Client:
            def __init__(self):
                """Inicializa o estado da instância."""
                self.headers = None

            async def __aenter__(self):
                """Implementa a operação especial __aenter__."""
                return self

            async def __aexit__(self, *_):
                """Implementa a operação especial __aexit__."""
                return False

            async def get(self, _url):
                """Executa a operação get."""
                return Response()

            def stream(self, _method, _url, headers=None):
                """Executa a operação stream."""
                self.headers = headers
                return Response()

        http_client = Client()
        monkeypatch.setattr(
            provider_routing.httpx, "AsyncClient", lambda **_: http_client
        )

        with pytest.raises(HTTPException, match="ignorou Range"):
            await provider_routing._install_ollama_from_official_release()

        assert partial.read_bytes() == b"prefix"
        assert http_client.headers == {
            "Range": "bytes=6-",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
        }
        object.__setattr__(settings, "vectora_home", original_home)

    @pytest.mark.asyncio
    async def test_ollama_download_reads_digest_manifest_when_asset_omits_digest(
        self, monkeypatch, tmp_path
    ):
        """Verifica o comportamento de ollama download reads digest manifest when asset omits digest."""
        from backend.api.handlers import provider_routing
        from backend.settings import settings

        original_home = settings.vectora_home
        object.__setattr__(settings, "vectora_home", Path(tmp_path))
        content = b"installer-bytes"
        digest = hashlib.sha256(content).hexdigest()
        installer_url = "https://example.invalid/OllamaSetup.exe"
        checksum_url = "https://example.invalid/sha256sum.txt"

        class Response:
            status_code = 200
            headers: dict[str, str] = {}

            def __init__(self, payload=None, text=""):
                """Inicializa o estado da instância."""
                self._payload = payload
                self.text = text

            def raise_for_status(self):
                """Executa a operação raise for status."""
                return

            def json(self):
                """Executa a operação json."""
                return self._payload

            async def __aenter__(self):
                """Implementa a operação especial __aenter__."""
                return self

            async def __aexit__(self, *_):
                """Implementa a operação especial __aexit__."""
                return False

            async def aiter_bytes(self, _chunk_size):
                """Executa a operação aiter bytes."""
                yield content

        class Client:
            async def __aenter__(self):
                """Implementa a operação especial __aenter__."""
                return self

            async def __aexit__(self, *_):
                """Implementa a operação especial __aexit__."""
                return False

            async def get(self, url):
                """Executa a operação get."""
                if url.endswith("sha256sum.txt"):
                    return Response(text=f"{digest} *OllamaSetup.exe\\n")
                return Response(
                    payload={
                        "tag_name": "v1",
                        "assets": [
                            {
                                "name": "OllamaSetup.exe",
                                "browser_download_url": installer_url,
                                "size": len(content),
                                "digest": None,
                            },
                            {
                                "name": "sha256sum.txt",
                                "browser_download_url": checksum_url,
                            },
                        ],
                    }
                )

            def stream(self, _method, _url, headers=None):
                """Executa a operação stream."""
                return Response()

        class Process:
            returncode = 0

            async def communicate(self):
                """Executa a operação communicate."""
                return b"", b""

        monkeypatch.setattr(provider_routing.httpx, "AsyncClient", lambda **_: Client())
        monkeypatch.setattr(
            provider_routing.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=Process()),
        )
        monkeypatch.setattr(
            provider_routing,
            "_ollama_executable",
            lambda: Path(tmp_path) / "ollama.exe",
        )

        result = await provider_routing._install_ollama_from_official_release()

        assert result == Path(tmp_path) / "ollama.exe"
        assert not (
            Path(tmp_path) / "tools" / "ollama" / "OllamaSetup.exe.part"
        ).exists()
        object.__setattr__(settings, "vectora_home", original_home)

    def test_host_reachable_returns_models(self, client):
        """Verifica o comportamento de host reachable returns models."""
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

    @pytest.mark.asyncio
    async def test_terminate_ollama_process_bounds_log_task_within_budget(
        self, monkeypatch
    ):
        """Verifica o comportamento de terminate ollama process bounds log task within budget."""
        from backend.api.handlers import provider_routing

        started_event = asyncio.Event()

        # Cria uma task de log que inicia cooperativamente e depois resiste ao cancelamento
        async def slow_logger():
            """Executa a operação slow logger."""
            started_event.set()
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                await asyncio.sleep(100)

        log_task = asyncio.create_task(slow_logger())
        await started_event.wait()
        monkeypatch.setattr(provider_routing, "_ollama_log_task", log_task)

        fake_process = MagicMock()
        fake_process.kill = MagicMock()
        fake_process.wait = AsyncMock(return_value=None)
        # Processo não encerra a tempo (returncode continua None)
        fake_process.returncode = None

        # Define um deadline muito curto no ContextVar (50ms)
        loop = asyncio.get_running_loop()
        start_time = loop.time()
        token = provider_routing._ollama_cleanup_deadline.set(start_time + 0.05)
        try:
            confirmed = await provider_routing._terminate_ollama_process(fake_process)
            elapsed = loop.time() - start_time
            # Não confirmou encerramento porque returncode permaneceu None
            assert confirmed is False
            # process.kill() deve ter sido chamado pontualmente
            assert fake_process.kill.called
            # A duração respeitou o orçamento (não ficou presa na task resistente)
            assert elapsed < 0.5
            # A task de log ainda pendente NÃO foi apagada
            assert provider_routing._ollama_log_task is log_task
            assert log_task.cancelling() or log_task.cancelled()
        finally:
            provider_routing._ollama_cleanup_deadline.reset(token)
            log_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await log_task


class TestOllamaRegisteredModels:
    def test_registered_model_mutation_requires_authentication(
        self, client, monkeypatch
    ) -> None:
        """Verifica o comportamento de registered model mutation requires authentication."""
        monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "true")
        response = client.post(
            "/provider-routing/ollama/registered", json={"tag": "unauthorized"}
        )
        assert response.status_code == 401
        monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "false")

    def test_register_list_and_delete(self, client):
        """Verifica o comportamento de register list and delete."""
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
        """Verifica o comportamento de register empty tag returns 400."""
        resp = client.post("/provider-routing/ollama/registered", json={"tag": "   "})
        assert resp.status_code == 400

    def test_register_duplicate_tag_returns_409(self, client):
        """Verifica o comportamento de register duplicate tag returns 409."""
        client.post("/provider-routing/ollama/registered", json={"tag": "dup-model"})
        resp = client.post(
            "/provider-routing/ollama/registered", json={"tag": "dup-model"}
        )
        assert resp.status_code == 409


class TestLlamaCppAndHuggingFace:
    def test_confirmation_evidence_is_canonical_and_parameter_bound(self):
        """Verifica o comportamento de confirmation evidence is canonical and parameter bound."""
        serialized, evidence = _confirmation_evidence_sha256(
            "a" * 64,
            "0123456789abcdef",
            {"temperature": 0.2, "seed": 7},
        )
        assert serialized == '{"seed":7,"temperature":0.2}'
        assert (
            evidence
            == hashlib.sha256(
                f"{'a' * 64}:0123456789abcdef:{serialized}".encode()
            ).hexdigest()
        )
        _, changed = _confirmation_evidence_sha256(
            "a" * 64,
            "0123456789abcdef",
            {"temperature": 0.3, "seed": 7},
        )
        assert changed != evidence

    def test_llamacpp_status_uses_suggested_endpoint_without_configuring_provider(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifica o comportamento de llamacpp status uses suggested endpoint without configuring provider."""
        from backend.settings import settings

        monkeypatch.setenv("LLAMACPP_MODE", "external")
        object.__setattr__(settings, "llamacpp_base_url", None)
        response = client.get("/provider-routing/llamacpp/status")
        assert response.status_code == 200
        assert response.json()["configured"] is False
        assert response.json()["base_url"] == "http://127.0.0.1:18080/v1"

    def test_llamacpp_connection_test_reports_empty_catalog(self, client):
        """Verifica o comportamento de llamacpp connection test reports empty catalog."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"data": []}
        with patch("httpx.AsyncClient") as mock_httpx:
            context = AsyncMock()
            context.__aenter__ = AsyncMock(return_value=context)
            context.__aexit__ = AsyncMock(return_value=False)
            context.get = AsyncMock(return_value=response)
            mock_httpx.return_value = context
            result = client.post("/provider-routing/llamacpp/test")
        assert result.status_code == 200
        assert result.json() == {"status": "empty", "models": [], "detail": None}

    def test_llamacpp_discovery_uses_openai_models_endpoint(self, client):
        """Verifica o comportamento de llamacpp discovery uses openai models endpoint."""
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
        """Verifica o comportamento de huggingface catalog is shared by runtimes."""
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
            dmr = client.get("/provider-routing/huggingface/models?provider=dmr")

        assert llama.status_code == ollama.status_code == dmr.status_code == 200
        assert llama.json()["models"][0]["provider"] == "llamacpp"
        assert ollama.json()["models"][0]["provider"] == "ollama"
        assert dmr.json()["models"][0]["provider"] == "dmr"
        assert mock_ctx.get.await_count == 3
        assert "filter" not in (mock_ctx.get.await_args_list[2].kwargs["params"])

    def test_huggingface_catalog_rejects_unknown_runtime(self, client):
        """Verifica o comportamento de huggingface catalog rejects unknown runtime."""
        response = client.get("/provider-routing/huggingface/models?provider=unknown")
        assert response.status_code == 400

    def test_huggingface_metadata_rejects_invalid_repo(self, client):
        """Verifica o comportamento de huggingface metadata rejects invalid repo."""
        response = client.get("/provider-routing/huggingface/models/not-a-repo")
        assert response.status_code == 400

    def test_huggingface_metadata_exposes_compatibility_fields(self, client):
        """Verifica o comportamento de huggingface metadata exposes compatibility fields."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "id": "org/model-GGUF",
            "sha": "abc123",
            "library_name": "transformers",
            "cardData": {"license": "apache-2.0", "context_length": 8192},
            "tags": ["gguf", "q4_k_m"],
            "siblings": [],
        }
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            response = client.get("/provider-routing/huggingface/models/org/model-GGUF")

        assert response.status_code == 200
        assert mock_ctx.get.await_args is not None
        assert mock_ctx.get.await_args.kwargs["params"] == {"blobs": "true"}
        assert response.json()["publisher"] == "org"
        assert response.json()["architecture"] == "transformers"
        assert response.json()["quantization"] == "q4_k_m"
        assert response.json()["context_length"] == 8192

    @pytest.mark.asyncio
    async def test_huggingface_file_metadata_requests_revision_and_published_digest(
        self,
    ):
        """Verifica o comportamento de huggingface file metadata requests revision and published digest."""
        from backend.api.handlers.provider_routing import _huggingface_file_metadata

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "siblings": [
                {
                    "rfilename": "weights.gguf",
                    "size": 123,
                    "lfs": {"sha256": "a" * 64, "size": 123},
                }
            ]
        }
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            digest, size = await _huggingface_file_metadata(
                "org/model", "revision-1", "weights.gguf"
            )

        assert digest == "a" * 64
        assert size == 123
        assert mock_ctx.get.await_args is not None
        assert mock_ctx.get.await_args.kwargs["params"] == {
            "revision": "revision-1",
            "blobs": "true",
        }

    def test_local_executor_requires_desktop_bridge_token(self, monkeypatch):
        """Verifica o comportamento de local executor requires desktop bridge token."""
        from fastapi import HTTPException

        from backend.api.handlers.provider_routing import _require_desktop_bridge

        monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "false")
        monkeypatch.setenv("VECTORA_DESKTOP_BRIDGE_TOKEN", "bridge-token")
        with pytest.raises(HTTPException) as error:
            _require_desktop_bridge(None)
        assert error.value.status_code == 403

    def test_huggingface_download_rejects_path_traversal(self, client):
        """Verifica o comportamento de huggingface download rejects path traversal."""
        response = client.post(
            "/provider-routing/huggingface/download",
            json={"repo_id": "org/model", "filename": "../model.gguf"},
        )
        assert response.status_code == 400

    @pytest.mark.parametrize(
        "filename", ["/tmp/model.gguf", "C:/model.gguf", "./model.gguf"]
    )
    def test_huggingface_download_rejects_absolute_or_ambiguous_paths(
        self, client, filename
    ):
        """Verifica o comportamento de huggingface download rejects absolute or ambiguous paths."""
        response = client.post(
            "/provider-routing/huggingface/download",
            json={"repo_id": "org/model", "filename": filename},
        )
        assert response.status_code == 400

    def test_huggingface_download_cancel_rejects_path_traversal(self, client):
        """Verifica o comportamento de huggingface download cancel rejects path traversal."""
        response = client.delete(
            "/provider-routing/huggingface/download",
            params={"repo_id": "org/model", "filename": "../model.gguf"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_huggingface_cancel_waits_for_download_lock_before_removing_partial(
        self, tmp_path, monkeypatch
    ):
        """Verifica o comportamento de huggingface cancel waits for download lock before removing partial."""
        from backend.api.handlers import provider_routing as provider_mod
        from backend.api.handlers.provider_routing import cancel_huggingface_download
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        partial = (
            tmp_path / "models" / "huggingface" / "org" / "model" / "weights.gguf.part"
        )
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"partial")
        key = "org/model/weights.gguf"
        lock = await provider_mod._download_lock(key)
        await lock.acquire()
        event = asyncio.Event()
        provider_mod._download_cancel_events[key] = event
        try:
            result_task = asyncio.create_task(
                cancel_huggingface_download(
                    "org/model", "weights.gguf", None, revision="revision-1"
                )
            )
            await asyncio.sleep(0)
            assert event.is_set()
            assert not result_task.done()
            lock.release()
            result = await result_task
        finally:
            if lock.locked():
                lock.release()
            provider_mod._download_cancel_events.pop(key, None)
        assert result == {"ok": True, "status": "cancelled"}
        assert not partial.exists()

    @pytest.mark.asyncio
    async def test_huggingface_download_rejects_duplicate_active_transfer(
        self, tmp_path, monkeypatch
    ):
        """Verifica o comportamento de huggingface download rejects duplicate active transfer."""
        from fastapi import HTTPException
        from starlette.requests import Request

        from backend.api.handlers import provider_routing as provider_mod
        from backend.api.handlers.provider_routing import (
            HuggingFaceDownloadRequest,
            download_huggingface_model,
        )
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        key = "org/model/weights.gguf"
        provider_mod._download_cancel_events[key] = asyncio.Event()

        class DisconnectedRequest:
            async def is_disconnected(self) -> bool:
                """Executa a operação is disconnected."""
                return False

        try:
            with pytest.raises(HTTPException) as error:
                await download_huggingface_model(
                    HuggingFaceDownloadRequest(
                        repo_id="org/model",
                        revision="main",
                        filename="weights.gguf",
                    ),
                    cast("Request", DisconnectedRequest()),
                    None,
                )
        finally:
            provider_mod._download_cancel_events.pop(key, None)

        assert error.value.status_code == 409

    @pytest.mark.asyncio
    async def test_huggingface_install_writes_manifest_for_selected_files(
        self, tmp_path, monkeypatch
    ):
        """Verifica o comportamento de huggingface install writes manifest for selected files."""
        from backend.api.handlers.provider_routing import (
            HuggingFaceInstallRequest,
            install_huggingface_model,
        )
        from backend.settings import settings

        root = tmp_path / "models" / "huggingface" / "org" / "model"
        root.mkdir(parents=True)
        (root / "model.Q4_K_M.gguf").write_bytes(b"weights")
        (root / "mmproj-f16.gguf").write_bytes(b"projector")
        for name, content in (
            ("model.Q4_K_M.gguf", b"weights"),
            ("mmproj-f16.gguf", b"projector"),
        ):
            (root / f"{name}.source.json").write_text(
                json.dumps(
                    {
                        "repo_id": "org/model",
                        "filename": name,
                        "revision": "abc123",
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size": len(content),
                    }
                ),
                encoding="utf-8",
            )
        monkeypatch.setattr(settings, "vectora_home", tmp_path)

        result = await install_huggingface_model(
            HuggingFaceInstallRequest(
                repo_id="org/model",
                revision="abc123",
                filename="model.Q4_K_M.gguf",
                mmproj_filename="mmproj-f16.gguf",
                alias="local-model",
                parameters={"ctx_size": 4096},
                license="apache-2.0",
                architecture="llama",
                quantization="Q4_K_M",
                context_length=8192,
                compatibility="provável",
            ),
            None,
        )

        assert result["status"] == "installed"
        manifest = result["manifest"]
        assert isinstance(manifest, dict)
        assert manifest["revision"] == "abc123"
        assert manifest["alias"] == "local-model"
        assert manifest["publisher"] == "org"
        assert manifest["license"] == "apache-2.0"
        assert manifest["architecture"] == "llama"
        assert manifest["quantization"] == "Q4_K_M"
        assert manifest["context_length"] == 8192
        assert manifest["compatibility"] == "provável"
        assert [item["role"] for item in manifest["files"]] == [
            "model",
            "mmproj",
        ]
        assert (root / "model-manifest.json").is_file()

    @pytest.mark.asyncio
    async def test_huggingface_ollama_install_requires_alias(self) -> None:
        """Verifica o comportamento de huggingface ollama install requires alias."""
        from fastapi import HTTPException

        from backend.api.handlers.provider_routing import (
            HuggingFaceInstallRequest,
            install_huggingface_model,
        )

        with pytest.raises(HTTPException, match="alias"):
            await install_huggingface_model(
                HuggingFaceInstallRequest(
                    repo_id="org/model",
                    filename="model.gguf",
                    provider="ollama",
                    alias="",
                ),
                None,
            )

    @pytest.mark.asyncio
    async def test_huggingface_ollama_install_requires_runtime_readiness(
        self, tmp_path, monkeypatch
    ) -> None:
        """Verifica o comportamento de huggingface ollama install requires runtime readiness."""
        from backend.api.handlers import provider_routing as provider_mod
        from backend.api.handlers.provider_routing import (
            HuggingFaceInstallRequest,
            install_huggingface_model,
        )
        from backend.settings import settings

        root = tmp_path / "models" / "huggingface" / "org" / "model"
        root.mkdir(parents=True)
        model = root / "model.gguf"
        model.write_bytes(b"weights")
        model.with_name("model.gguf.source.json").write_text(
            json.dumps(
                {
                    "repo_id": "org/model",
                    "filename": "model.gguf",
                    "revision": "abc123",
                    "sha256": hashlib.sha256(b"weights").hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        monkeypatch.setattr(provider_mod, "_ollama_executable", lambda: model)
        monkeypatch.setattr(
            provider_mod, "_ollama_http_status", AsyncMock(return_value=(False, None))
        )

        with pytest.raises(HTTPException, match="readiness"):
            await install_huggingface_model(
                HuggingFaceInstallRequest(
                    repo_id="org/model",
                    revision="abc123",
                    filename="model.gguf",
                    provider="ollama",
                    alias="local-model",
                ),
                None,
            )

        assert not (root / "model-manifest.json").exists()

    @pytest.mark.asyncio
    async def test_huggingface_ollama_failure_does_not_publish_manifest(
        self, tmp_path, monkeypatch
    ) -> None:
        """Verifica o comportamento de huggingface ollama failure does not publish manifest."""
        from fastapi import HTTPException

        from backend.api.handlers import provider_routing as provider_mod
        from backend.api.handlers.provider_routing import (
            HuggingFaceInstallRequest,
            install_huggingface_model,
        )
        from backend.settings import settings

        root = tmp_path / "models" / "huggingface" / "org" / "model"
        root.mkdir(parents=True)
        model = root / "model.gguf"
        model.write_bytes(b"weights")
        model.with_name("model.gguf.source.json").write_text(
            json.dumps(
                {
                    "repo_id": "org/model",
                    "filename": "model.gguf",
                    "revision": "abc123",
                    "sha256": hashlib.sha256(b"weights").hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        monkeypatch.setattr(
            provider_mod, "_ollama_http_status", AsyncMock(return_value=(True, "0.1"))
        )

        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "upstream failure",
            request=httpx.Request("POST", "http://127.0.0.1:11434/api/create"),
            response=httpx.Response(500),
        )
        client_context = AsyncMock()
        client_context.__aenter__ = AsyncMock(return_value=client_context)
        client_context.__aexit__ = AsyncMock(return_value=False)
        client_context.post = AsyncMock(return_value=response)

        with patch("httpx.AsyncClient", return_value=client_context):
            with pytest.raises(HTTPException, match="importar o modelo"):
                await install_huggingface_model(
                    HuggingFaceInstallRequest(
                        repo_id="org/model",
                        revision="abc123",
                        filename="model.gguf",
                        provider="ollama",
                        alias="local-model",
                    ),
                    None,
                )

        assert not (root / "model-manifest.json").exists()

    @pytest.mark.asyncio
    async def test_huggingface_install_rejects_file_without_source_metadata(
        self, tmp_path, monkeypatch
    ):
        """Verifica o comportamento de huggingface install rejects file without source metadata."""
        from fastapi import HTTPException

        from backend.api.handlers.provider_routing import (
            HuggingFaceInstallRequest,
            install_huggingface_model,
        )
        from backend.settings import settings

        root = tmp_path / "models" / "huggingface" / "org" / "model"
        root.mkdir(parents=True)
        (root / "model.gguf").write_bytes(b"weights")
        monkeypatch.setattr(settings, "vectora_home", tmp_path)

        with pytest.raises(HTTPException, match="metadados de origem ausentes"):
            await install_huggingface_model(
                HuggingFaceInstallRequest(
                    repo_id="org/model",
                    revision="abc123",
                    filename="model.gguf",
                ),
                None,
            )

    @pytest.mark.asyncio
    async def test_huggingface_start_uses_active_managed_runtime(
        self, tmp_path, monkeypatch
    ):
        """Verifica o comportamento de huggingface start uses active managed runtime."""
        from backend.api.handlers.provider_routing import (
            HuggingFaceStartRequest,
            start_installed_huggingface_model,
        )
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        model_root = tmp_path / "models" / "huggingface" / "org" / "model"
        model_root.mkdir(parents=True)
        model_path = model_root / "weights.gguf"
        model_path.write_bytes(b"weights")
        runtime_root = tmp_path / "tools" / "llama.cpp"
        runtime_dir = runtime_root / "versions" / "0123456789abcdef"
        runtime_dir.mkdir(parents=True)
        executable = runtime_dir / "llama-server"
        executable.write_bytes(b"server")
        (runtime_root / "active-runtime").write_text(
            "0123456789abcdef", encoding="utf-8"
        )
        (runtime_root / "runtime-manifest.json").write_text(
            json.dumps(
                {
                    "runtimes": [
                        {
                            "id": "0123456789abcdef",
                            "directory": str(runtime_dir),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (model_root / "model-manifest.json").write_text(
            json.dumps({"files": [{"filename": "weights.gguf", "role": "model"}]}),
            encoding="utf-8",
        )
        started: dict[str, object] = {}

        async def fake_start(executable_arg, model_arg, **options):
            """Executa a operação fake start."""
            started.update(
                executable=str(executable_arg),
                model=str(model_arg),
                **options,
            )

        monkeypatch.setattr(
            "backend.services.llamacpp_sidecar.start_llamacpp", fake_start
        )
        result = await start_installed_huggingface_model(
            HuggingFaceStartRequest(repo_id="org/model"), None
        )

        assert result["running"] is True
        assert started == {
            "executable": str(executable),
            "model": str(model_path),
            "host": "127.0.0.1",
            "port": 18080,
            "alias": None,
            "mmproj": None,
            "ctx_size": 32768,
            "n_gpu_layers": None,
            "threads": None,
            "parallel": None,
            "jinja": False,
        }
        assert result["confirmed"] is True
        assert result["model_tag"] == "weights.gguf"
        assert os.environ["LLAMACPP_MODE"] == "managed"
        assert "LLAMACPP_MODE=managed" in (tmp_path / ".env").read_text()

    @pytest.mark.asyncio
    async def test_huggingface_start_restores_configuration_when_confirmation_fails(
        self, app, tmp_path, monkeypatch
    ):
        """A falha de confirmação não deixa um endpoint falso como gerenciado."""
        from backend.api.handlers.provider_routing import (
            HuggingFaceStartRequest,
            start_installed_huggingface_model,
        )
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        model_root = tmp_path / "models" / "huggingface" / "org" / "rollback-model"
        model_root.mkdir(parents=True)
        model_path = model_root / "weights.gguf"
        model_path.write_bytes(b"weights")
        runtime_dir = tmp_path / "tools" / "llama.cpp" / "versions" / "runtime-1"
        runtime_dir.mkdir(parents=True)
        executable = runtime_dir / "llama-server"
        executable.write_bytes(b"server")
        runtime_root = runtime_dir.parents[1]
        (runtime_root / "active-runtime").write_text("runtime-1", encoding="utf-8")
        (runtime_root / "runtime-manifest.json").write_text(
            json.dumps(
                {"runtimes": [{"id": "runtime-1", "directory": str(runtime_dir)}]}
            ),
            encoding="utf-8",
        )
        (model_root / "model-manifest.json").write_text(
            json.dumps({"files": [{"filename": "weights.gguf", "role": "model"}]}),
            encoding="utf-8",
        )
        env_file = tmp_path / ".env"
        env_file.write_text("LLAMACPP_BASE_URL=http://old.local/v1\n", encoding="utf-8")
        monkeypatch.setenv("LLAMACPP_BASE_URL", "http://old.local/v1")
        monkeypatch.setenv("LLAMACPP_MODE", "external")

        async def fake_start(*args, **kwargs):
            return None

        async def fail_confirmation(*args, **kwargs):
            raise RuntimeError("probe failed")

        stop = AsyncMock()
        monkeypatch.setattr(
            "backend.services.llamacpp_sidecar.start_llamacpp", fake_start
        )
        monkeypatch.setattr("backend.services.llamacpp_sidecar.stop_llamacpp", stop)
        monkeypatch.setattr(
            "backend.api.handlers.provider_routing.confirm_llamacpp_model",
            fail_confirmation,
        )

        with pytest.raises(HTTPException, match="não foi possível confirmar"):
            await start_installed_huggingface_model(
                HuggingFaceStartRequest(repo_id="org/rollback-model"), None
            )

        assert stop.await_count == 1
        assert os.environ["LLAMACPP_BASE_URL"] == "http://old.local/v1"
        assert os.environ["LLAMACPP_MODE"] == "external"
        assert (
            env_file.read_text(encoding="utf-8")
            == "LLAMACPP_BASE_URL=http://old.local/v1\n"
        )

    @pytest.mark.asyncio
    async def test_huggingface_start_removes_new_env_when_confirmation_fails(
        self, app, tmp_path, monkeypatch
    ):
        """Falha sem .env prévio não deixa um arquivo vazio persistente."""
        from backend.api.handlers.provider_routing import (
            HuggingFaceStartRequest,
            start_installed_huggingface_model,
        )
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        model_root = tmp_path / "models" / "huggingface" / "org" / "fresh-model"
        model_root.mkdir(parents=True)
        (model_root / "weights.gguf").write_bytes(b"weights")
        runtime_dir = tmp_path / "tools" / "llama.cpp" / "versions" / "runtime-1"
        runtime_dir.mkdir(parents=True)
        (runtime_dir / "llama-server").write_bytes(b"server")
        runtime_root = runtime_dir.parents[1]
        (runtime_root / "active-runtime").write_text("runtime-1", encoding="utf-8")
        (runtime_root / "runtime-manifest.json").write_text(
            json.dumps(
                {"runtimes": [{"id": "runtime-1", "directory": str(runtime_dir)}]}
            ),
            encoding="utf-8",
        )
        (model_root / "model-manifest.json").write_text(
            json.dumps({"files": [{"filename": "weights.gguf", "role": "model"}]}),
            encoding="utf-8",
        )

        async def fake_start(*args, **kwargs):
            return None

        async def fail_confirmation(*args, **kwargs):
            raise RuntimeError("probe failed")

        stop = AsyncMock()
        monkeypatch.setattr(
            "backend.services.llamacpp_sidecar.start_llamacpp", fake_start
        )
        monkeypatch.setattr("backend.services.llamacpp_sidecar.stop_llamacpp", stop)
        monkeypatch.setattr(
            "backend.api.handlers.provider_routing.confirm_llamacpp_model",
            fail_confirmation,
        )

        with pytest.raises(HTTPException, match="não foi possível confirmar"):
            await start_installed_huggingface_model(
                HuggingFaceStartRequest(repo_id="org/fresh-model"), None
            )

        assert stop.await_count == 1
        assert not (tmp_path / ".env").exists()

    def test_llamacpp_install_rejects_non_official_url(self, client):
        """Verifica o comportamento de llamacpp install rejects non official url."""
        response = client.post(
            "/provider-routing/llamacpp/install",
            json={"asset_url": "https://example.com/llama.zip"},
        )
        assert response.status_code == 400

    def test_llamacpp_install_rejects_invalid_checksum(self, client):
        """Verifica o comportamento de llamacpp install rejects invalid checksum."""
        response = client.post(
            "/provider-routing/llamacpp/install",
            json={
                "asset_url": "https://github.com/ggml-org/llama.cpp/releases/download/b1/llama.zip",
                "sha256": "bad",
            },
        )
        assert response.status_code == 400

    def test_llamacpp_runtime_test_rejects_unknown_binary(self, client):
        """Verifica o comportamento de llamacpp runtime test rejects unknown binary."""
        response = client.post(
            "/provider-routing/llamacpp/runtime/test",
            json={"path": "C:/tmp/not-llama.exe"},
        )
        assert response.status_code == 400

    def test_llamacpp_model_confirmation_rejects_unmanaged_path(self, client):
        """Verifica o comportamento de llamacpp model confirmation rejects unmanaged path."""
        response = client.post(
            "/provider-routing/llamacpp/models/confirm",
            json={
                "tag": "local-model",
                "model_path": "C:/outside/model.gguf",
                "runtime_id": "0123456789abcdef",
            },
        )
        assert response.status_code == 400

    def test_llamacpp_archive_extraction_rejects_traversal(self, tmp_path):
        """Verifica o comportamento de llamacpp archive extraction rejects traversal."""
        archive = tmp_path / "runtime.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("../escape.exe", b"bad")
        with pytest.raises(ValueError):
            _extract_llamacpp_archive(archive, tmp_path / "runtime")

    def test_llamacpp_archive_extraction_returns_files(self, tmp_path):
        """Verifica o comportamento de llamacpp archive extraction returns files."""
        archive = tmp_path / "runtime.zip"
        destination = tmp_path / "runtime"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("bin/llama-server.exe", b"binary")
        files = _extract_llamacpp_archive(archive, destination)
        assert (destination / "bin" / "llama-server.exe").read_bytes() == b"binary"
        assert files == [str(destination / "bin" / "llama-server.exe")]


class TestOpenRouterKey:
    def test_status_not_configured_by_default(self, client, clean_openrouter_key):
        """Verifica o comportamento de status not configured by default."""
        os.environ.pop("OPENROUTER_API_KEY", None)
        resp = client.get("/provider-routing/openrouter/status")
        assert resp.status_code == 200
        assert resp.json() == {"configured": False, "masked": ""}

    def test_set_key_valid_persists_and_masks(
        self, client, clean_openrouter_key, tmp_path
    ):
        """Verifica o comportamento de set key valid persists and masks."""
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
        """Verifica o comportamento de set key rejected by openrouter returns 400."""
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
        """Verifica o comportamento de set key empty returns 400 without network call."""
        with patch("httpx.AsyncClient") as mock_httpx:
            resp = client.post(
                "/provider-routing/openrouter/key", json={"api_key": "   "}
            )
        assert resp.status_code == 400
        mock_httpx.assert_not_called()

    def test_clear_key_removes_env(self, client, clean_openrouter_key, tmp_path):
        """Verifica o comportamento de clear key removes env."""
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
        """Executa a operação  reset cache."""
        from backend.api.handlers.provider_routing import _catalog_cache

        _catalog_cache["fetched_at"] = float("-inf")
        _catalog_cache["models"] = []

    @pytest.fixture(autouse=True)
    def _isolated_cache(self):
        # Reseta antes E depois — o fixture `client` deste arquivo é
        # module-scoped (mesmo TestClient/app pra todos os testes), então
        # qualquer estado deixado em `_catalog_cache` por este teste não
        # pode vazar pro próximo, seja qual for a ordem de execução.
        """Executa a operação  isolated cache."""
        self._reset_cache()
        yield
        self._reset_cache()

    @contextmanager
    def _mocked_http_client(
        self, app, handler: Callable[[httpx.Request], httpx.Response]
    ) -> Generator[None]:
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
            """Executa a operação  fake client."""
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
        """Verifica o comportamento de catalog returns models."""

        def handler(request: httpx.Request) -> httpx.Response:
            """Executa a operação handler."""
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
        """Verifica o comportamento de catalog filters by q."""

        def handler(request: httpx.Request) -> httpx.Response:
            """Executa a operação handler."""
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
            """Executa a operação handler."""
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
            """Executa a operação handler."""
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
        """Verifica o comportamento de register list and delete."""
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
        """Verifica o comportamento de register empty tag returns 400."""
        resp = client.post(
            "/provider-routing/openrouter/registered", json={"tag": "   "}
        )
        assert resp.status_code == 400

    def test_register_duplicate_tag_returns_409(self, client):
        """Verifica o comportamento de register duplicate tag returns 409."""
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
        """Executa a operação  reset cache."""
        from backend.api.handlers import provider_routing as pr

        pr._catalog_cache["fetched_at"] = float("-inf")
        pr._catalog_cache["models"] = []

    @pytest.mark.asyncio
    async def test_rede_ok_devolve_o_catalogo_real(self):
        """Verifica o comportamento de rede ok devolve o catalogo real."""
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
        """Verifica o comportamento de busca filtra tambem no fallback."""
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
        """Executa a operação  reset cache."""
        from backend.api.handlers import provider_routing as pr

        pr._catalog_cache["fetched_at"] = float("-inf")
        pr._catalog_cache["models"] = []

    @pytest.mark.asyncio
    async def test_modelo_com_image_no_catalogo_retorna_true(self):
        """Verifica o comportamento de modelo com image no catalogo retorna true."""
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
        """Verifica o comportamento de modelo com modalidade image com espacos retorna true."""
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
        """Verifica o comportamento de cache vazio tenta popular antes de checar."""
        from backend.api.handlers import provider_routing as pr
        from backend.api.handlers.provider_routing import (
            openrouter_model_supports_image,
        )

        self._reset_cache()

        async def _fake_ensure(client):
            """Executa a operação  fake ensure."""
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
        """Verifica o comportamento de status not configured by default."""
        resp = client.get("/provider-routing/nine-router/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is False
        assert body["masked"] == ""

    def test_set_config_persists_both_fields(
        self, client, clean_nine_router_config, tmp_path
    ):
        """Verifica o comportamento de set config persists both fields."""
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
        """Verifica o comportamento de set config empty base url returns 400."""
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
        """Verifica o comportamento de set config empty api key returns 400."""
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
        """Verifica o comportamento de clear config removes both."""
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
        """Verifica o comportamento de not configured returns reachable false not 500."""
        resp = client.get("/provider-routing/nine-router/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["reachable"] is False
        assert body["models"] == []

    def test_host_unreachable_returns_reachable_false_not_500(
        self, client, clean_nine_router_config, tmp_path
    ):
        """Verifica o comportamento de host unreachable returns reachable false not 500."""
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
        """Verifica o comportamento de host reachable returns models."""
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
        """Verifica o comportamento de register list and delete."""
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
        """Verifica o comportamento de register empty tag returns 400."""
        resp = client.post(
            "/provider-routing/nine-router/registered", json={"tag": "   "}
        )
        assert resp.status_code == 400

    def test_register_duplicate_tag_returns_409(self, client):
        """Verifica o comportamento de register duplicate tag returns 409."""
        client.post(
            "/provider-routing/nine-router/registered", json={"tag": "dup/nine-model"}
        )
        resp = client.post(
            "/provider-routing/nine-router/registered", json={"tag": "dup/nine-model"}
        )
        assert resp.status_code == 409


class TestLlamaCppConfiguration:
    def test_managed_mode_requires_ready_sidecar(self, client, monkeypatch):
        """Verifica o comportamento de managed mode requires ready sidecar."""
        monkeypatch.delenv("LLAMACPP_MODE", raising=False)
        monkeypatch.setattr(
            "backend.services.llamacpp_sidecar.llamacpp_status",
            lambda: {"running": False},
        )
        response = client.post(
            "/provider-routing/llamacpp/mode", json={"mode": "managed"}
        )
        assert response.status_code == 409

    def test_external_mode_is_persisted_without_copying_credentials(
        self, client, clean_llamacpp_config, tmp_path, monkeypatch
    ):
        """Verifica o comportamento de external mode is persisted without copying credentials."""
        monkeypatch.setattr(
            "backend.api.handlers.provider_routing._env_file",
            lambda: tmp_path / ".env",
        )
        response = client.post(
            "/provider-routing/llamacpp/mode", json={"mode": "external"}
        )
        assert response.status_code == 200
        assert response.json()["mode"] == "external"
        assert "LLAMACPP_MODE=external" in (tmp_path / ".env").read_text()

    def test_config_syncs_model_environment(
        self, client, clean_llamacpp_config, tmp_path, monkeypatch
    ) -> None:
        """Verifica o comportamento de config syncs model environment."""
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        response = client.post(
            "/provider-routing/llamacpp/config",
            json={
                "base_url": "http://127.0.0.1:8080/v1",
                "api_key": "",
                "model": "  local-model  ",
            },
        )
        assert response.status_code == 200
        assert os.environ["LLAMACPP_MODEL"] == "local-model"

        cleared = client.post(
            "/provider-routing/llamacpp/config",
            json={
                "base_url": "http://127.0.0.1:8080/v1",
                "api_key": "",
                "model": "",
            },
        )
        assert cleared.status_code == 200
        assert "LLAMACPP_MODEL" not in os.environ

    @pytest.mark.asyncio
    async def test_runtime_status_reads_installed_manifest(self, tmp_path, monkeypatch):
        """Verifica o comportamento de runtime status reads installed manifest."""
        from backend.settings import settings

        # Keep this assertion independent from provider-configuration tests
        # that run before it and may leave an external URL in the shared
        # settings singleton.  An installed manifest without an active
        # process is a managed runtime that is currently unavailable.
        monkeypatch.setattr(settings, "llamacpp_base_url", None)
        monkeypatch.setenv("LLAMACPP_MODE", "managed")
        monkeypatch.setattr(
            "backend.services.llamacpp_sidecar.llamacpp_status",
            lambda: {"running": False, "persisted_pid": None, "stale_state": False},
        )
        root = tmp_path / "tools" / "llama.cpp"
        root.mkdir(parents=True)
        (root / "runtime-manifest.json").write_text(
            '{"runtimes": [{"asset": "llama-server.zip", "sha256": "abc"}]}',
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "vectora_home", tmp_path)

        status = await llamacpp_runtime_status()

        assert status["installed"] is True
        assert status["runtimes"] == [{"asset": "llama-server.zip", "sha256": "abc"}]
        assert status["state"] == "unavailable"
        assert status["managed"] is True

    @pytest.mark.asyncio
    async def test_runtime_status_distinguishes_external_mode_without_managed_files(
        self, tmp_path, monkeypatch
    ):
        """Verifica o comportamento de runtime status distinguishes external mode without managed files."""
        from backend.api.handlers.provider_routing import llamacpp_runtime_status
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        monkeypatch.setattr(settings, "llamacpp_base_url", "http://127.0.0.1:8080/v1")
        monkeypatch.setenv("LLAMACPP_MODE", "external")
        monkeypatch.setattr(
            "backend.services.llamacpp_sidecar.llamacpp_status",
            lambda: {"running": False, "persisted_pid": None, "stale_state": False},
        )

        status = await llamacpp_runtime_status()

        assert status["state"] == "external"
        assert status["managed"] is False
        assert status["external"] is True

    @pytest.mark.asyncio
    async def test_runtime_status_marks_persisted_unknown_process(
        self, tmp_path, monkeypatch
    ):
        """Verifica o comportamento de runtime status marks persisted unknown process."""
        from backend.api.handlers.provider_routing import llamacpp_runtime_status
        from backend.settings import settings

        root = tmp_path / "tools" / "llama.cpp"
        root.mkdir(parents=True)
        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        monkeypatch.setattr(
            "backend.services.llamacpp_sidecar.llamacpp_status",
            lambda: {
                "running": False,
                "persisted_pid": 9876,
                "stale_state": False,
            },
        )

        status = await llamacpp_runtime_status()

        assert status["state"] == "unknown-process"

    @pytest.mark.asyncio
    async def test_runtime_removal_rejects_active_sidecar(self, monkeypatch):
        """Verifica o comportamento de runtime removal rejects active sidecar."""
        monkeypatch.setattr(
            "backend.services.llamacpp_sidecar.llamacpp_status",
            lambda: {"running": True, "pid": 123},
        )

        with pytest.raises(Exception, match="pare o sidecar"):
            await remove_llamacpp_runtime(None)

    @pytest.mark.asyncio
    async def test_runtime_rollback_activates_registered_version(
        self, tmp_path, monkeypatch
    ):
        """Verifica o comportamento de runtime rollback activates registered version."""
        from backend.api.handlers.provider_routing import rollback_llamacpp_runtime
        from backend.settings import settings

        root = tmp_path / "tools" / "llama.cpp"
        version = root / "versions" / "0123456789abcdef"
        version.mkdir(parents=True)
        (root / "runtime-manifest.json").write_text(
            '{"runtimes": [{"id": "0123456789abcdef", "directory": "ignored"}]}',
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        result = await rollback_llamacpp_runtime(
            LlamaCppRollbackRequest(runtime_id="0123456789abcdef"), None
        )
        assert result == {"ok": True, "active_runtime": "0123456789abcdef"}
        assert (root / "active-runtime").read_text(
            encoding="utf-8"
        ) == "0123456789abcdef"

    @pytest.mark.asyncio
    async def test_runtime_version_removal_keeps_active_version(
        self, tmp_path, monkeypatch
    ):
        """Verifica o comportamento de runtime version removal keeps active version."""
        from backend.api.handlers.provider_routing import (
            remove_llamacpp_runtime_version,
        )
        from backend.settings import settings

        root = tmp_path / "tools" / "llama.cpp"
        version = root / "versions" / "fedcba9876543210"
        version.mkdir(parents=True)
        (version / "llama-server").write_bytes(b"runtime")
        (root / "active-runtime").write_text("0123456789abcdef", encoding="utf-8")
        (root / "runtime-manifest.json").write_text(
            '{"runtimes": [{"id": "0123456789abcdef"}, {"id": "fedcba9876543210"}]}',
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        result = await remove_llamacpp_runtime_version("fedcba9876543210", None)
        assert result == {"ok": True}
        assert not version.exists()

    @pytest.mark.asyncio
    async def test_runtime_cleanup_retains_active_and_newest_inactive_versions(
        self, tmp_path, monkeypatch
    ):
        """Verifica o comportamento de runtime cleanup retains active and newest inactive versions."""
        from backend.api.handlers.provider_routing import (
            LlamaCppRetentionRequest,
            cleanup_llamacpp_runtime_versions,
        )
        from backend.settings import settings

        root = tmp_path / "tools" / "llama.cpp"
        versions = root / "versions"
        versions.mkdir(parents=True)
        active = "0123456789abcdef"
        newest = "fedcba9876543210"
        oldest = "0011223344556677"
        (root / "active-runtime").write_text(active, encoding="utf-8")
        for runtime_id in (active, newest, oldest):
            (versions / runtime_id).mkdir()
        (root / "runtime-manifest.json").write_text(
            json.dumps(
                {
                    "runtimes": [
                        {"id": active, "installed_at": "2026-01-01T00:00:00+00:00"},
                        {"id": newest, "installed_at": "2026-02-01T00:00:00+00:00"},
                        {"id": oldest, "installed_at": "2026-01-01T00:00:00+00:00"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        monkeypatch.setattr(
            "backend.services.llamacpp_sidecar.llamacpp_status",
            lambda: {"running": False},
        )

        result = await cleanup_llamacpp_runtime_versions(
            LlamaCppRetentionRequest(keep=1), None
        )

        assert result["removed"] == [oldest]
        assert (versions / active).exists()
        assert (versions / newest).exists()
        assert not (versions / oldest).exists()

    @pytest.mark.asyncio
    async def test_runtime_transitions_are_serialized(self):
        """Verifica o comportamento de runtime transitions are serialized."""
        from backend.api.handlers.provider_routing import _runtime_transition

        events: list[str] = []

        @_runtime_transition
        async def transition(name: str) -> None:
            """Executa a operação transition."""
            events.append(f"start:{name}")
            await asyncio.sleep(0)
            events.append(f"end:{name}")

        await asyncio.gather(transition("first"), transition("second"))

        assert events in (
            ["start:first", "end:first", "start:second", "end:second"],
            ["start:second", "end:second", "start:first", "end:first"],
        )

    @pytest.mark.asyncio
    async def test_runtime_validation_requires_llama_server(self):
        """Verifica o comportamento de runtime validation requires llama server."""
        from backend.api.handlers.provider_routing import _validate_runtime_executable

        with pytest.raises(ValueError, match="não contém llama-server"):
            await _validate_runtime_executable(["/tmp/other-binary"])
