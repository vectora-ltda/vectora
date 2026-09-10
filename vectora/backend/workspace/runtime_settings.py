"""RuntimeSettings — Preferências de runtime persistidas em ~/.vectora/checkpoints.db.

Separação clara de responsabilidades:
  ~/.vectora/.env          → segredos (API keys, tokens) — NÃO versionar
  ~/.vectora/checkpoints.db → preferências não-secretas (provider ativo, model,
                              storage_mode, auth_required, nome do usuário local
                              etc.), tabela ``app_settings`` — mesmo SQLite de
                              users/secrets (backend/rbac/auth.py::_get_db()).

Diferente do Settings (pydantic, lido na startup), o RuntimeSettings pode ser
atualizado em tempo de execução via /model, /debug, etc., e as mudanças são
imediatamente persistidas e aplicadas sem reiniciar.

Uso:
    from backend.workspace.runtime_settings import runtime_settings
    runtime_settings.set_active_model("google-genai", "gemini-3.6-flash")
    print(runtime_settings.active_provider)  # "google-genai"

``active_provider``/``active_model`` devolvem string vazia quando o
usuário nunca configurou nada explicitamente (``set_active_model`` nunca
chamado) — nenhum provider/modelo é inventado como default silencioso;
ver ``backend/services/utils.py::load_native_llm`` para o consumidor
principal, que trata string vazia como "nada configurado" e levanta erro
em vez de adivinhar.
"""

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)


def _bootstrap_vectora_home() -> Path:
    """Lê ``VECTORA_HOME`` direto de ``os.environ`` em vez de importar
    ``backend.settings.settings``.

    Este módulo é importado de dentro de
    ``Settings._load_environment_hierarchy`` (import local, para evitar
    ciclo) antes do singleton ``settings`` existir — importar
    ``backend.settings.settings`` aqui levantaria ImportError de módulo
    parcialmente inicializado. Espelha a mesma leitura de
    ``backend.settings._default_vectora_home``.
    """
    env_value = os.environ.get("VECTORA_HOME")
    return Path(env_value) if env_value else Path.home() / ".vectora"


_DB_PATH = _bootstrap_vectora_home() / "checkpoints.db"

#: Sem entrada pra "active_provider"/"active_model" de propósito — um
#: default aqui seria adotado silenciosamente por qualquer chamador
#: antes do usuário escolher provider/modelo nenhum (ver docstring do
#: módulo). `theme`/`language` são cosméticos, sem esse risco.
_DEFAULTS: dict = {
    "theme": "dark",
    "language": "en",
}

# Temas válidos da TUI (ver `src/ui/theme.py`: VECTORA_DARK/LIGHT/SYSTEM).
_VALID_THEMES = ("dark", "light", "system")

# Idiomas válidos da TUI (ver `src/ui/i18n`: csv `key,en,es,pt-BR`).
_VALID_LANGUAGES = ("en", "es", "pt-BR")

# Preferências do frontend web sincronizadas com o backend (fonte de verdade,
# CLAUDE.md §8) — os demais campos do settings-store do frontend continuam só
# em cache local (localStorage), sem persistência cross-device/cross-reinstall.
_ALLOWED_FRONTEND_PREF_KEYS = frozenset(
    {
        "selectedModel",
        "theme",
        "language",
        "chatMode",
        "permissionMode",
        "reasoningEffort",
        "sidebarPosition",
        "autoUpdateEnabled",
        "weeklyInsightEnabled",
        "weeklyInsightWeeks",
        "weeklyInsightDismissedWindow",
    }
)


class RuntimeSettings:
    """Store key-value em SQLite para preferências de runtime não-secretas.

    Thread-safe: leituras batem no cache em memória (lock-free, GIL garante
    atomicidade do dict.get()); escritas usam threading.Lock e persistem no
    SQLite antes de atualizar o cache — evita race condition quando múltiplos
    threads (ex: UI thread + background worker) chamam set() simultaneamente.
    """

    def __init__(self, path: Path = _DB_PATH) -> None:
        self._path = path
        self._data: dict = {}
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        # Mesmos PRAGMAs de rbac/auth.py::_get_db() — WAL + busy_timeout
        # permitem essa conexão síncrona conviver com a conexão aiosqlite
        # assíncrona que o backend abre no mesmo arquivo.
        self._conn.executescript(
            "PRAGMA journal_mode=WAL;PRAGMA busy_timeout=30000;PRAGMA synchronous=NORMAL;"
        )
        self._ensure_schema()
        self._load()

    # ─── I/O ────────────────────────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS app_settings ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        self._conn.commit()

    def _load(self) -> None:
        """Carrega todas as chaves do SQLite pro cache em memória; silencia erros."""
        try:
            rows = self._conn.execute("SELECT key, value FROM app_settings").fetchall()
        except Exception as e:
            logger.warning(
                "runtime_settings: erro ao carregar (%s) — usando defaults", e
            )
            self._data = {}
            return
        data: dict = {}
        for key, raw in rows:
            try:
                data[key] = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning(
                    "runtime_settings: valor inválido pra %r, ignorando", key
                )
        self._data = data
        logger.debug("runtime_settings: carregado de %s", self._path)

    def _persist(self, key: str, value: object) -> None:
        """Grava uma chave no SQLite. Chamar só dentro de `self._lock`."""
        now = datetime.now(UTC).isoformat()
        payload = json.dumps(value, ensure_ascii=False)
        try:
            self._conn.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, payload, now),
            )
            self._conn.commit()
            logger.debug("runtime_settings: salvo %r em %s", key, self._path)
        except Exception:
            logger.exception("runtime_settings: erro ao salvar %r", key)
            try:
                self._conn.rollback()
            except Exception:
                logger.exception(
                    "runtime_settings: falha ao desfazer transação %r", key
                )
            raise

    def reload(self) -> None:
        """Recarrega do SQLite (útil após mudanças externas)."""
        with self._lock:
            self._load()

    # ─── Acesso genérico ─────────────────────────────────────────────────────

    def get(self, key: str, default: object = None) -> object:
        """Retorna valor do cache em memória, com fallback para _DEFAULTS."""
        return self._data.get(key, _DEFAULTS.get(key, default))

    def has(self, key: str) -> bool:
        """True se `key` foi explicitamente persistida — ignora `_DEFAULTS`.

        `get()` sempre cai no default quando a chave não está em `self._data`,
        então nunca é `None`/falsy pra chaves com default — inútil pra
        distinguir "nunca configurado" de "configurado igual ao default".
        """
        return key in self._data

    def set(self, key: str, value: object) -> None:
        """Persiste um valor de forma thread-safe (SQLite + cache em memória)."""
        with self._lock:
            previous = self._data.get(key)
            self._data[key] = value
            try:
                self._persist(key, value)
            except Exception:
                if previous is None:
                    self._data.pop(key, None)
                else:
                    self._data[key] = previous
                raise

    def update_list(
        self, key: str, update: Callable[[list[object]], list[object]]
    ) -> list[object]:
        """Atualiza uma lista sob o mesmo lock da leitura e da persistência."""
        with self._lock:
            raw = self._data.get(key, [])
            current = list(raw) if isinstance(raw, list) else []
            updated = update(current)
            previous = self._data.get(key)
            self._data[key] = updated
            try:
                self._persist(key, updated)
            except Exception:
                if previous is None:
                    self._data.pop(key, None)
                else:
                    self._data[key] = previous
                raise
            return list(updated)

    # ─── Properties tipadas ───────────────────────────────────────────────────

    @property
    def active_provider(self) -> str:
        """Provider ativo, ou string vazia se o usuário nunca escolheu um."""
        return str(self.get("active_provider", ""))

    @property
    def active_model(self) -> str:
        """Modelo ativo, ou string vazia se o usuário nunca escolheu um."""
        return str(self.get("active_model", ""))

    @property
    def theme(self) -> str:
        """Tema ativo da TUI: 'dark' | 'light' | 'system' (ver `src/ui/theme.py`)."""
        raw = str(self.get("theme", "dark"))
        return raw if raw in _VALID_THEMES else "dark"

    @property
    def language(self) -> str:
        """Idioma ativo da TUI: 'en' | 'es' | 'pt-BR' (ver `src/ui/i18n`).

        Resolução em `src/ui/i18n/__init__.py::t()` segue
        `runtime_settings.language` → env `LANG` → "en".
        """
        raw = str(self.get("language", "en"))
        return raw if raw in _VALID_LANGUAGES else "en"

    # ─── Identidade local (nome/empresa) ──────────────────────────────────────

    @property
    def local_user_name(self) -> str:
        return str(self.get("local_user_name", ""))

    @property
    def local_user_company(self) -> str:
        return str(self.get("local_user_company", ""))

    @property
    def local_username(self) -> str:
        """Username escolhido no onboarding local. Vazio = ainda não escolhido
        (`_get_virtual_local_user` cai pro slugify do nome nesse caso)."""
        return str(self.get("local_username", ""))

    def set_local_user(self, name: str, company: str, username: str = "") -> None:
        """Define nome/empresa/username do usuário local (modo sem conta) e persiste."""
        with self._lock:
            self._data["local_user_name"] = name
            self._persist("local_user_name", name)
            self._data["local_user_company"] = company
            self._persist("local_user_company", company)
            if username:
                self._data["local_username"] = username
                self._persist("local_username", username)

    # ─── Auth ─────────────────────────────────────────────────────────────────

    @property
    def auth_required(self) -> bool:
        """False no modo local sem conta (setado por `POST /auth/setup-local`)."""
        return bool(self.get("auth_required", True))

    @auth_required.setter
    def auth_required(self, value: bool) -> None:
        self.set("auth_required", bool(value))

    # ─── Métodos de negócio ───────────────────────────────────────────────────

    @property
    def storage_mode(self) -> str:
        """Modo de storage ativo: 'lite' (SQLite+LanceDB) | 'complete' (Postgres+Qdrant+Redis).

        Sobrepõe `Settings.storage_mode` em runtime (alterado via
        `PATCH /admin/storage`, ver `src/api/handlers/admin.py`).
        """
        raw = str(self.get("storage_mode", "lite"))
        return raw if raw in ("lite", "complete") else "lite"

    @storage_mode.setter
    def storage_mode(self, value: str) -> None:
        self.set("storage_mode", value if value in ("lite", "complete") else "lite")

    def get_service_startup(self, service: str) -> dict:
        """Configuração de auto-start para um serviço self-hosted do modo
        "complete" (``postgres`` | ``redis`` | ``qdrant``).

        Retorna ``{"self_hosted": bool, "start_command": str | None}``. Usado
        por ``POST /admin/storage/test`` (Setup Wizard) para tentar subir o
        serviço localmente quando a conexão falha. Não se aplica a serviços
        terceirizados (Supabase, Upstash, Qdrant Cloud etc.).
        """
        services = self.get("storage_services", {})
        if not isinstance(services, dict):
            return {"self_hosted": False, "start_command": None}
        cfg = cast("dict[str, Any]", services).get(service)
        if not isinstance(cfg, dict):
            return {"self_hosted": False, "start_command": None}
        return {
            "self_hosted": bool(cfg.get("self_hosted", False)),
            "start_command": cfg.get("start_command") or None,
        }

    def set_service_startup(
        self, service: str, *, self_hosted: bool, start_command: str | None
    ) -> None:
        """Persiste a configuração de auto-start de um serviço self-hosted."""
        with self._lock:
            services = self._data.get("storage_services", {})
            if not isinstance(services, dict):
                services = {}
            services[service] = {
                "self_hosted": self_hosted,
                "start_command": start_command,
            }
            self._data["storage_services"] = services
            self._persist("storage_services", services)

    def set_active_model(self, provider: str, model: str) -> None:
        """Troca provider + model ativos e persiste (thread-safe)."""
        with self._lock:
            self._data["active_provider"] = provider
            self._persist("active_provider", provider)
            self._data["active_model"] = model
            self._persist("active_model", model)
        logger.info("runtime_settings: provider=%s model=%s", provider, model)

    def set_theme(self, theme: str) -> None:
        """Define o tema da TUI ('dark'|'light'|'system') e persiste.

        Valores fora de `_VALID_THEMES` caem em 'dark' — mantém o arquivo
        consistente mesmo se um valor inválido chegar via edição manual
        ou de uma versão futura/antiga do app.
        """
        self.set("theme", theme if theme in _VALID_THEMES else "dark")

    def set_language(self, language: str) -> None:
        """Define o idioma da TUI ('en'|'es'|'pt-BR') e persiste.

        Mesma lógica defensiva de `set_theme` — valor inválido cai em 'en'.
        """
        self.set("language", language if language in _VALID_LANGUAGES else "en")

    @property
    def user_timezone(self) -> str:
        """Timezone IANA do usuário (ex.: "America/Sao_Paulo") pro
        agendamento. Vazio = usar o fuso local do SO — sem isso "toda
        segunda às 9h" viraria 9h UTC, um horário diferente do que o
        usuário digitou em qualquer fuso que não o de Greenwich.
        """
        return str(self.get("user_timezone", ""))

    def set_user_timezone(self, tz_name: str) -> None:
        """Define o timezone IANA e persiste. String inválida é rejeitada
        (mantém o valor anterior) — um fuso inexistente gravado silenciosamente
        faria todo agendamento futuro cair no fallback sem o usuário saber.
        """
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        clean = tz_name.strip()
        if clean:
            try:
                ZoneInfo(clean)
            except (ZoneInfoNotFoundError, ValueError):
                logger.warning("runtime_settings: timezone inválido %r ignorado", clean)
                return
        self.set("user_timezone", clean)

    @property
    def fallback_order(self) -> list[str]:
        """Ordem de fallback de providers LLM (lista de 'provider:model')."""
        val = self.get("llm_fallback_order", [])
        return [str(x) for x in val] if isinstance(val, list) else []

    def set_fallback_order(self, order: list[str]) -> None:
        """Define a ordem de fallback de LLM e persiste.

        Filtra entradas vazias/em branco, preserva a ordem fornecida.
        """
        clean = [str(x).strip() for x in order if str(x).strip()]
        self.set("llm_fallback_order", clean)

    @property
    def rag_settings(self) -> dict[str, object]:
        """Settings de RAG configuráveis em runtime (aba de memória).

        Defaults espelham o comportamento atual: reranker ligado, top_k=5,
        providers em "auto" (escolhe por key/fallback), ingestão de todos os tipos.
        """
        val = self.get("rag_settings", {})
        data: dict[str, object] = (
            {str(k): v for k, v in val.items()} if isinstance(val, dict) else {}
        )
        raw_top_k = data.get("reranker_top_k", 5)
        top_k = int(raw_top_k) if isinstance(raw_top_k, (int, float, str)) else 5
        raw_types = data.get("ingest_file_types")
        return {
            "reranker_enabled": bool(data.get("reranker_enabled", True)),
            "reranker_top_k": top_k or 5,
            "rerank_provider": str(data.get("rerank_provider", "auto")),
            "embed_provider": str(data.get("embed_provider", "auto")),
            # Nome do modelo de embedding quando embed_provider força ollama/
            # openrouter — esses dois não têm um default sensato (ao contrário
            # de Cohere/Voyage, que já têm modelo fixo em settings.py), então
            # o usuário escolhe explicitamente (ver storage/factory.py).
            "embed_model": str(data.get("embed_model", "")),
            "ingest_file_types": (
                [str(x) for x in raw_types] if isinstance(raw_types, list) else []
            ),
        }

    def set_rag_settings(self, **changes: object) -> dict[str, object]:
        """Mescla mudanças nos settings de RAG e persiste; devolve o estado final."""
        merged = {
            **self.rag_settings,
            **{k: v for k, v in changes.items() if v is not None},
        }
        self.set("rag_settings", merged)
        return self.rag_settings

    @property
    def media_settings(self) -> dict[str, object]:
        """Modelo escolhido por provider+capacidade de mídia.

        Chaves no formato `{provider}_{capability}_model` (ex.
        `ollama_image_model`) — mesmo nome das env vars correspondentes em
        `settings.py`, para `configured_gateway_model` conseguir procurar nos
        dois lugares com a mesma chave.
        """
        val = self.get("media_settings", {})
        if not isinstance(val, dict):
            return {}
        return {str(k): v for k, v in val.items()}

    def set_media_settings(self, **changes: object) -> dict[str, object]:
        """Mescla e persiste. String vazia **limpa** a escolha (volta a valer a
        env var, se houver) — por isso o filtro é `is not None`, não truthy."""
        merged = {
            **self.media_settings,
            **{k: v for k, v in changes.items() if v is not None},
        }
        self.set("media_settings", merged)
        return self.media_settings

    @property
    def last_session_by_dir(self) -> dict[str, str]:
        """Mapping of working directory path -> thread_id (6-digit string)."""
        val = self.get("last_session_by_dir", {})
        if not isinstance(val, dict):
            return {}
        return {str(k): str(v) for k, v in val.items()}

    def get_session_for_dir(self, cwd: str) -> str | None:
        """Return the last thread_id used in the given directory, or None."""
        return self.last_session_by_dir.get(cwd)

    def set_session_for_dir(self, cwd: str, thread_id: str) -> None:
        """Persist the last thread_id used in the given directory."""
        with self._lock:
            mapping = dict(self.last_session_by_dir)
            mapping[cwd] = thread_id
            self._data["last_session_by_dir"] = mapping
            self._persist("last_session_by_dir", mapping)

    def get_frontend_prefs(self, user_id: str) -> dict[str, object]:
        """Preferências do frontend web persistidas para ``user_id``.

        Chave por usuário — numa instância multi-usuário (VPS), as
        preferências de um usuário não vazam pra outro.
        """
        all_prefs = self.get("frontend_prefs", {})
        if not isinstance(all_prefs, dict):
            return {}
        user_prefs = all_prefs.get(user_id)
        if not isinstance(user_prefs, dict):
            return {}
        return {str(k): v for k, v in user_prefs.items()}

    def set_frontend_prefs(
        self, user_id: str, changes: dict[str, object]
    ) -> dict[str, object]:
        """Mescla ``changes`` nas preferências do frontend de ``user_id`` e persiste.

        Chaves fora de ``_ALLOWED_FRONTEND_PREF_KEYS`` são ignoradas
        silenciosamente (forward-compat — um frontend mais novo pode mandar
        campos que este backend ainda não reconhece). Devolve o estado final
        mesclado do usuário.
        """
        allowed = {k: v for k, v in changes.items() if k in _ALLOWED_FRONTEND_PREF_KEYS}
        if "weeklyInsightEnabled" in allowed and not isinstance(
            allowed["weeklyInsightEnabled"], bool
        ):
            allowed.pop("weeklyInsightEnabled")
        if "weeklyInsightWeeks" in allowed and (
            isinstance(allowed["weeklyInsightWeeks"], bool)
            or allowed["weeklyInsightWeeks"] not in {1, 2, 4}
        ):
            allowed.pop("weeklyInsightWeeks")
        if "weeklyInsightDismissedWindow" in allowed and not isinstance(
            allowed["weeklyInsightDismissedWindow"], str
        ):
            allowed.pop("weeklyInsightDismissedWindow")
        with self._lock:
            all_prefs_raw = self._data.get("frontend_prefs", {})
            all_prefs: dict[str, object] = (
                {str(k): v for k, v in all_prefs_raw.items()}
                if isinstance(all_prefs_raw, dict)
                else {}
            )
            existing = all_prefs.get(user_id)
            user_prefs: dict[str, object] = (
                {str(k): v for k, v in existing.items()}
                if isinstance(existing, dict)
                else {}
            )
            user_prefs.update(allowed)
            all_prefs[user_id] = user_prefs
            self._data["frontend_prefs"] = all_prefs
            self._persist("frontend_prefs", all_prefs)
        return user_prefs


# Singleton — único por processo
runtime_settings = RuntimeSettings()


# ── Orquestração de troca de modelo ──────────────────────────────────────────


def apply_model_change(provider: str, model: str) -> None:
    """Aplica troca de provider/model: SQLite + os.environ + Settings em memória + singletons LLM."""
    from backend.settings import PROVIDER_MODEL_ENV, settings

    # 1. Persiste no SQLite (app_settings)
    runtime_settings.set_active_model(provider, model)

    # 2. Atualiza os.environ (efeito imediato para load_llm())
    os.environ["LLM_PROVIDER"] = provider
    if env_var := PROVIDER_MODEL_ENV.get(provider):
        os.environ[env_var] = model

    # 3. Atualiza o singleton Settings em memória
    try:
        settings.llm_provider = provider  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
        settings.set_model(provider, model)
    except Exception as e:
        logger.warning("Erro ao atualizar settings singleton: %s", e)

    logger.info("Model aplicado: provider=%s model=%s", provider, model)
