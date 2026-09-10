"""Configuração de workspace — ``vectora.toml`` e pasta local ``.vectora/``.

Cada workspace (diretório de projeto) pode ter um ``vectora.toml`` na raiz —
arquivo de configuração do projeto, seguro para commitar, editável por
qualquer colaborador. Diferente de ``~/.vectora/settings.json``
(preferências pessoais do usuário) e ``~/.vectora/config.toml``
([server], config admin global).

``ensure_workspace_files()`` cria, de forma idempotente, o ``vectora.toml``
(template comentado) e a pasta ``.vectora/`` (cópias locais de planos,
ignorada pelo git) na primeira vez que uma sessão roda nesse workspace.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

WORKSPACE_CONFIG_FILENAME = "vectora.toml"
WORKSPACE_LOCAL_DIR = ".vectora"

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _default_toml_template(name: str) -> str:
    return f"""# vectora.toml — Configuração do workspace Vectora. Seguro para commitar.
# Edite as seções abaixo para personalizar este workspace.

[workspace]
name = "{name}"
# description = ""

# [storage]
# mode = "complete"                          # "lite" | "complete" — sobrescreve o padrão global
# postgres_dsn = "${{MEUPROJETO_POSTGRES_DSN}}" # nunca credenciais literais
# qdrant_url = "http://localhost:6333"
# qdrant_collection = "meuprojeto_articles"   # isola vetores por workspace
# redis_url = "${{MEUPROJETO_REDIS_URL}}"

# [rag]
# embedding_model = "embed-multilingual-v3.0"
# chunk_size = 1000
# chunk_overlap = 200

# [agent]
# allowed_models = ["claude-sonnet-4-6"]
# default_model = "claude-sonnet-4-6"
# auto_commit = false                        # commit automático após file_write/file_edit

# [hooks]
# post_file_write = ["ruff format {{file}}"]  # {{file}} = path absoluto do arquivo editado
"""


class WorkspaceSection(BaseModel):
    name: str | None = None
    description: str | None = None


class StorageSection(BaseModel):
    mode: str | None = None
    postgres_dsn: str | None = None
    qdrant_url: str | None = None
    qdrant_collection: str | None = None
    qdrant_api_key: str | None = None
    redis_url: str | None = None


class RagSection(BaseModel):
    embedding_model: str | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None


class AgentSection(BaseModel):
    allowed_models: list[str] | None = None
    default_model: str | None = None
    recursion_limit: int | None = None
    #: Opt-in (desligado por default) — commit automático após file_write/
    #: file_edit bem-sucedido. Mensagem gerada deterministicamente a partir
    #: do path editado, não por chamada de LLM (custo/latência extra).
    auto_commit: bool = False


class HooksSection(BaseModel):
    """Comandos shell disparados após uma tool específica rodar.

    ``{file}`` no comando é substituído pelo path absoluto do arquivo
    afetado. Falha de hook nunca propaga pro resultado da tool — só loga.
    """

    post_file_write: list[str] = []


class ComputerUseSection(BaseModel):
    """Controle de mouse/teclado da tela do desktop — opt-in explícito.

    A tool de maior risco do produto (ações físicas fora do sandbox de
    arquivo/terminal); sem esta seção presente e ``enabled = true``, a tool
    recusa antes de tocar no mouse/teclado, mesmo que o agente a chame.
    """

    enabled: bool = False


class WorkspaceConfig(BaseModel):
    """Conteúdo de ``<cwd>/vectora.toml`` (todos os campos opcionais)."""

    workspace: WorkspaceSection = WorkspaceSection()
    storage: StorageSection = StorageSection()
    rag: RagSection = RagSection()
    agent: AgentSection = AgentSection()
    hooks: HooksSection = HooksSection()
    computer_use: ComputerUseSection = ComputerUseSection()


def _resolve_env_placeholders(value: object) -> object:
    """Substitui placeholders ``${VAR}`` por ``os.environ``, recursivamente."""
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _resolve_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_placeholders(v) for v in value]
    return value


def load_workspace_config(cwd: str | Path) -> WorkspaceConfig | None:
    """Lê ``<cwd>/vectora.toml``, resolvendo placeholders ``${VAR}``.

    Retorna ``None`` se o arquivo não existir ou não puder ser interpretado
    (loga um aviso e degrada — config de workspace nunca derruba a sessão).
    """
    path = Path(cwd) / WORKSPACE_CONFIG_FILENAME
    if not path.is_file():
        return None
    try:
        import tomllib

        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        resolved = _resolve_env_placeholders(raw)
        return WorkspaceConfig.model_validate(resolved)
    except Exception:
        logger.warning("workspace_config: falha ao ler %s", path, exc_info=True)
        return None


def ensure_workspace_files(cwd: str | Path, *, name: str | None = None) -> None:
    """Cria ``vectora.toml`` e ``.vectora/`` no workspace, se ainda não existirem.

    Idempotente — não sobrescreve arquivos existentes. Nunca lança: qualquer
    falha é apenas logada, pois isso não pode impedir o início de uma sessão.
    """
    try:
        root = Path(cwd)
        if not root.is_dir():
            return

        toml_path = root / WORKSPACE_CONFIG_FILENAME
        if not toml_path.exists():
            toml_path.write_text(
                _default_toml_template(name or root.name or "workspace"),
                encoding="utf-8",
            )
            logger.info("workspace_config: criado %s", toml_path)

        local_dir = root / WORKSPACE_LOCAL_DIR
        plans_dir = local_dir / "plans"
        if not local_dir.exists():
            plans_dir.mkdir(parents=True, exist_ok=True)
            (local_dir / ".gitignore").write_text("*\n", encoding="utf-8")
            logger.info("workspace_config: criada pasta local %s", local_dir)
        elif not plans_dir.exists():
            plans_dir.mkdir(parents=True, exist_ok=True)

        _ensure_gitignore_entry(root)
    except Exception:
        logger.warning(
            "workspace_config: falha ao criar arquivos do workspace em %s",
            cwd,
            exc_info=True,
        )


def _ensure_gitignore_entry(root: Path) -> None:
    """Acrescenta ``.vectora/`` ao ``.gitignore`` da raiz, se existir e faltar."""
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return
    content = gitignore.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines()]
    if f"!{WORKSPACE_LOCAL_DIR}/skills.lock.json" in lines:
        return
    sep = "" if content.endswith("\n") or not content else "\n"
    has_root_rule = any(line.rstrip("/") == WORKSPACE_LOCAL_DIR for line in lines)
    rules = (
        f"!{WORKSPACE_LOCAL_DIR}/skills.lock.json\n"
        if has_root_rule
        else f"{WORKSPACE_LOCAL_DIR}/\n!{WORKSPACE_LOCAL_DIR}/skills.lock.json\n"
    )
    gitignore.write_text(f"{content}{sep}{rules}", encoding="utf-8")
