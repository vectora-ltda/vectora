"""Tests — GitHub OAuth integration (O2).

Verifica:
- handler oauth existe com os endpoints esperados
- INTEGRATIONS_REGISTRY lista as integrações conhecidas (O1)
- status endpoint responde corretamente quando não configurado
"""

from __future__ import annotations

import pytest


class TestOAuthHandlerExists:
    """src/api/handlers/oauth.py deve existir com os endpoints esperados."""

    def test_module_exists(self):
        import backend.api.handlers.oauth as mod

        assert mod is not None

    def test_router_exists(self):
        from backend.api.handlers.oauth import router

        assert router is not None

    def test_github_status_route_registered(self):
        from backend.api.handlers.oauth import router

        paths = [r.path for r in router.routes]  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        assert any("github" in p and "status" in p for p in paths)

    def test_github_start_route_registered(self):
        from backend.api.handlers.oauth import router

        paths = [r.path for r in router.routes]  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        assert any("github" in p for p in paths)

    def test_github_callback_route_registered(self):
        from backend.api.handlers.oauth import router

        paths = [r.path for r in router.routes]  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        assert any("callback" in p for p in paths)

    def test_github_disconnect_route_registered(self):
        from backend.api.handlers.oauth import router

        methods_paths = [
            (method, r.path)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
            for r in router.routes  # type: ignore[attr-defined]
            for method in getattr(r, "methods", [])
        ]
        assert any(m == "DELETE" and "github" in p for m, p in methods_paths)


class TestIntegrationsRegistry:
    """INTEGRATIONS_REGISTRY deve declarar as integrações O1."""

    def test_registry_exists(self):
        from backend.api.handlers.oauth import INTEGRATIONS_REGISTRY

        assert INTEGRATIONS_REGISTRY is not None
        assert len(INTEGRATIONS_REGISTRY) > 0

    def test_openai_present(self):
        from backend.api.handlers.oauth import INTEGRATIONS_REGISTRY

        keys = [i["env_var"] for i in INTEGRATIONS_REGISTRY]
        assert "OPENAI_API_KEY" in keys

    def test_anthropic_present(self):
        from backend.api.handlers.oauth import INTEGRATIONS_REGISTRY

        keys = [i["env_var"] for i in INTEGRATIONS_REGISTRY]
        assert "ANTHROPIC_API_KEY" in keys

    def test_each_integration_has_required_fields(self):
        from backend.api.handlers.oauth import INTEGRATIONS_REGISTRY

        required = {"id", "name", "env_var", "kind"}
        for integration in INTEGRATIONS_REGISTRY:
            missing = required - set(integration.keys())
            assert not missing, f"Integração {integration.get('id')} falta: {missing}"

    def test_github_is_hybrid_kind(self):
        # GitHub aceita OAuth OU Personal Access Token (kind="hybrid").
        from backend.api.handlers.oauth import INTEGRATIONS_REGISTRY

        github = next((i for i in INTEGRATIONS_REGISTRY if i["id"] == "github"), None)
        assert github is not None
        assert github["kind"] == "hybrid"
        assert github["env_var"] == "GITHUB_TOKEN"

    def test_openai_is_apikey_kind(self):
        from backend.api.handlers.oauth import INTEGRATIONS_REGISTRY

        openai = next((i for i in INTEGRATIONS_REGISTRY if i["id"] == "openai"), None)
        assert openai is not None
        assert openai["kind"] == "apikey"

    def test_gemini_present_com_google_api_key(self):
        """Gemini/Google Gemini faltava no catálogo — usava GOOGLE_API_KEY
        (mesma env que o resto do backend já lê), não GEMINI_API_KEY."""
        from backend.api.handlers.oauth import INTEGRATIONS_REGISTRY

        gemini = next((i for i in INTEGRATIONS_REGISTRY if i["id"] == "gemini"), None)
        assert gemini is not None
        assert gemini["env_var"] == "GOOGLE_API_KEY"
        assert gemini["kind"] == "apikey"

    def test_github_declara_alias_personal_access_token(self):
        """GITHUB_PERSONAL_ACCESS_TOKEN é a convenção do servidor MCP oficial
        do GitHub — reconhecida como alias pra não depender de qual dos dois
        nomes o usuário configurou."""
        from backend.api.handlers.oauth import INTEGRATIONS_REGISTRY

        github = next((i for i in INTEGRATIONS_REGISTRY if i["id"] == "github"), None)
        assert github is not None
        assert "GITHUB_PERSONAL_ACCESS_TOKEN" in github.get("env_var_aliases", [])
