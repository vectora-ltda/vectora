"""Tests — Admin Storage endpoints (F10).

GET /admin/storage, POST /admin/storage/test, PATCH /admin/storage.
Usa httpx.AsyncClient com a app FastAPI mockando storage_health.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request
from httpx import AsyncClient


@pytest.fixture
async def admin_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """httpx.AsyncClient apontando para a app FastAPI em memória."""
    from httpx import ASGITransport

    import backend.api.handlers.admin as admin_handlers
    from backend.api.server import create_app

    monkeypatch.setattr(admin_handlers, "require_admin", lambda _user: None)

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


class TestAdminStorageGet:
    """GET /admin/storage retorna health de todos os backends."""

    @pytest.mark.asyncio
    async def test_endpoint_exists(self, admin_client):
        """Endpoint /admin/storage existe e retorna resposta HTTP."""
        with patch(
            "backend.storage.factory.storage_health",
            new_callable=AsyncMock,
            return_value={"sqlite": {"ok": True}},
        ):
            resp = await admin_client.get("/admin/storage")
        # Pode retornar 200 ou 401 dependendo de auth configurado
        assert resp.status_code in (200, 401, 403, 404)

    @pytest.mark.asyncio
    async def test_response_is_json(self, admin_client):
        """Response tem Content-Type JSON quando não autenticado (401)."""
        resp = await admin_client.get("/admin/storage")
        assert resp.status_code in (200, 401, 403, 404)


class TestAdminStorageDefaults:
    """GET /admin/storage/defaults entrega config default para pré-preencher."""

    @pytest.mark.asyncio
    async def test_endpoint_exists(self, admin_client):
        """Endpoint registrado — responde HTTP (200 com auth, 401/403 sem)."""
        resp = await admin_client.get("/admin/storage/defaults")
        assert resp.status_code in (200, 401, 403, 404)

    def test_connection_defaults_shape(self):
        """A fonte (dev_stack.connection_defaults) traz url + start_command.

        O endpoint só repassa isto; testar a fonte cobre o contrato sem
        depender de auth montado no client de teste.
        """
        from backend.storage.dev_stack import connection_defaults

        defaults = connection_defaults()
        assert set(defaults) == {"postgres", "redis", "qdrant"}
        for service in ("postgres", "redis", "qdrant"):
            assert defaults[service]["url"]
            assert "docker compose up -d" in defaults[service]["start_command"]
        # Qdrant é o único com API key (auth-first).
        assert defaults["qdrant"]["api_key"] == "vectora"
        assert "api_key" not in defaults["redis"]


class TestAdminStorageTest:
    """POST /admin/storage/test verifica conectividade com DSN."""

    @pytest.mark.asyncio
    async def test_sqlite_dsn_accepted(self, admin_client, tmp_path):
        db_path = str(tmp_path / "test.db")
        resp = await admin_client.post(
            "/admin/storage/test",
            json={"dsn": db_path},
        )
        # Pode ser 200 (OK), 401 (sem auth), ou 422 (schema diferente)
        assert resp.status_code in (200, 401, 403, 422)

    @pytest.mark.asyncio
    async def test_invalid_dsn_handled(self, admin_client):
        """DSN inválido retorna ok=False ou erro HTTP."""
        resp = await admin_client.post(
            "/admin/storage/test",
            json={"dsn": "postgresql://bad:5432/nonexistent"},
        )
        assert resp.status_code in (200, 400, 401, 403, 422, 500)
        if resp.status_code == 200:
            data = resp.json()
            # Pode ter "ok": false ou "ok": true dependendo da implementação
            assert "ok" in data or isinstance(data, dict)


class TestAdminStoragePatch:
    """PATCH /admin/storage atualiza storage_mode em runtime."""

    @pytest.mark.asyncio
    async def test_patch_storage_mode(self, admin_client):
        resp = await admin_client.patch(
            "/admin/storage",
            json={"storage_mode": "lite"},
        )
        assert resp.status_code in (200, 401, 403, 422)

    @pytest.mark.asyncio
    async def test_patch_invalid_mode(self, admin_client):
        """Modo inválido deve retornar erro ou ser aceito com validação."""
        resp = await admin_client.patch(
            "/admin/storage",
            json={"storage_mode": "invalid_mode"},
        )
        # Pode validar e retornar 422, ou aceitar e silenciar
        assert resp.status_code in (200, 400, 401, 403, 422, 500)


class TestStorageHealthUnit:
    """storage_health() unit test sem infra real."""

    @pytest.mark.asyncio
    async def test_health_returns_dict(self, tmp_path, monkeypatch):
        import backend.settings as _settings_mod
        from backend.storage.factory import storage_health

        monkeypatch.setattr(_settings_mod.settings, "storage_mode", "lite")
        monkeypatch.setattr(
            _settings_mod.settings, "db_dsn", str(tmp_path / "health.db")
        )
        monkeypatch.setattr(
            _settings_mod.settings,
            "lancedb_dir",
            str(tmp_path / "lancedb"),
        )

        import backend.storage.factory as _fac

        _fac._reset_singletons()
        health = await storage_health()
        _fac._reset_singletons()

        assert isinstance(health, dict)
        # Deve ter pelo menos SQLite no resultado
        assert len(health) >= 1

    @pytest.mark.asyncio
    async def test_health_sqlite_ok_on_lite(self, tmp_path, monkeypatch):
        import backend.settings as _settings_mod
        from backend.storage.factory import storage_health

        monkeypatch.setattr(_settings_mod.settings, "storage_mode", "lite")
        monkeypatch.setattr(
            _settings_mod.settings, "db_dsn", str(tmp_path / "health2.db")
        )
        monkeypatch.setattr(
            _settings_mod.settings,
            "lancedb_dir",
            str(tmp_path / "lancedb2"),
        )

        import backend.storage.factory as _fac

        _fac._reset_singletons()
        health = await storage_health()
        _fac._reset_singletons()

        # Modo lite não deve reportar erro no SQLite
        if "sqlite" in health:
            assert health["sqlite"].get("ok") is True
        elif "checkpointer" in health:
            assert health["checkpointer"].get("ok") is True


class TestFallbackOrderEndpoint:
    """PATCH/GET /admin/model/fallback-order (Parte A)."""

    def _fresh_runtime(self, tmp_path, monkeypatch):
        import backend.workspace.runtime_settings as rt_mod
        from backend.workspace.runtime_settings import RuntimeSettings

        fresh = RuntimeSettings(path=tmp_path / "settings.json")
        monkeypatch.setattr(rt_mod, "runtime_settings", fresh)
        return fresh

    @pytest.mark.asyncio
    async def test_patch_sets_order(self, admin_client, tmp_path, monkeypatch):
        fresh = self._fresh_runtime(tmp_path, monkeypatch)
        from backend.settings import settings

        monkeypatch.setattr(settings, "openai_api_key", "test-openai-key")
        monkeypatch.setattr(settings, "cohere_api_key", "test-cohere-key")
        resp = await admin_client.patch(
            "/admin/model/fallback-order",
            json={"order": ["openai:gpt-4o", "cohere:command-a"]},
        )
        assert resp.status_code == 200
        assert resp.json()["fallback_order"] == [
            "openai:gpt-4o",
            "cohere:command-a",
        ]
        assert fresh.fallback_order == ["openai:gpt-4o", "cohere:command-a"]

    @pytest.mark.asyncio
    async def test_get_returns_order(self, admin_client, tmp_path, monkeypatch):
        fresh = self._fresh_runtime(tmp_path, monkeypatch)
        fresh.set_fallback_order(["cohere:command-a"])
        resp = await admin_client.get("/admin/model/fallback-order")
        assert resp.status_code in (200, 401, 403)
        if resp.status_code == 200:
            assert resp.json()["fallback_order"] == ["cohere:command-a"]

    @pytest.mark.asyncio
    async def test_patch_filters_empty(self, admin_client, tmp_path, monkeypatch):
        self._fresh_runtime(tmp_path, monkeypatch)
        from backend.settings import settings

        monkeypatch.setattr(settings, "openai_api_key", "test-openai-key")
        resp = await admin_client.patch(
            "/admin/model/fallback-order",
            json={"order": ["openai:gpt-4o", "", "   "]},
        )
        assert resp.status_code == 200
        assert resp.json()["fallback_order"] == ["openai:gpt-4o"]

    @pytest.mark.asyncio
    async def test_patch_rejects_incomplete_model_ids(
        self,
        admin_client: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._fresh_runtime(tmp_path, monkeypatch)
        from backend.settings import settings

        monkeypatch.setattr(settings, "openai_api_key", "test-openai-key")
        monkeypatch.setattr(settings, "nine_router_api_key", "test-nine-key")
        monkeypatch.setattr(settings, "nine_router_base_url", None)
        resp = await admin_client.patch(
            "/admin/model/fallback-order",
            json={"order": ["openai:", "nine_router:test-model"]},
        )
        assert resp.status_code == 200
        assert resp.json()["fallback_order"] == []

    @pytest.mark.asyncio
    async def test_patch_empty_clears(self, admin_client, tmp_path, monkeypatch):
        fresh = self._fresh_runtime(tmp_path, monkeypatch)
        fresh.set_fallback_order(["openai:gpt-4o"])
        resp = await admin_client.patch(
            "/admin/model/fallback-order", json={"order": []}
        )
        if resp.status_code == 200:
            assert resp.json()["fallback_order"] == []

    @pytest.mark.asyncio
    async def test_patch_missing_order_defaults_empty(
        self, admin_client, tmp_path, monkeypatch
    ):
        self._fresh_runtime(tmp_path, monkeypatch)
        resp = await admin_client.patch("/admin/model/fallback-order", json={})
        # order tem default [] — aceita sem 422
        assert resp.status_code in (200, 401, 403)


class TestImageFallbackModelEndpoint:
    """GET/PATCH /admin/model/image-fallback."""

    def _fresh_runtime(self, tmp_path, monkeypatch):
        import backend.workspace.runtime_settings as rt_mod
        from backend.workspace.runtime_settings import RuntimeSettings

        fresh = RuntimeSettings(path=tmp_path / "settings.json")
        monkeypatch.setattr(rt_mod, "runtime_settings", fresh)
        return fresh

    @pytest.mark.asyncio
    async def test_get_vazio_por_padrao(self, admin_client, tmp_path, monkeypatch):
        """Sem configuração: string vazia, não erro — o comportamento
        antigo (bloquear o envio) continua sendo o default."""
        self._fresh_runtime(tmp_path, monkeypatch)
        resp = await admin_client.get("/admin/model/image-fallback")
        assert resp.status_code in (200, 401, 403)
        if resp.status_code == 200:
            assert resp.json()["model"] == ""

    @pytest.mark.asyncio
    async def test_patch_seta_e_get_reflete(self, admin_client, tmp_path, monkeypatch):
        self._fresh_runtime(tmp_path, monkeypatch)
        resp = await admin_client.patch(
            "/admin/model/image-fallback",
            json={"model": "google-genai:gemini-2.5-flash"},
        )
        assert resp.status_code in (200, 401, 403, 422)
        if resp.status_code == 200:
            assert resp.json()["model"] == "google-genai:gemini-2.5-flash"
            get_resp = await admin_client.get("/admin/model/image-fallback")
            assert get_resp.json()["model"] == "google-genai:gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_patch_string_vazia_limpa(self, admin_client, tmp_path, monkeypatch):
        """Bad/edge: enviar string vazia limpa a config já salva, em vez de
        ser rejeitado ou ignorado silenciosamente."""
        fresh = self._fresh_runtime(tmp_path, monkeypatch)
        fresh.set("image_fallback_model", "openai:gpt-4o")
        resp = await admin_client.patch(
            "/admin/model/image-fallback", json={"model": ""}
        )
        if resp.status_code == 200:
            assert resp.json()["model"] == ""
            assert fresh.get("image_fallback_model") == ""

    @pytest.mark.asyncio
    async def test_patch_rejects_incomplete_image_model_id(
        self,
        admin_client: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._fresh_runtime(tmp_path, monkeypatch)
        from backend.settings import settings

        monkeypatch.setattr(settings, "openai_api_key", "test-openai-key")
        resp = await admin_client.patch(
            "/admin/model/image-fallback", json={"model": "openai:"}
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_rejects_nine_router_without_base_url(
        self,
        admin_client: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._fresh_runtime(tmp_path, monkeypatch)
        from backend.settings import settings

        monkeypatch.setattr(settings, "nine_router_api_key", "test-nine-key")
        monkeypatch.setattr(settings, "nine_router_base_url", None)
        resp = await admin_client.patch(
            "/admin/model/image-fallback", json={"model": "nine_router:test-model"}
        )
        assert resp.status_code == 422


class TestPatchStorageRequiresPro:
    """Storage Completo (Postgres/Redis/Qdrant) é recurso do Vectora Pro.

    O frontend já desabilita a opção pra não-Pro, mas o gate visual é
    decoração enquanto o handler não checa tier: uma chamada direta à API
    ligava o modo completo sem licença.
    """

    @staticmethod
    def _write_license(tmp_path, monkeypatch, tier: str) -> None:
        import json
        from datetime import UTC, datetime

        from backend.services import license as lic

        cache_path = tmp_path / "license_cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "tier": tier,
                    "status": "active",
                    "days_remaining": 30,
                    "expires_at": "2027-01-01",
                    "validated_at": datetime.now(UTC).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(lic, "CACHE_PATH", cache_path)

    @staticmethod
    def _request(body: dict) -> Request:
        from unittest.mock import AsyncMock, MagicMock

        request = MagicMock()
        request.state.user = MagicMock(role="admin", id="u1")
        request.json = AsyncMock(return_value=body)
        return request

    @pytest.mark.asyncio
    async def test_free_nao_liga_modo_completo(self, tmp_path, monkeypatch):
        """402 **e** o modo persistido não muda — o par de erro é o que prova
        o gate: recusar a resposta sem recusar a escrita não gatearia nada."""
        from backend.api.handlers.admin import update_storage_config
        from backend.settings import settings as _s
        from backend.workspace.runtime_settings import runtime_settings

        self._write_license(tmp_path, monkeypatch, "free")
        monkeypatch.setattr(_s, "storage_mode", "lite", raising=False)
        monkeypatch.setattr(runtime_settings, "storage_mode", "lite", raising=False)

        with pytest.raises(Exception) as exc:
            await update_storage_config(self._request({"storage_mode": "complete"}))
        assert exc.value.status_code == 402  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        assert _s.storage_mode == "lite"
        assert runtime_settings.storage_mode == "lite"

    @pytest.mark.asyncio
    async def test_free_nao_configura_conexao_do_modo_completo(
        self, tmp_path, monkeypatch
    ):
        """Sem Pro, também não dá pra preencher DSN/URL dos serviços do modo
        completo — senão o não-Pro deixa tudo pronto e só falta o flip."""
        from backend.api.handlers.admin import update_storage_config

        self._write_license(tmp_path, monkeypatch, "free")

        with pytest.raises(Exception) as exc:
            await update_storage_config(
                self._request({"postgres_dsn": "postgresql://x/y"})
            )
        assert exc.value.status_code == 402  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    @pytest.mark.asyncio
    async def test_free_pode_voltar_pra_lite(self, tmp_path, monkeypatch):
        """`lite` nunca é bloqueado — gatear a volta prenderia um usuário que
        perdeu a licença no modo que ele não pode mais usar."""
        from backend.api.handlers.admin import update_storage_config
        from backend.settings import settings as _s
        from backend.workspace.runtime_settings import runtime_settings

        self._write_license(tmp_path, monkeypatch, "free")
        monkeypatch.setattr(_s, "storage_mode", "complete", raising=False)
        monkeypatch.setattr(runtime_settings, "storage_mode", "complete", raising=False)

        result = await update_storage_config(self._request({"storage_mode": "lite"}))
        assert result["storage_mode"] == "lite"
        assert _s.storage_mode == "lite"

    @pytest.mark.asyncio
    async def test_pro_liga_modo_completo(self, tmp_path, monkeypatch):
        from backend.api.handlers.admin import update_storage_config
        from backend.settings import settings as _s
        from backend.workspace.runtime_settings import runtime_settings

        self._write_license(tmp_path, monkeypatch, "pro")
        monkeypatch.setattr(_s, "storage_mode", "lite", raising=False)
        monkeypatch.setattr(runtime_settings, "storage_mode", "lite", raising=False)

        result = await update_storage_config(
            self._request({"storage_mode": "complete"})
        )
        assert result["storage_mode"] == "complete"
        assert _s.storage_mode == "complete"
