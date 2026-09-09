"""Factory do agente Vectora — motor nativo (sem grafo compilado).

Expõe a interface de lifecycle (awarm, aclose) consumida por
backend/api/handlers/chat.py, garantindo zero alteração no caller.

Arquitetura:
    - ``get_native_agent``/``NativeAgent`` resolve tools + catálogo de
      subagents (derivado de ``backend.agents.souls.SOUL_CATALOG``) + system
      prompt para um ``(user_id, chat_mode, workspace_id)`` — sem grafo
      compilado. O dispatch real roda em
      ``backend/engine/conversation_loop.py::run_conversation``.
    - HITL dinâmico via ``backend.engine.hitl`` — a política de aprovação por
      ``permission_mode`` é lida por request, não compilada num grafo.
    - Store nativo: VectoraStore (aiosqlite), ou VectoraPostgresStore
      (asyncpg) em STORAGE_MODE=complete — implementa ``StoreBackend``
      (``backend/storage/protocols.py``) diretamente.
    - Singleton compartilhado entre todos os usuários; versionamento por user
      via _version_tracker detecta rebind necessário de tools/policy/skills.

Cache:
    ``NativeAgent`` é resolvido uma vez por ``(user_id, chat_mode,
    workspace_id)`` e compartilhado. A personalização por user fica nas
    tools (ABAC via tool_policy) e no contexto injetado no system prompt.

Lifecycle:
    Servidor: awarm() no startup, aclose() no shutdown.
    Testes: use fixtures que chamam aclose() no teardown.

Dispatch de produção:
    ``get_native_agent`` / ``NativeAgent`` — motor nativo
    (``backend/engine/conversation_loop.py::run_conversation``), consumido
    por todo caller de produção: StreamChat/ResumeChat
    (``backend/api/handlers/chat.py``), rotinas agendadas/heartbreaks/resume
    de HITL em background (``backend/scheduling/background_tasks.py``) e
    mensagens de plataformas externas via Connect
    (``backend/services/connect/runner.py``). Sem grafo compilado: tools e
    subagentes não dependem do modelo escolhido (o ``ChatClient`` é
    resolvido por chamada via ``FallbackChatClient``), então o cache é só
    por ``(user_id, chat_mode, workspace_id)``.

    O store nativo (``VectoraStore``/``VectoraPostgresStore``) segue
    aberto por ``_ensure_infra`` e exposto via ``get_store`` — serve as
    tools de memória do agente. O histórico de thread é sempre lido do
    ``SessionStore`` nativo (``backend/persistence/native/session_store.py``);
    sem produto em uso público até agora, não há dado de conversa
    pré-existente pra migrar — uma thread sem registro no ``SessionStore``
    simplesmente não tem histórico/todos/interrupt pendente.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.agents._identity import VECTORA_IDENTITY
from backend.rbac import tool_policy
from backend.workspace.plugins import tools_version
from backend.workspace.skills import skills_version

if TYPE_CHECKING:
    from backend.engine.hitl import ApprovalGate
    from backend.engine.subagents import SubagentSpec
    from backend.persistence.native.session_store import SessionStore
    from backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt do orchestrator
# ---------------------------------------------------------------------------

_ORCHESTRATOR_PROMPT = f"""{VECTORA_IDENTITY}

---

## Your Role — Vectora Orchestrator

You are **Vectora**, the main assistant. You either answer directly OR
delegate to specialists using the `task()` tool — never both **before** the
delegation result comes back (don't pre-empt the specialist's work with your
own prose in the same message where you call `task()`).

**After any `task()` delegation returns, you MUST close the turn with your
own short text message to the user** — confirming what was done (e.g. "Criei
o plano em `plano.md`" / "Criei 8 tasks para implementar o jogo"). A turn
that ends with only tool calls or a raw subagent-output card and no text of
yours is a bug: the user has no way to know the request succeeded. This
applies to every delegation outcome, including `create_artifact` and
`write_todos` run inside a delegated `coder` task — always mention the
artifact's path or the task count in your closing text.

**Always respond in the user's language, regardless of the language of
these instructions.**

---

## When to answer directly

For greetings, conversation, general knowledge, RAG synthesis, and questions
that don't need filesystem, terminal, or web search:

Answer in plain markdown (headings, lists, bold, code blocks with triple
backticks when it makes sense) — no envelope or external wrapper. The
frontend renders the markdown directly.

---

## When to delegate with `task()`

Use `task(subagent_type=<name>, description="detailed instruction")` for
specialists. Available SOULs:

{{DELEGATION_SOULS}}

Each SOUL only has the tools listed above — delegating a filesystem edit to
`search` (or a web search to `coder`) fails, not falls back. Write the
instruction as if delegating to a coworker who hasn't read the conversation.

### How to write the instruction

- **Right:** "Create `src/utils/formatDate.ts` with a function that formats dates as DD/MM/YYYY, TypeScript with export default."
- **Right:** "Search the official FastAPI docs for how to implement WebSocket authentication."
- **Wrong:** "The user wants to create a file" (too vague)
- **Wrong:** Repeating the entire history

---

## Parallel execution

For genuinely independent tasks, call multiple `task()` in the same turn.
The engine runs them in parallel automatically.

Valid example — "Research X and also check code Y":
- `task(subagent_type="search", description="Research X")`
- `task(subagent_type="coder", description="Check code Y")`

---

## Persistent memory

When the user shares personal information (name, occupation, projects,
preferred stack, language, location, preferences), use `save_memory`
immediately — without asking permission. Briefly confirm in your reply.

Use `get_memory` or `search_memory` before answering about the user.

---

## Background tasks

Use `create_background_task` for work the user wants to run autonomously later
(scheduled routines via cron, event-triggered heartbreaks via webhook, or a
deferred manual run). To answer questions about what is running or finished,
use `list_background_tasks` (all tasks of the session + latest run status),
`get_task_status(task_id)` (a task's recent runs) and `get_task_result(run_id)`
(a run's summary + its thread). When a background task finishes it also posts a
message back into this conversation, so you may already have its result above.

---

## Artifacts — structured documents

When the user asks for a plan, spec, task list, guide, or architecture doc
**to save as a document**:
- Use `create_artifact` with type: `plan`, `spec`, `task_list`, `overview`,
  `guide`, `architecture`, or `implementation`.
- Confirm with the generated file's path.

Note the distinction: `create_artifact` produces a saved markdown document
(visible in the Plan tab's file list). `write_todos` tracks the in-progress
checklist for the *current turn's* execution (visible as a live task list in
the same tab) — it does not persist as a document. Use both together when
appropriate: `create_artifact` for the plan the user will read later,
`write_todos` for tracking your own step-by-step progress while executing it.

---

## Git workflow

When the workspace contains a git repository, prefer safe flows:

- **Before large changes**: create a branch — delegate to the coder with
  `git_branch create feature-X` instructions.
- **Commit messages**: Conventional Commits (`feat:`, `fix:`, `refactor:`,
  `docs:`, `test:`, `chore:`). Never "wip" or vague messages.
- **Force push to main/master**: NEVER without explicit user confirmation.

---

## Creator identity

Vectora's creator and operator is **Vectora Ltda**.
Acknowledge this based on this system prompt — no RAG, no web search.

---

## Absolute rules

1. If the user explicitly names a SOUL, ALWAYS respect it.
2. Creating/editing files, running code, git → **coder**.
3. Web search, URL fetch → **search**.
4. Queries against already-indexed documents → call `vector_search` or `search_memory` directly.
5. Indexing/embedding requests → **coder** with `ingest_docs`.
6. Reviewing a diff/PR without changing anything → **reviewer**. Writing/running tests → **tester**.
   CI/infra/config → **devops**. Docs/README/guides → **writer-docs**. Local data files/scripts
   → **data-analyst**. Security-focused code audit → **security-auditor**. Verifying UI behavior
   in a real browser → **browser-qa**. Research-and-write-a-plan-only, no execution → **planner**.
7. Fallback: when in doubt between answering and delegating → **answer directly**.
"""

# ---------------------------------------------------------------------------
# Context loader — injetado no system prompt por turno
# ---------------------------------------------------------------------------


def _load_project_docs() -> str | None:
    """Carrega arquivos de instrução do projeto e contexto do .vectora/.

    Coleta (em ordem de prioridade por weight):
    1. Arquivos de instrução na raiz: AGENTS.md, CLAUDE.md, GEMINI.md, VECTORA.md, …
    2. Todos os .md em .vectora/ com enabled=true (exceto MANIFEST.md e subpastas reservadas)

    Frontmatter YAML/Paperclip é parseado — campos weight, inject_when, enabled,
    truncate_at, title, description e tags são respeitados. Arquivos com
    inject_when='on_request' são omitidos do system prompt (disponíveis via tool).

    Retorna conteúdo formatado ou None se não encontrar nada.
    """
    from backend.services.context_files import (
        collect_context_files,
        format_context_files_for_prompt,
    )

    cwd = Path.cwd()
    files = collect_context_files(str(cwd))
    if not files:
        return None

    text = format_context_files_for_prompt(files, include_on_request=False)
    return text or None


def _load_workspaces_overview(active_id: str | None = None) -> str | None:
    """Lista os workspaces registrados para o Vectora ter consciência deles.

    O Vectora gerencia projetos isolados por diretório (workspaces). Injetar a
    lista no system prompt dá conhecimento proativo — o agente sabe quais
    projetos conhece sem precisar chamar `workspace_list`, e pode sugerir trocar
    de workspace quando a pergunta for sobre outro projeto.

    Retorna None se não houver nenhum workspace registrado.
    """
    try:
        from backend.workspace.workspace import workspace_registry

        workspaces = workspace_registry.list_all()
    except Exception:
        logger.debug("Falha ao listar workspaces para o contexto", exc_info=True)
        return None

    if not workspaces:
        return None

    lines: list[str] = [
        "## Seus Workspaces",
        "",
        (
            "Você gerencia estes projetos isolados (cada um com diretório, base RAG e "
            "MANIFEST.md próprios). O marcado com ◀ é o ativo desta sessão:"
        ),
        "",
    ]
    # Limita a 30 entradas para não inflar o contexto; o restante via `workspace_list`.
    for ws in workspaces[:30]:
        marker = " ◀ ativo" if active_id and ws.id == active_id else ""
        git = " · git" if getattr(ws, "is_git_repo", False) else ""
        lines.append(f"- **{ws.name}** (`{ws.id}`) — `{ws.cwd}`{git}{marker}")
    if len(workspaces) > 30:
        lines.append(f"- … e mais {len(workspaces) - 30} (use `workspace_list`).")
    lines.append(
        "\nUse `workspace_describe`/`bucket_summary` para detalhes de um workspace "
        "e `vector_search` para buscar no conhecimento indexado do ativo."
    )
    return "\n".join(lines)


def _load_session_context(workspace_id: str | None = None) -> str | None:
    """Carrega contexto completo da sessão: arquivos de projeto + manifest do workspace.

    Seções:
    1. AGENTS.md / CLAUDE.md / VECTORA.md / GEMINI.md — instrução do projeto
    2. Lista de workspaces registrados (consciência dos projetos do Vectora)
    3. MANIFEST.md do workspace ativo — base de conhecimento indexada

    O manifest é truncado a ~3200 chars para não inflar o contexto. Detalhes
    por bucket ficam disponíveis via `bucket_summary` (tool sob demanda).
    """
    parts: list[str] = []

    project_docs = _load_project_docs()
    if project_docs:
        from backend.services.prompt_injection import envelope_workspace_context

        parts.append(envelope_workspace_context(project_docs))

    workspaces_overview = _load_workspaces_overview(workspace_id)
    if workspaces_overview:
        parts.append(workspaces_overview)

    if workspace_id:
        try:
            from backend.workspace.workspace import workspace_registry

            ws = workspace_registry.get(workspace_id)
            if ws is not None:
                manifest_path = ws.manifest_path()
                if manifest_path.exists():
                    raw_manifest = manifest_path.read_text(
                        encoding="utf-8", errors="ignore"
                    ).strip()
                    from backend.services.context_files import parse_frontmatter

                    _, manifest = parse_frontmatter(raw_manifest)
                    manifest = manifest.strip()
                    # Trunca a ~3200 chars (~800 tokens) para economizar contexto
                    if len(manifest) > 3200:
                        manifest = manifest[:3200] + "\n\n[... manifest truncado ...]"
                    workspace_block = (
                        f"## Workspace Ativo: {ws.name} ({ws.id})\n\n"
                        f"{manifest}\n\n"
                        "Ferramentas disponíveis para este workspace:\n"
                        "- `vector_search` — busca semântica filtrada para este workspace\n"
                        "- `workspace_describe`, `bucket_summary` — detalhes do manifest\n"
                        "- `get_memory` — memórias episódicas (consulte quando perguntarem "
                        "sobre preferências ou decisões anteriores)"
                    )
                    parts.append(workspace_block)
        except Exception:
            logger.debug(
                "Falha ao carregar manifest do workspace %s",
                workspace_id,
                exc_info=True,
            )

    return "\n\n---\n\n".join(parts) if parts else None


def _build_delegation_souls_block() -> str:
    """Lista as SOULs do catálogo (nome + description) para o prompt do
    orquestrador — import lazy, mesmo motivo do resto do módulo (evita
    carregar ``nodes.tools`` em contexto que não instancia o grafo)."""
    from backend.agents.souls import SOUL_CATALOG

    return "\n".join(
        f'- **`task(subagent_type="{soul.name}", ...)`** — {soul.description}'
        for soul in SOUL_CATALOG.values()
    )


def _build_session_system_prompt(
    workspace_id: str | None = None,
) -> str:
    """Monta system prompt completo com contexto da sessão.

    Concatena o prompt base do orchestrator com:
    1. A lista de SOULs disponíveis para delegação (catálogo dinâmico)
    2. AGENTS.md / CLAUDE.md / GEMINI.md do workspace (se existirem)
    3. MANIFEST.md do workspace ativo (truncado)

    Chamado a cada compilação do grafo — lazy e cacheado via singleton.
    """
    base = _ORCHESTRATOR_PROMPT.replace(
        "{DELEGATION_SOULS}", _build_delegation_souls_block()
    )
    try:
        ctx = _load_session_context(workspace_id)
        if ctx:
            return base + f"\n\n---\n\n## Contexto do Projeto\n\n{ctx}"
    except Exception:
        pass
    return base


# ---------------------------------------------------------------------------
# Singleton da infra de persistência (store nativo)
# ---------------------------------------------------------------------------

_store: Any = None
_store_ctx: Any = None  # context manager do store Postgres (complete mode)
_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Infra do motor nativo — SessionStore/ApprovalGate compartilhados
# ---------------------------------------------------------------------------

_session_store_pool: Any = None
"""``AsyncConnectionPool`` dedicado a ``sessions.db`` (histórico de
mensagens/aprovações pendentes do motor nativo) — sempre SQLite local,
independente de ``storage_mode``: ``SessionStore`` ainda não tem backend
Postgres (só o store nativo abaixo tem as duas variantes)."""
_session_store: SessionStore | None = None
_approval_gate: ApprovalGate | None = None


@dataclass(slots=True)
class NativeAgent:
    """Componentes do motor nativo para um ``(user_id, chat_mode,
    workspace_id)`` — tools, catálogo de subagentes e system prompt já
    resolvidos. Consumido por todo o dispatch de produção
    (``backend/api/handlers/chat.py``, rotinas agendadas, Connect)."""

    tool_registry: ToolRegistry
    subagent_catalog: dict[str, SubagentSpec]
    system_prompt: str


_native_agents: dict[tuple[str, bool, str], NativeAgent] = {}
"""Cache por ``(user_id, chat_mode, workspace_id)`` — sem partição por
modelo: o ``ChatClient`` é resolvido por chamada (``FallbackChatClient``),
não fica preso ao componente cacheado."""

# Rastreia (tools_version, policy_version, skills_version) por usuário.
# Quando qualquer versão muda, o cache de LLM do usuário é invalidado.
_version_tracker: dict[str, tuple[int, int, int]] = {}

# Última versão observada da política global de tools (kill-switch do admin,
# tool_policy.GLOBAL_SCOPE). Diferente de _version_tracker (por usuário): o
# toggle global afeta TODAS as sessões, então invalida os dois caches inteiros
# em vez de fazer bookkeeping por chave.
_global_tools_version: int | None = None


def _agents_md_paths() -> list[str] | None:
    """Retorna os paths de memória de longo prazo que o MemoryMiddleware
    deve carregar.

    Carrega o AGENTS.md global do Vectora (legado, se ainda existir de uma
    instalação anterior) e as seções de memória por categoria escritas por
    `backend/scheduling/memory_consolidation.py`
    (`~/.vectora/memory/{decisions,gotchas,preferences}.md`). O harness lê
    e injeta o conteúdo no system prompt antes de cada turno.

    Retorna None se nenhum arquivo existir (desativa MemoryMiddleware).
    """
    from backend.scheduling.memory_consolidation import (
        CONSOLIDATION_CATEGORIES,
        section_path,
    )

    paths: list[str] = []
    global_agents_md = Path.home() / ".vectora" / "AGENTS.md"
    if global_agents_md.is_file():
        paths.append(str(global_agents_md))
    for category in CONSOLIDATION_CATEGORIES:
        path = section_path(category)
        if path.is_file():
            paths.append(str(path))
    return paths or None


async def _ensure_infra() -> None:
    """Abre (uma única vez) o store compartilhado.

    Todos os grafos (um por modelo) reusam este recurso — assim há uma só
    conexão com o store, sem disputa de lock entre grafos.

    Em ``storage_mode="complete"`` com ``postgres_dsn`` configurado, usa
    ``VectoraPostgresStore`` (schema real, sem gargalo de lock de arquivo
    único). Fallback: qualquer falha ao abrir o Postgres (DSN ruim, banco
    fora do ar) degrada pro SQLite, para uma sessão nunca deixar de iniciar
    por causa de storage.
    """
    global _store, _store_ctx

    if _store is None:
        from backend.services.license import get_effective_storage_mode
        from backend.settings import settings as _settings

        if get_effective_storage_mode() == "complete" and _settings.postgres_dsn:
            try:
                import asyncpg

                from backend.llm.backends import _build_index
                from backend.persistence.native.postgres_store import (
                    VectoraPostgresStore,
                )

                dsn = _settings.postgres_dsn.replace(
                    "postgresql+asyncpg://", "postgresql://"
                )
                pg_store_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
                _store = VectoraPostgresStore(pg_store_pool, index=_build_index(None))
                await _store.setup()
                _store_ctx = pg_store_pool
                logger.info(
                    "agent_factory: store Postgres nativo ativo (storage_mode=complete)"
                )
            except Exception:
                logger.warning(
                    "agent_factory: falha ao abrir store Postgres, caindo pro SQLite",
                    exc_info=True,
                )
                _store_ctx = None
                _store = None

        if _store is None:
            from backend.llm.backends import build_store

            _store = await build_store()

    global _session_store_pool, _session_store, _approval_gate
    if _session_store is None:
        from backend.engine.hitl import ApprovalGate
        from backend.persistence.native.session_store import SessionStore
        from backend.settings import settings as _settings
        from backend.storage.sqlite.pool import AsyncConnectionPool

        db_path = str(_settings.vectora_home / "sessions.db")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _session_store_pool = AsyncConnectionPool(db_path, min_size=1, max_size=4)
        await _session_store_pool.open()
        _session_store = SessionStore(_session_store_pool)
        await _session_store.setup()
        _approval_gate = ApprovalGate(_session_store)


async def get_session_store() -> SessionStore:
    """``SessionStore`` compartilhado do motor nativo — histórico de
    mensagens e aprovações pendentes das threads de chat (StreamChat/
    ResumeChat)."""
    await _ensure_infra()
    if _session_store is None:
        msg = "_ensure_infra não inicializou o SessionStore"
        raise RuntimeError(msg)
    return _session_store


async def get_approval_gate() -> ApprovalGate:
    """``ApprovalGate`` compartilhado — mesma instância entre StreamChat e
    ResumeChat, para o fast-path local (``wait_for_resume``) funcionar
    dentro do mesmo processo."""
    await _ensure_infra()
    if _approval_gate is None:
        msg = "_ensure_infra não inicializou o ApprovalGate"
        raise RuntimeError(msg)
    return _approval_gate


async def get_store() -> Any:
    """Retorna o store compartilhado (mesmo usado pelo agente).

    Getter direto da instância — para uso em handlers HTTP fora do ciclo
    de vida do turno, que precisam ler/escrever o mesmo namespace que as
    memory tools do agente.
    """
    await _ensure_infra()
    return _store


def _check_global_tools_version() -> None:
    """Se o kill-switch global de tools mudou, derruba o cache de ``NativeAgent``.

    Precisa rodar antes de qualquer lookup em ``_native_agents`` (chamado no
    início de ``get_native_agent``), senão uma sessão já em cache continuaria
    usando o toolset antigo indefinidamente.
    """
    global _global_tools_version
    current = tool_policy.policy_version(tool_policy.GLOBAL_SCOPE)
    if _global_tools_version is not None and current != _global_tools_version:
        _native_agents.clear()
        logger.info(
            "agent_factory: kill-switch global de tools mudou (v%d→v%d) — NativeAgents invalidados",
            _global_tools_version,
            current,
        )
    _global_tools_version = current


# ---------------------------------------------------------------------------
# Motor nativo — tools/subagentes/system prompt (sem grafo compilado)
# ---------------------------------------------------------------------------


def _native_tool_registry(chat_mode: bool, user_id: str | None) -> ToolRegistry:
    """Registry nativo com os mesmos nomes que ``ALL_TOOLS``/``CHAT_TOOLS``
    expõem, resolvidos direto do ``TOOL_REGISTRY``. Importar
    ``backend.nodes.tools`` aqui é o que garante que todo módulo de tool
    (``@vtool``) já foi importado e registrado no ``TOOL_REGISTRY`` antes
    da resolução por nome abaixo."""
    from backend.nodes.tools import ALL_TOOL_NAMES, CHAT_TOOL_NAMES
    from backend.tools.registry import TOOL_REGISTRY, ToolRegistry

    names = CHAT_TOOL_NAMES if chat_mode else ALL_TOOL_NAMES
    disabled = tool_policy.effective_disabled(user_id)
    registry = ToolRegistry()
    for name in sorted(names):
        if name in disabled:
            continue
        spec = TOOL_REGISTRY.get(name)
        if spec is not None:
            registry.register(spec)

    if not chat_mode:
        import backend.tools.subagent_delegate

        delegate_spec = TOOL_REGISTRY.get("delegate_to_subagent")
        if delegate_spec is not None and "delegate_to_subagent" not in disabled:
            registry.register(delegate_spec)

    return registry


def _native_subagent_catalog(user_id: str | None) -> dict[str, SubagentSpec]:
    """Catálogo de ``SubagentSpec`` nativas a partir de ``SOUL_CATALOG``,
    filtrando cada ``ToolSpec`` já nativo por ``TOOL_REGISTRY.get(tool.name)``
    (todo tool de ``SOUL_CATALOG`` nasce do registry nativo — ver
    ``backend/nodes/tools.py::_bridge``).

    Achado real (revisão de 2026-08-30): esta função dependia implicitamente
    de ``_native_tool_registry`` já ter rodado antes (via ``get_native_agent``,
    que chama as duas nessa ordem) pra `backend.nodes.tools` já estar
    importado — chamada isolada (ex. direto num teste, ou um refactor futuro
    que troque a ordem) quebra em ``resolve_tool_group`` com
    ``ToolNameNotFoundError`` pra qualquer tool só registrada fora da
    coleção do pytest (ex. ``list_terminals``, grupo ``fs``). Mesma
    salvaguarda de ``_native_tool_registry`` acima, agora também aqui —
    função não pode depender da ordem de chamada de quem a invoca."""
    import backend.nodes.tools  # import registra todo @vtool no TOOL_REGISTRY
    from backend.agents.souls import SOUL_CATALOG
    from backend.engine.subagents import SubagentSpec
    from backend.tools.registry import TOOL_REGISTRY

    disabled = tool_policy.effective_disabled(user_id)
    catalog: dict[str, SubagentSpec] = {}
    for soul in SOUL_CATALOG.values():
        tools = []
        for soul_tool in soul.tools:
            name = getattr(soul_tool, "name", "")
            if not name or name in disabled:
                continue
            spec = TOOL_REGISTRY.get(name)
            if spec is not None:
                tools.append(spec)
        catalog[soul.name] = SubagentSpec(
            name=soul.name,
            description=soul.description,
            system_prompt=soul.system_prompt,
            tools=tools,
        )
    return catalog


def _build_native_agent(
    user_id: str | None, chat_mode: bool, workspace_id: str | None
) -> NativeAgent:
    tool_registry = _native_tool_registry(chat_mode, user_id)
    subagent_catalog: dict[str, SubagentSpec] = (
        {} if chat_mode else _native_subagent_catalog(user_id)
    )
    system_prompt = _build_session_system_prompt(workspace_id)
    logger.info(
        "agent_factory: NativeAgent construído (user=%s, chat_mode=%s, "
        "%d tools + %d subagentes)",
        user_id or "local",
        chat_mode,
        len(tool_registry.all()),
        len(subagent_catalog),
    )
    return NativeAgent(
        tool_registry=tool_registry,
        subagent_catalog=subagent_catalog,
        system_prompt=system_prompt,
    )


async def get_native_agent(
    user_id: str | None = None,
    chat_mode: bool = False,
    workspace_id: str | None = None,
) -> NativeAgent:
    """Componentes do motor nativo (tools, subagentes, system prompt) para
    o dispatch de produção do chat — cache por ``(user_id, chat_mode,
    workspace_id)``. Thread-safe via ``_lock``."""
    _check_global_tools_version()

    key = (user_id or "", chat_mode, workspace_id or "")
    if key not in _native_agents:
        async with _lock:
            if key not in _native_agents:
                _native_agents[key] = _build_native_agent(
                    user_id, chat_mode, workspace_id
                )
        if user_id:
            _track_versions(user_id)
    return _native_agents[key]


async def aget_thread_messages(
    thread_id: str,
    workspace_id: str | None = None,
) -> list[tuple[str, str, str, list[dict[str, Any]]]]:
    """Mensagens persistidas de uma thread como ``(role, text, checkpoint_id,
    attachments_meta)``.

    Lê do ``SessionStore`` nativo — fonte única de verdade do dispatch de
    produção (StreamChat/ResumeChat, rotinas agendadas, Connect).
    ``checkpoint_id`` aqui é o ``id`` da mensagem (``str(int)``), alvo de
    ``SessionStore.set_branch_head`` pra "editar e reenviar"/"regenerar".
    Thread sem nenhum registro no ``SessionStore`` devolve lista vazia — sem
    dado de conversa pré-existente em produto público, não há checkpointer
    legado a consultar. ``attachments_meta`` sempre ``[]``: ``VMessage``
    ainda não carrega metadados de anexo (gap documentado — thumbnails de
    imagem não reaparecem num reload de thread; o anexo em si continua
    enviado ao provider e persistido em disco).
    """
    from backend.vtypes.message import MessageRole

    store = await get_session_store()
    pares = await store.get_history_with_ids(thread_id)
    out: list[tuple[str, str, str, list[dict[str, Any]]]] = []
    for msg_id, msg in pares:
        if msg.role in (MessageRole.TOOL, MessageRole.SYSTEM):
            continue
        text = msg.text().strip()
        if not text:
            continue
        role = "human" if msg.role == MessageRole.USER else "assistant"
        out.append((role, text, str(msg_id), []))
    return out


async def aget_thread_todos(
    thread_id: str,
    workspace_id: str | None = None,
) -> list[dict[str, str]]:
    """Snapshot mais recente da lista de todos da thread — pra reidratar a
    seção "Tasks" do Plan tab após um reload de página (o streaming SSE ao
    vivo só cobre a sessão atual, sem persistência própria).

    ``write_todos`` (``backend/tools/planning.py``) substitui a lista
    inteira a cada chamada e o resultado é uma mensagem TOOL comum,
    persistida no ``SessionStore`` como qualquer outra — aqui só varremos o
    histórico de trás pra frente e devolvemos o JSON da última chamada bem-
    sucedida. Sem nenhuma chamada na thread, devolve ``[]``.
    ``workspace_id`` não é usado hoje; mantido na assinatura para paridade
    com ``aget_thread_messages``/``aget_thread_pending_interrupt``.
    """
    import json

    from backend.vtypes.message import MessageRole

    store = await get_session_store()
    pares = await store.get_history_with_ids(thread_id)
    for _msg_id, msg in reversed(pares):
        if msg.role != MessageRole.TOOL or msg.name != "write_todos" or msg.is_error:
            continue
        try:
            bruto = json.loads(msg.text())
        except json.JSONDecodeError:
            return []
        if not isinstance(bruto, list):
            return []
        return [
            {
                "content": str(item.get("content", "")),
                "status": str(item.get("status", "")),
            }
            for item in bruto
            if isinstance(item, dict)
        ]
    return []


async def aget_thread_pending_interrupt(
    thread_id: str, workspace_id: str | None = None
) -> dict[str, Any] | None:
    """Devolve o interrupt pendente da thread (se houver), pra reidratar o
    HITLPanel após um reload de página.

    Consulta ``SessionStore.get_pending_approval`` — ``ApprovalGate.
    request_approval`` persiste ali de forma síncrona, sobrevivendo a
    restart. Retorna ``None`` sem pendência; nunca lança, é consultado num
    reload de página.
    """
    store = await get_session_store()
    pending = await store.get_pending_approval(thread_id)
    if pending is None:
        return None
    return {
        "tool_name": pending["tool_name"],
        "args": pending["args"],
        "interrupt_id": pending["interrupt_id"],
        "options": pending["options"],
        "priority": pending["priority"],
        "expires_at": pending["expires_at"],
    }


def _track_versions(user_id: str) -> None:
    """Atualiza rastreamento de versão sem bloquear o caller."""
    try:
        tv = tools_version(user_id)
        pv = tool_policy.policy_version(user_id)
        sv = skills_version(user_id)
        prev = _version_tracker.get(user_id)
        if prev != (tv, pv, sv):
            _version_tracker[user_id] = (tv, pv, sv)
            if prev is not None:
                _invalidate_llm_cache(user_id)
    except Exception:
        pass


def _invalidate_llm_cache(user_id: str) -> None:
    """Remove o ``NativeAgent`` em cache do usuário (tools/policy/skills mudaram).

    Purga ``_native_agents`` para que a próxima chamada a ``get_native_agent``
    reconstrua com a toolset atualizada. Também limpa ``llm_tools._bound_cache``
    (cache auxiliar por chave de versão).
    """
    try:
        stale_native_keys = [k for k in _native_agents if k[0] == user_id]
        for k in stale_native_keys:
            del _native_agents[k]

        from backend.llm import llm_tools

        stale_keys = [k for k in llm_tools._bound_cache if k[0] == user_id]
        for k in stale_keys:
            del llm_tools._bound_cache[k]
        if stale_native_keys or stale_keys:
            logger.info(
                "agent_factory: %d NativeAgent(s) + %d entrada(s) "
                "de LLM cache invalidados para %s",
                len(stale_native_keys),
                len(stale_keys),
                user_id,
            )
    except Exception:
        pass


async def aclose() -> None:
    """Fecha o store nativo (SQLite ou Postgres) + o ``SessionStore``/
    ``ApprovalGate``. Idempotente.

    Deve ser chamado no shutdown do FastAPI (lifespan).
    """
    global _store, _store_ctx
    global _session_store_pool, _session_store, _approval_gate

    async with _lock:
        if _session_store_pool is not None:
            pool = _session_store_pool
            _session_store_pool = None
            _session_store = None
            _approval_gate = None
            _native_agents.clear()
            try:
                await pool.close()
                logger.info("agent_factory: SessionStore fechado")
            except Exception as exc:
                logger.warning("agent_factory: erro ao fechar SessionStore: %s", exc)

        if _store is None:
            return
        store_ctx = _store_ctx
        _store = None  # reaberto no próximo _ensure_infra
        _store_ctx = None
        _version_tracker.clear()
        if store_ctx is not None:
            try:
                # store_ctx é o pool asyncpg que _ensure_infra abriu para o
                # VectoraPostgresStore em storage_mode=complete.
                await store_ctx.close()
                logger.info("agent_factory: store Postgres fechado")
            except Exception as exc:
                logger.warning("agent_factory: erro ao fechar store Postgres: %s", exc)


async def awarm() -> None:
    """Inicializa a infra de persistência + o ``NativeAgent`` padrão eagerly
    no startup (opt-in).

    Evita que a primeira request pague o custo de abrir o store e montar
    tools/subagentes. Falhas aqui não derrubam o servidor — apenas logam
    aviso.
    """
    try:
        await _ensure_infra()
        await get_native_agent()
    except Exception as exc:
        logger.warning("agent_factory: awarm falhou: %s", exc)
