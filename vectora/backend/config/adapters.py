"""Adapters que envolvem os mecanismos de persistência já existentes com a
interface uniforme ``get(key)``/``set(key, value)`` exigida por
``backend.config.registry.SettingField`` — nenhum deles substitui o
mecanismo real, só declara um contrato comum por cima.
"""

from __future__ import annotations


class EnvAdapter:
    """Envolve ``backend/services/env_keys.py`` — persiste em ``.env`` +
    ``os.environ`` + ``settings`` (singleton em memória), mesmo caminho já
    usado por ``/auth/envs`` e ``/admin/api-keys``.
    """

    def __init__(self, env_var: str) -> None:
        self.env_var = env_var

    def get(self, key: str) -> object:
        import os

        # Fonte canônica em runtime: os.environ (que apply_llm_env_key seta
        # no momento da escrita). settings espelha no boot, mas uma key
        # gravada/limpa em runtime só reflete aqui de forma confiável; o
        # GET /admin/api-keys mocka exatamente os.environ.
        return os.environ.get(self.env_var, "") or None

    def set(self, key: str, value: object) -> None:
        from backend.services.env_keys import apply_llm_env_key, default_env_file

        apply_llm_env_key(default_env_file(), self.env_var, str(value or ""))


class RuntimeSettingsAdapter:
    """Envolve ``backend/workspace/runtime_settings.py`` (SQLite
    ``app_settings``, global — não por usuário). ``settings_key`` permite
    apontar pra uma chave interna diferente do nome público do campo (ex.:
    campo público ``timezone`` → chave interna ``user_timezone``).
    """

    def __init__(self, settings_key: str | None = None) -> None:
        self.settings_key = settings_key

    def get(self, key: str) -> object:
        from backend.workspace.runtime_settings import runtime_settings

        return runtime_settings.get(self.settings_key or key)

    def set(self, key: str, value: object) -> None:
        from backend.workspace.runtime_settings import runtime_settings

        runtime_settings.set(self.settings_key or key, value)


class ConfigTomlAdapter:
    """Envolve ``backend/services/license.py::write_config_section``
    (``~/.vectora/config.toml``) — usado pela seção ``[server]`` hoje
    consumida por ``/admin/config``. Mantém ``settings`` (singleton em
    memória) sincronizado, mesmo padrão do handler REST existente.
    """

    def __init__(self, section: str, toml_key: str | None = None) -> None:
        self.section = section
        self.toml_key = toml_key

    def get(self, key: str) -> object:
        from backend.settings import settings

        return getattr(settings, self.toml_key or key, None)

    def set(self, key: str, value: object) -> None:
        from backend.services.license import write_config_section
        from backend.settings import settings

        if value is not None and not isinstance(value, (str, int, bool)):
            msg = f"ConfigTomlAdapter só aceita str/int/bool/None, recebeu {type(value)!r}"
            raise TypeError(msg)
        attr = self.toml_key or key
        object.__setattr__(settings, attr, value)
        write_config_section(self.section, {attr: value})


class RegisteredModelsTableAdapter:
    """Envolve as tabelas SQLite ad hoc de modelos registrados por gateway do
    `provider_routing` (``ollama_registered_models`` /
    ``llamacpp_registered_models`` / ``openrouter_registered_models`` /
    ``nine_router_registered_models``). Reusa o resolver de DB e as funções
    internas do handler — não duplica a lógica de tabela.
    """

    def __init__(self, table: str) -> None:
        self.table = table

    async def list_items(self) -> list[dict[str, str]]:
        from backend.api.handlers.provider_routing import _list_registered

        rows = await _list_registered(self.table)
        return [{"id": r.id, "tag": r.tag, "created_at": r.created_at} for r in rows]

    async def add(self, item: dict[str, str]) -> dict[str, str]:
        from backend.api.handlers.provider_routing import _register

        registered = await _register(self.table, item["tag"])
        return {
            "id": registered.id,
            "tag": registered.tag,
            "created_at": registered.created_at,
        }

    async def remove(self, item_id: str) -> None:
        from backend.api.handlers.provider_routing import _unregister

        await _unregister(self.table, item_id)


class UserRowAdapter:
    """Envolve o acesso por-usuário a ``users.env_overrides_json``
    (``backend/rbac/auth.py``) para uma única env var.

    O registry escalar (`SettingField`) é **global** — um campo não tem
    contexto de usuário. Este adapter é o contraponto por-usuário: encapsula
    UMA chave e recebe ``user_id`` em cada operação, isolando o acesso à
    tabela do resto da lógica de requisição. Delega para os mesmos serviços
    de ``rbac.auth`` que o handler REST já usa — não duplica storage.
    """

    def __init__(self, env_key: str) -> None:
        self.env_key = env_key

    async def get(self, user_id: str) -> object:
        from backend.rbac import auth as auth_svc

        overrides = await auth_svc.get_env_overrides(user_id)
        return overrides.get(self.env_key)

    async def set(self, user_id: str, value: object) -> None:
        from backend.rbac import auth as auth_svc

        await auth_svc.set_env_override(user_id, self.env_key, str(value or ""))

    async def delete(self, user_id: str) -> None:
        from backend.rbac import auth as auth_svc

        await auth_svc.delete_env_override(user_id, self.env_key)


class UserProfileAdapter:
    """Recurso per-usuário `account` — o perfil de um usuário como item,
    escopado por ``user_id``. `list` devolve o perfil do dono (0 ou 1 itens);
    `add` atualiza o nome; `remove` é no-op (perfil não é deletável pelo
    schema). Delega para ``rbac.auth``.
    """

    async def list_items(self, user_id: str) -> list[dict[str, object]]:
        from backend.rbac import auth as auth_svc

        user = await auth_svc.get_user_by_id(user_id)
        if user is None:
            return []
        return [
            {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "name": user.name,
                "role": user.role,
            }
        ]

    async def add(self, item: dict[str, object]) -> dict[str, object] | None:
        from backend.rbac import auth as auth_svc

        user_id = str(item["user_id"])
        name = str(item.get("name") or "")
        await auth_svc.update_profile(user_id, name=name)
        return await self._read_one(user_id)

    async def _read_one(self, user_id: str) -> dict[str, object] | None:
        items = await self.list_items(user_id)
        return items[0] if items else None

    async def remove(self, user_id: str, item_id: str) -> None:
        # O perfil do usuário não é removível pelo schema declarativo (a
        # gestão de usuário tem endpoint próprio). No-op de contrato.
        return None


class MemoryAdapter:
    """Recurso per-usuário `memory` — fatos de memória por usuário.

    Uma memória é um item escopado por ``user_id`` (namespace
    ``("user", user_id, "memories")`` do store do agente). O store é
    injetado por contextvar num turno de agente; chamado fora de um grafo
    ativo (ex. CLI), `list` degrada para lista vazia com log em vez de
    quebrar — a listagem completa requer o contexto do agente.
    """

    async def list_items(self, user_id: str) -> list[dict[str, object]]:
        try:
            import backend.tools.memory as mem
            from backend.tools.context import ToolContext

            store = mem._get_store(ToolContext(user_id=user_id))
        except Exception as exc:  # RuntimeError: fora de um grafo ativo
            import logging

            logging.getLogger(__name__).info(
                "MemoryAdapter.list sem store de agente ativo (user_id=%s): %s",
                user_id,
                exc,
            )
            return []
        ns = ("user", user_id, "memories")
        items = await store.asearch(ns, query="", limit=100)
        return [
            {
                "key": item.key,
                "content": item.value.get("content", ""),
                "updated_at": item.value.get("updated_at"),
            }
            for item in items
        ]

    async def add(self, item: dict[str, object]) -> dict[str, object] | None:
        import backend.tools.memory as mem

        user_id = str(item["user_id"])
        key = str(item["key"])
        content = str(item["content"])
        from backend.tools.context import ToolContext

        ctx = ToolContext(user_id=user_id)
        try:
            store = mem._get_store(ctx)
        except Exception as exc:  # RuntimeError: fora de uma execução ativa
            import logging

            logging.getLogger(__name__).info(
                "MemoryAdapter.add sem store de agente ativo (user_id=%s): %s",
                user_id,
                exc,
            )
            return None
        import datetime

        from backend.tools.memory import _memory_namespace

        ns = _memory_namespace(ctx)
        value = {
            "key": key,
            "content": content,
            "metadata": {},
            "updated_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "category": None,
        }
        await store.aput(ns, key, value)
        return {"key": key, "content": content}

    async def remove(self, user_id: str, item_id: str) -> None:
        import backend.tools.memory as mem
        from backend.tools.context import ToolContext

        store = mem._get_store(ToolContext(user_id=user_id))
        ns = ("user", user_id, "memories")
        await store.adelete(ns, item_id)
