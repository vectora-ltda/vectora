"""Testes de componentes-chave do motor nativo: contexto de execução,
permissões de filesystem, namespacing de memórias e montagem do agente.

Cobertura:
    - VectoraContext: construção correta a partir de config dict
    - FilesystemPermission: regras DENY/ALLOW/INTERRUPT
    - Memory tools: namespace correto + store API
    - backends.build_store: InMemoryStore sem index quando sem API key
    - agent_factory._subagent_specs: ABAC filtering
    - agent_factory._agents_md_paths: retorna None se não há AGENTS.md
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# VectoraContext
# ---------------------------------------------------------------------------


class TestVectoraContext:
    def test_decision_preferences_come_from_the_authenticated_user_store(
        self, monkeypatch
    ):
        """Runtime prefs are trusted; caller config cannot forge a decision model opt-in."""
        from backend.vtypes.context import ctx_from_config

        class Preferences:
            def get_frontend_prefs(
                self, user_id: str, workspace_id: str | None = None
            ) -> dict[str, object]:
                assert user_id == "alice"
                assert workspace_id is None
                return {
                    "decisionModelsEnabled": True,
                    "decisionModelProvider": "jev-api",
                    "decisionModelSurfaces": {"hitl": True, "mcp": False},
                }

        monkeypatch.setattr(
            "backend.workspace.runtime_settings.runtime_settings", Preferences()
        )
        ctx = ctx_from_config(
            {
                "configurable": {
                    "user_id": "alice",
                    "decision_model_preferences": {"enabled": False},
                }
            }
        )
        assert ctx._extra["decision_model_preferences"] == {
            "enabled": True,
            "provider": "jev-api",
            "surfaces": {"hitl": True, "mcp": False},
        }

    def test_ctx_from_config_full(self):
        """Todos os campos são preenchidos corretamente."""
        from backend.vtypes.context import ctx_from_config

        config = {
            "configurable": {
                "user_id": "u1",
                "workspace_id": "ws1",
                "permission_mode": "ask",
                "org_id": "org1",
                "language": "pt-BR",
                "model": "claude-3-5-sonnet",
                "thread_id": "t1",
            }
        }
        ctx = ctx_from_config(config)
        assert ctx.user_id == "u1"
        assert ctx.workspace_id == "ws1"
        assert ctx.permission_mode == "ask"
        assert ctx.org_id == "org1"
        assert ctx.locale == "pt-BR"
        assert ctx.model == "claude-3-5-sonnet"
        assert ctx.thread_id == "t1"

    def test_ctx_from_config_empty(self):
        """Valores padrão quando config é None."""
        from backend.vtypes.context import ctx_from_config

        ctx = ctx_from_config(None)
        assert ctx.user_id == "local"
        assert ctx.workspace_id == ""
        assert ctx.permission_mode == "ask"

    def test_ctx_from_config_partial(self):
        """Campos ausentes recebem defaults."""
        from backend.vtypes.context import ctx_from_config

        ctx = ctx_from_config({"configurable": {"user_id": "u2"}})
        assert ctx.user_id == "u2"
        assert ctx.permission_mode == "ask"
        assert ctx.locale == ""

    def test_ctx_locale_fallback(self):
        """``locale`` aceita ``language`` como alias."""
        from backend.vtypes.context import ctx_from_config

        ctx = ctx_from_config({"configurable": {"language": "en-US"}})
        assert ctx.locale == "en-US"


# ---------------------------------------------------------------------------
# FilesystemPermission
# ---------------------------------------------------------------------------


class TestFilesystemPermission:
    def test_deny_env_file(self):
        """Arquivos .env devem ser DENY para qualquer operação."""
        from backend.rbac.permissions import FS_PERMISSION

        assert FS_PERMISSION.check(".env", "read") == "deny"
        assert FS_PERMISSION.check(".env", "write") == "deny"
        assert FS_PERMISSION.check("project/.env.local", "read") == "deny"

    def test_deny_ssh_key(self):
        """Chaves SSH privadas devem ser DENY."""
        from backend.rbac.permissions import FS_PERMISSION

        assert FS_PERMISSION.check("id_rsa", "read") == "deny"
        assert FS_PERMISSION.check("/home/user/.ssh/id_ed25519", "read") == "deny"

    def test_allow_workspace(self):
        """Paths sob /workspace/ devem ser ALLOW."""
        from backend.rbac.permissions import FS_PERMISSION

        assert FS_PERMISSION.check("/workspace/src/main.py", "read") == "allow"
        assert FS_PERMISSION.check("/workspace/package.json", "write") == "allow"

    def test_allow_memories_read(self):
        """Leitura de memórias deve ser ALLOW."""
        from backend.rbac.permissions import FS_PERMISSION

        assert FS_PERMISSION.check("/memories/notes.md", "read") == "allow"

    def test_deny_skills_write(self):
        """Escrita em skills deve ser DENY."""
        from backend.rbac.permissions import FS_PERMISSION

        assert (
            FS_PERMISSION.check("/memories/skills/my-skill/SKILL.md", "write") == "deny"
        )

    def test_allow_skills_read(self):
        """Leitura de skills deve ser ALLOW."""
        from backend.rbac.permissions import FS_PERMISSION

        assert (
            FS_PERMISSION.check("/memories/skills/my-skill/SKILL.md", "read") == "allow"
        )

    def test_interrupt_external_write(self):
        """Escrita fora do workspace deve ser INTERRUPT."""
        from backend.rbac.permissions import FS_PERMISSION

        assert FS_PERMISSION.check("/tmp/something.txt", "write") == "interrupt"
        assert FS_PERMISSION.check("/home/user/notes.txt", "write") == "interrupt"

    def test_allow_external_read(self):
        """Leitura fora do workspace deve ser ALLOW (sem interrupt)."""
        from backend.rbac.permissions import FS_PERMISSION

        result = FS_PERMISSION.check("/tmp/something.txt", "read")
        assert result == "allow"

    def test_is_allowed_helper(self):
        """Atalho is_allowed() funciona corretamente."""
        from backend.rbac.permissions import FS_PERMISSION

        assert FS_PERMISSION.is_allowed("/workspace/file.py", "read") is True
        assert FS_PERMISSION.is_allowed(".env", "read") is False

    def test_requires_interrupt_helper(self):
        """requires_interrupt() detecta ops que precisam de aprovação."""
        from backend.rbac.permissions import FS_PERMISSION

        assert FS_PERMISSION.requires_interrupt("/tmp/file.txt", "write") is True
        assert FS_PERMISSION.requires_interrupt("/workspace/file.py", "write") is False


# ---------------------------------------------------------------------------
# Memory tools namespace + store API
# ---------------------------------------------------------------------------


class TestMemoryToolsNamespace:
    def test_user_id_from_ctx_authenticated(self):
        """user_id autenticado deve ser retornado limpo."""
        from backend.tools.context import ToolContext
        from backend.tools.memory import _user_id_from_ctx

        ctx = ToolContext(user_id="abc123")
        assert _user_id_from_ctx(ctx) == "abc123"

    def test_user_id_from_ctx_workspace(self):
        """workspace_id usado quando não há user_id."""
        from backend.tools.context import ToolContext
        from backend.tools.memory import _user_id_from_ctx

        ctx = ToolContext(workspace_id="ws1")
        assert _user_id_from_ctx(ctx) == "workspace_ws1"

    def test_user_id_from_ctx_thread_fallback(self):
        """thread_id como fallback."""
        from backend.tools.context import ToolContext
        from backend.tools.memory import _user_id_from_ctx

        ctx = ToolContext(thread_id="t42")
        assert _user_id_from_ctx(ctx) == "session_t42"

    def test_user_id_from_ctx_bare(self):
        """ctx sem nenhum identificador retorna 'local'."""
        from backend.tools.context import ToolContext
        from backend.tools.memory import _user_id_from_ctx

        assert _user_id_from_ctx(ToolContext()) == "local"

    def test_memory_namespace_structure(self):
        """Namespace deve ser tupla 3-uple."""
        from backend.tools.context import ToolContext
        from backend.tools.memory import _memory_namespace

        ctx = ToolContext(user_id="u1")
        ns = _memory_namespace(ctx)
        assert ns == ("user", "u1", "memories")
        assert isinstance(ns, tuple)
        assert len(ns) == 3


# ---------------------------------------------------------------------------
# backends.build_store
# ---------------------------------------------------------------------------


class TestBuildStore:
    async def test_build_store_returns_vectora_store(self):
        """build_store() (async) retorna VectoraStore (nativo, aiosqlite)
        persistente."""
        import contextlib

        from backend.llm.backends import build_store
        from backend.persistence.native.store import VectoraStore

        with patch("backend.llm.backends._build_index", return_value=None):
            store = await build_store()
        assert isinstance(store, VectoraStore)
        # Fecha o pool aiosqlite aberto pelo store para não vazar handle.
        with contextlib.suppress(Exception):
            await store._pool.close()

    def test_build_store_no_index_when_no_key(self):
        """Sem nenhum provider de embedding configurado, build_store()
        retorna store sem indexação — `_build_index` delega a
        `_build_lc_embeddings()` (fallback Cohere↔Voyage↔Ollama↔OpenRouter)."""
        from backend.llm.backends import _build_index

        with patch("backend.storage.factory._build_lc_embeddings", return_value=None):
            result = _build_index(None)
        assert result is None

    async def test_build_store_index_uses_lc_embeddings_fallback(self):
        """Com um provider resolvido por `_build_lc_embeddings()`, o
        IndexConfig usa esse client (fallback multi-provider) em vez de
        `CohereEmbeddings` hardcoded."""
        from backend.llm.backends import _build_index

        class _FakeEmb:
            async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
                return [[0.1] * 1024 for _ in texts]

        with patch(
            "backend.storage.factory._build_lc_embeddings", return_value=_FakeEmb()
        ):
            result = _build_index(None)

        assert result is not None
        assert result["dims"] == 1024
        assert result["fields"] == ["content"]
        vetores = await result["embed"](["a"])
        assert vetores == [[0.1] * 1024]


# ---------------------------------------------------------------------------
# _agents_md_paths
# ---------------------------------------------------------------------------


class TestAgentsMdPaths:
    def test_returns_none_when_no_file(self, tmp_path):
        """Sem AGENTS.md retorna None."""
        from backend.services.agent_factory import _agents_md_paths

        with patch("backend.services.agent_factory.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_agents_md = MagicMock()
            mock_agents_md.is_file.return_value = False
            mock_home.__truediv__.return_value.__truediv__.return_value = mock_agents_md
            result = _agents_md_paths()
        assert result is None

    def test_returns_list_when_file_exists(self):
        """Com AGENTS.md existente retorna lista com o path."""
        from backend.services.agent_factory import _agents_md_paths

        with patch("backend.services.agent_factory.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_agents_md = MagicMock()
            mock_agents_md.is_file.return_value = True
            mock_agents_md.__str__.return_value = "/home/user/.vectora/AGENTS.md"
            mock_home.__truediv__.return_value.__truediv__.return_value = mock_agents_md
            result = _agents_md_paths()
        assert isinstance(result, list)
        assert len(result) == 1
