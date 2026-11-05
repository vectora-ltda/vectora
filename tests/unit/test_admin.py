"""Tests — Root Admin Panel (P1+P2).

Verifica:
- handler admin existe com os endpoints esperados
- endpoints exigem role admin/root (decoradores aplicados)
- schemas de resposta têm estrutura correta
"""

from __future__ import annotations

from typing import Any

import pytest


class TestAdminHandlerExists:
    """src/api/handlers/admin.py deve existir com os endpoints esperados."""

    def test_module_exists(self):
        import backend.api.handlers.admin as mod

        assert mod is not None

    def test_router_exists(self):
        from backend.api.handlers.admin import router

        assert router is not None

    def test_list_users_route_registered(self):
        from backend.api.handlers.admin import router

        paths = [r.path for r in router.routes]  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        assert any("users" in p for p in paths)

    def test_system_info_route_registered(self):
        from backend.api.handlers.admin import router

        paths = [r.path for r in router.routes]  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        assert any("system" in p for p in paths)

    def test_config_route_registered(self):
        from backend.api.handlers.admin import router

        paths = [r.path for r in router.routes]  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        assert any("config" in p for p in paths)

    def test_tools_override_route_registered(self):
        from backend.api.handlers.admin import router

        paths = [r.path for r in router.routes]  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        assert any("tools" in p for p in paths)


class TestAdminRequiresAdminRole:
    """Endpoints admin devem checar permissão de role."""

    def test_require_admin_decorator_exists(self):
        from backend.api.handlers.admin import require_admin

        assert callable(require_admin)

    def test_require_admin_raises_for_member(self):
        """member não tem acesso a endpoints admin."""
        from unittest.mock import MagicMock

        from backend.api.handlers.admin import require_admin

        mock_user = MagicMock()
        mock_user.role = "member"

        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            require_admin(mock_user)

    def test_require_admin_passes_for_root(self):
        from unittest.mock import MagicMock

        from backend.api.handlers.admin import require_admin

        mock_user = MagicMock()
        mock_user.role = "root"

        # Não deve levantar exceção
        result = require_admin(mock_user)
        assert result is None or result == mock_user

    def test_require_admin_passes_for_admin(self):
        from unittest.mock import MagicMock

        from backend.api.handlers.admin import require_admin

        mock_user = MagicMock()
        mock_user.role = "admin"

        result = require_admin(mock_user)
        assert result is None or result == mock_user


class TestAdminSystemInfo:
    """Estrutura do sistema info."""

    def test_system_info_fields_exist(self):
        from backend.api.handlers.admin import _build_system_info

        info = _build_system_info()
        assert "version" in info
        assert "build_version" in info
        assert "python_version" in info
        assert "platform" in info

    def test_build_version_falls_back_to_version_without_env(self, monkeypatch):
        """Sem VECTORA_BUILD_VERSION (dev local, sem build oficial), cai pra semver puro."""
        from backend.api.handlers.admin import _build_system_info

        monkeypatch.delenv("VECTORA_BUILD_VERSION", raising=False)
        info = _build_system_info()
        assert info["build_version"] == info["version"]

    def test_build_version_reads_env_when_set(self, monkeypatch):
        """Com VECTORA_BUILD_VERSION setado (pipeline de release), usa o valor com hash."""
        from backend.api.handlers.admin import _build_system_info

        monkeypatch.setenv("VECTORA_BUILD_VERSION", "0.1.1.11325")
        info = _build_system_info()
        assert info["build_version"] == "0.1.1.11325"


class TestApiKeysMaskKey:
    """_mask_key oculta segredos mantendo prefixo e sufixo."""

    def test_mask_key_long(self):
        from backend.api.handlers.admin import _mask_key

        masked = _mask_key("AIzaSyDEADBEEF12345XYZ")
        assert masked.startswith("AIzaSy")
        assert masked.endswith("5XYZ")
        assert "•" in masked

    def test_mask_key_short(self):
        from backend.api.handlers.admin import _mask_key

        masked = _mask_key("abc")
        assert masked == "•••"

    def test_mask_key_empty(self):
        from backend.api.handlers.admin import _mask_key

        assert _mask_key("") == ""


class TestApiKeysEndpoints:
    """GET/PATCH/POST /admin/api-keys — contratos básicos."""

    @pytest.mark.asyncio
    async def test_get_api_keys_structure(self):
        import os
        from unittest.mock import MagicMock, patch

        request = MagicMock()
        request.state.user = MagicMock(role="root")

        with patch.dict(
            os.environ,
            {
                "GOOGLE_API_KEY": "AIzaSyXXXXLONGKEY123",
                "COHERE_API_KEY": "",
                "TAVILY_API_KEY": "tvly-secretkey987",
            },
        ):
            from backend.api.handlers.admin import get_api_keys

            result = await get_api_keys(request)

        assert "google" in result
        assert "cohere" in result
        assert "tavily" in result
        assert result["google"]["configured"] is True
        assert result["cohere"]["configured"] is False
        assert result["tavily"]["configured"] is True
        assert "AIzaSy" in result["google"]["masked"]

    @pytest.mark.asyncio
    async def test_get_api_keys_returns_masked_not_raw(self):
        import os
        from unittest.mock import MagicMock, patch

        request = MagicMock()
        request.state.user = MagicMock(role="root")

        raw = "AIzaSyDEADBEEFSECRET9999"
        with patch.dict(os.environ, {"GOOGLE_API_KEY": raw}):
            from backend.api.handlers.admin import get_api_keys

            result = await get_api_keys(request)

        assert result["google"]["masked"] != raw

    @pytest.mark.asyncio
    async def test_test_api_key_empty_returns_error(self):
        from unittest.mock import MagicMock

        from backend.api.handlers.admin import TestApiKeyBody, test_api_key

        request = MagicMock()
        request.state.user = MagicMock(role="root")
        body = TestApiKeyBody(provider="google", api_key="")
        result = await test_api_key(request, body)

        assert result["ok"] is False
        assert "vazia" in result["error"].lower() or result["error"]

    @pytest.mark.asyncio
    async def test_test_api_key_unknown_provider(self):
        from unittest.mock import MagicMock

        from backend.api.handlers.admin import TestApiKeyBody, test_api_key

        request = MagicMock()
        request.state.user = MagicMock(role="root")
        body = TestApiKeyBody(provider="openai_unsupported", api_key="sk-123")
        result = await test_api_key(request, body)

        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_patch_api_keys_calls_upsert(self):
        from unittest.mock import MagicMock, patch

        from backend.api.handlers.admin import PatchApiKeysBody, patch_api_keys

        request = MagicMock()
        request.state.user = MagicMock(role="root", id="u1")

        with (
            patch("backend.cli.keys.upsert_env_key") as mock_upsert,
            patch("backend.api.handlers.admin._env_file", return_value=MagicMock()),
        ):
            body = PatchApiKeysBody(
                google_api_key="AIzaSyNEW", cohere_api_key=None, tavily_api_key=None
            )
            result = await patch_api_keys(request, body)

        assert result["status"] == "updated"
        assert "GOOGLE_API_KEY" in result["updated"]
        assert mock_upsert.called

    @pytest.mark.asyncio
    async def test_get_api_keys_le_dos_fields_do_registry(self, monkeypatch):
        """A leitura passa pelos fields do registry (EnvAdapter), não por
        os.environ direto — mesma fonte usada por CLI/outros handlers."""
        from unittest.mock import MagicMock

        from backend.config import registry

        captured: list[str] = []

        class _FakeAdapter:
            def __init__(self, env_var: str) -> None:
                self.env_var = env_var

            def get(self, key: str) -> object:
                captured.append(self.env_var)
                return "AIzaSyFAKE123"

            def set(self, key: str, value: object) -> None:
                pass

        adapters: dict[str, _FakeAdapter] = {
            "google_api_key": _FakeAdapter("GOOGLE_API_KEY"),
            "cohere_api_key": _FakeAdapter("COHERE_API_KEY"),
        }
        monkeypatch.setattr(
            registry,
            "_REGISTRY",
            {
                k: registry.SettingField(
                    key=k,
                    category="integrations",
                    cli_flag=f"--{k}",
                    description="d",
                    adapter=adapter,
                )
                for k, adapter in adapters.items()
            },
        )

        request = MagicMock()
        request.state.user = MagicMock(role="root")

        from backend.api.handlers.admin import get_api_keys

        result = await get_api_keys(request)

        assert set(captured) == {"GOOGLE_API_KEY", "COHERE_API_KEY"}
        assert result["google"]["configured"] is True
        assert "AIzaSy" in result["google"]["masked"]


class TestCreateInviteRequiresPro:
    """Convidar membro adicional é feature de time — exige plano Pro.

    O 1º usuário (root) nasce direto no signup, sem passar por /admin/invites
    (backend/services/auth.py::signup) — este endpoint gateia só convites de
    membros extras (2º+ usuário).
    """

    @pytest.mark.asyncio
    async def test_create_invite_raises_402_on_free(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        from backend.api.handlers.admin import CreateInviteBody, create_invite
        from backend.services import license as lic

        monkeypatch.setattr(lic, "CACHE_PATH", tmp_path / "license_cache.json")

        request = MagicMock()
        request.state.user = MagicMock(role="admin", id="u1")
        body = CreateInviteBody(role="member", email="novo@example.com")

        with pytest.raises(Exception) as exc:
            await create_invite(request, body)
        assert exc.value.status_code == 402  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    @pytest.mark.asyncio
    async def test_create_invite_passes_gate_on_pro(self, tmp_path, monkeypatch):
        """Com plano pro, o gate libera (segue para require_admin / lógica normal)."""
        import json
        from datetime import UTC, datetime
        from unittest.mock import MagicMock

        from backend.api.handlers.admin import CreateInviteBody, create_invite
        from backend.services import license as lic

        cache_path = tmp_path / "license_cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "tier": "pro",
                    "status": "active",
                    "days_remaining": 30,
                    "expires_at": "2027-01-01",
                    "validated_at": datetime.now(UTC).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(lic, "CACHE_PATH", cache_path)

        # Sem role admin/root → passa pelo gate de pro e falha depois no
        # require_admin (403), não no gate de tier (402) — confirma a ordem.
        request = MagicMock()
        request.state.user = MagicMock(role="member", id="u1")
        body = CreateInviteBody(role="member", email="novo@example.com")

        with pytest.raises(Exception) as exc:
            await create_invite(request, body)
        assert exc.value.status_code == 403  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]


class TestToggleToolGlobal:
    """Kill-switch global de tools (/admin/tools, /admin/tools/{name}/toggle).

    Antes, o toggle não persistia nada (TODO em código) e list_tools_admin
    sempre devolvia enabled=True — um admin podia desligar uma tool
    "destructive" e ela continuar ativa pro agente. Estes testes cobrem a
    persistência real via tool_policy.GLOBAL_SCOPE e a leitura de volta.
    """

    @pytest.fixture(autouse=True)
    def iso_dir(self, tmp_path, monkeypatch):
        from backend.rbac import tool_policy

        monkeypatch.setattr(tool_policy, "_policy_dir", lambda: tmp_path / "tools")

    @staticmethod
    def _admin_request() -> Any:
        from unittest.mock import MagicMock

        request = MagicMock()
        request.state.user = MagicMock(role="admin", id="admin-1")
        return request

    @pytest.mark.asyncio
    async def test_list_tools_all_enabled_by_default(self):
        from backend.api.handlers.admin import list_tools_admin

        result = await list_tools_admin(self._admin_request())
        assert result["total"] > 0
        assert all(t["enabled"] for t in result["tools"])

    @pytest.mark.asyncio
    async def test_toggle_off_persists_and_reflects_in_list(self):
        from backend.api.handlers.admin import (
            ToolToggleBody,
            list_tools_admin,
            toggle_tool,
        )
        from backend.nodes.tools import ALL_TOOLS

        target = ALL_TOOLS[0].name

        res = await toggle_tool(
            self._admin_request(), target, ToolToggleBody(enabled=False)
        )
        assert res == {"status": "ok", "tool": target, "enabled": False}

        listing = await list_tools_admin(self._admin_request())
        entry = next(t for t in listing["tools"] if t["name"] == target)
        assert entry["enabled"] is False

    @pytest.mark.asyncio
    async def test_toggle_persists_via_tool_policy_global_scope(self):
        """A persistência real é tool_policy — não um dict/atributo local do handler."""
        from backend.api.handlers.admin import ToolToggleBody, toggle_tool
        from backend.nodes.tools import ALL_TOOLS
        from backend.rbac import tool_policy

        target = ALL_TOOLS[0].name
        await toggle_tool(self._admin_request(), target, ToolToggleBody(enabled=False))

        assert target in tool_policy.get_disabled(tool_policy.GLOBAL_SCOPE)
        assert tool_policy.is_allowed("qualquer-usuario", target) is False

    @pytest.mark.asyncio
    async def test_toggle_back_on_reenables(self):
        from backend.api.handlers.admin import ToolToggleBody, toggle_tool
        from backend.nodes.tools import ALL_TOOLS
        from backend.rbac import tool_policy

        target = ALL_TOOLS[0].name
        await toggle_tool(self._admin_request(), target, ToolToggleBody(enabled=False))
        await toggle_tool(self._admin_request(), target, ToolToggleBody(enabled=True))

        assert tool_policy.is_allowed("qualquer-usuario", target) is True

    @pytest.mark.asyncio
    async def test_toggle_unknown_tool_404(self):
        from fastapi import HTTPException

        from backend.api.handlers.admin import ToolToggleBody, toggle_tool

        with pytest.raises(HTTPException) as exc:
            await toggle_tool(
                self._admin_request(),
                "ferramenta-que-nao-existe",
                ToolToggleBody(enabled=False),
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_toggle_requires_admin_role(self):
        from unittest.mock import MagicMock

        from fastapi import HTTPException

        from backend.api.handlers.admin import ToolToggleBody, toggle_tool
        from backend.nodes.tools import ALL_TOOLS

        request = MagicMock()
        request.state.user = MagicMock(role="member", id="u1")

        with pytest.raises(HTTPException) as exc:
            await toggle_tool(request, ALL_TOOLS[0].name, ToolToggleBody(enabled=False))
        assert exc.value.status_code == 403


class TestTimezoneEndpoint:
    """Timezone do usuário — o backend já usava `user_timezone` no scheduler,
    mas só dava pra configurar por API interna; sem estes endpoints a UI não
    tem como expor o campo."""

    def test_rotas_registradas(self):
        from backend.api.handlers.admin import router

        paths = [r.path for r in router.routes]  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        assert any("timezone" in p for p in paths)

    @pytest.mark.asyncio
    async def test_salva_timezone_valido_e_rejeita_invalido(self, monkeypatch):
        from fastapi import HTTPException

        from backend.api.handlers import admin
        from backend.workspace.runtime_settings import runtime_settings

        gravados: list[str] = []
        monkeypatch.setattr(admin, "require_admin", lambda _u: None)
        monkeypatch.setattr(runtime_settings, "set_user_timezone", gravados.append)
        monkeypatch.setattr(
            type(runtime_settings),
            "user_timezone",
            property(lambda _s: gravados[-1] if gravados else ""),
        )

        class _Req:
            state = type("S", (), {"user": object()})()

        # Happy: zona IANA real é aceita e persistida.
        out = await admin.patch_timezone(
            _Req(),  # ty: ignore[invalid-argument-type]
            admin.TimezoneBody(timezone="America/Sao_Paulo"),
        )
        assert out["timezone"] == "America/Sao_Paulo"
        assert gravados == ["America/Sao_Paulo"]

        # Erro/borda: zona inventada é REJEITADA com 422 em vez de aceita e
        # degradada — salvar outra coisa faria os agendamentos dispararem num
        # fuso que o usuário não pediu, sem aviso nenhum.
        with pytest.raises(HTTPException) as exc:
            await admin.patch_timezone(
                _Req(),  # ty: ignore[invalid-argument-type]
                admin.TimezoneBody(timezone="Marte/Olympus"),
            )
        assert exc.value.status_code == 422
        assert gravados == ["America/Sao_Paulo"], "gravou apesar de inválido"

        # Borda: string vazia é válida (volta ao fuso local do SO).
        await admin.patch_timezone(
            _Req(),  # ty: ignore[invalid-argument-type]
            admin.TimezoneBody(timezone="  "),
        )
        assert gravados[-1] == ""

    @pytest.mark.asyncio
    async def test_get_devolve_lista_de_zonas_para_o_seletor(self, monkeypatch):
        from backend.api.handlers import admin

        monkeypatch.setattr(admin, "require_admin", lambda _u: None)

        class _Req:
            state = type("S", (), {"user": object()})()

        out = await admin.get_timezone(_Req())  # ty: ignore[invalid-argument-type]

        # A lista vem do zoneinfo do próprio backend — copiá-la no frontend
        # faria a UI divergir do que o backend aceita.
        assert "America/Sao_Paulo" in out["available"]
        assert "UTC" in out["available"]
        assert len(out["available"]) > 100


class TestServiceTokenEndpoints:
    """POST/GET/DELETE /admin/service-tokens — apenas root."""

    @pytest.fixture(autouse=True)
    async def _isolate_db(self, tmp_path, monkeypatch):
        import aiosqlite

        import backend.rbac.auth as auth_mod

        auth_mod._db_conn = None
        db_file = str(tmp_path / "admin_service_tokens.db")

        async def _patched_get_db():
            if auth_mod._db_conn is not None:
                return auth_mod._db_conn
            conn = await aiosqlite.connect(db_file)
            conn.row_factory = aiosqlite.Row
            await auth_mod._ensure_schema(conn)
            auth_mod._db_conn = conn
            return conn

        monkeypatch.setattr(auth_mod, "_get_db", _patched_get_db)
        yield
        if auth_mod._db_conn is not None:
            await auth_mod._db_conn.close()
            auth_mod._db_conn = None

    def _req(self, role: str = "root") -> Any:
        class _Req:
            state = type(
                "S", (), {"user": type("U", (), {"role": role, "id": "root-1"})()}
            )()

        return _Req()

    async def test_root_cria_lista_e_revoga_token(self):
        from backend.api.handlers import admin

        criado = await admin.create_service_token_endpoint(
            self._req(), admin.CreateServiceTokenBody(name="ci-bot", scopes=["*"])
        )
        assert criado["raw_token"].startswith("vst_")
        token_id = criado["token"]["id"]

        listado = await admin.list_service_tokens_endpoint(self._req())
        assert any(t["id"] == token_id for t in listado["tokens"])

        revogado = await admin.revoke_service_token_endpoint(self._req(), token_id)
        assert revogado == {"revoked": True}
        # Erro/borda: revogar de novo não é erro, só devolve False.
        revogado_de_novo = await admin.revoke_service_token_endpoint(
            self._req(), token_id
        )
        assert revogado_de_novo == {"revoked": False}

    async def test_role_nao_root_e_negada_com_403(self):
        """Erro/borda: admin (não-root) não pode criar/listar/revogar
        tokens de serviço — mesmo peso de criar um usuário novo."""
        from fastapi import HTTPException

        from backend.api.handlers import admin

        with pytest.raises(HTTPException) as exc:
            await admin.create_service_token_endpoint(
                self._req(role="admin"),
                admin.CreateServiceTokenBody(name="x", scopes=[]),
            )
        assert exc.value.status_code == 403

        with pytest.raises(HTTPException) as exc:
            await admin.list_service_tokens_endpoint(self._req(role="admin"))
        assert exc.value.status_code == 403


class TestResolveAgent:
    """GET /admin/agents/resolve — dump do NativeAgent resolvido,
    equivalente nativo ao `dsh --dump-config` do deepseek-harness. Existe
    pra debugar por que uma tool/subagente não aparece pra um usuário sem
    precisar vasculhar logs."""

    @staticmethod
    def _req(role: str = "admin") -> Any:
        from unittest.mock import MagicMock

        request = MagicMock()
        request.state.user = MagicMock(role=role, id="admin-1")
        return request

    @pytest.mark.asyncio
    async def test_admin_resolve_devolve_tools_prompt_e_subagentes(self):
        from backend.api.handlers.admin import resolve_agent

        result = await resolve_agent(self._req(), user_id="alice", chat_mode=True)

        assert result["user_id"] == "alice"
        assert result["chat_mode"] is True
        assert result["workspace_id"] is None
        assert isinstance(result["tools"], list)
        assert len(result["tools"]) > 0
        assert all(
            {"name", "category", "destructive", "render_hint"} <= t.keys()
            for t in result["tools"]
        )
        assert result["system_prompt_length"] == len(result["system_prompt"])
        # chat_mode=True não delega a subagentes (agent_factory._native_subagent_catalog)
        assert result["subagents"] == []

    @pytest.mark.asyncio
    async def test_sem_workspace_e_user_id_usa_defaults(self):
        """Erro/borda: sem `user_id`/`workspace_id` (query params vazios,
        caso real de um admin só testando o endpoint), resolve pro agente
        'local' padrão em vez de lançar."""
        from backend.api.handlers.admin import resolve_agent

        result = await resolve_agent(self._req())

        assert result["user_id"] == "local"
        assert result["workspace_id"] is None
        assert len(result["tools"]) > 0

    @pytest.mark.asyncio
    async def test_usuario_nao_admin_recebe_403(self):
        from fastapi import HTTPException

        from backend.api.handlers.admin import resolve_agent

        with pytest.raises(HTTPException) as exc:
            await resolve_agent(self._req(role="member"), user_id="alice")

        assert exc.value.status_code == 403
