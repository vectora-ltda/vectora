"""Grupos nomeados de tools nativas — composição por ``includes`` (um grupo
referencia outro grupo pelo nome) com resolução recursiva e detecção de
ciclo real, no mesmo espírito do ``toolsets.py`` do Hermes.

Cada ``ToolGroupSpec`` guarda nomes de tool (strings resolvidas no
``TOOL_REGISTRY`` nativo em tempo de resolução), nunca objetos — o grupo é
só uma lista nomeada; o objeto de tool concreto vem sempre do registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.tools.registry import TOOL_REGISTRY, ToolSpec


class ToolGroupCycleError(Exception):
    """Cadeia de ``includes`` entre grupos forma um ciclo (ex.: A inclui B,
    B inclui A) — sem isso, ``resolve_tool_group`` recursaria até estourar
    a pilha em vez de falhar de forma legível."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__("ciclo detectado em tool_groups: " + " -> ".join(cycle))


class ToolGroupNotFoundError(Exception):
    """``resolve_tool_group`` (ou a montagem de uma SOUL) referencia um nome
    de grupo que não existe em ``TOOL_GROUPS``."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"tool group '{name}' não registrado em TOOL_GROUPS")


class ToolNameNotFoundError(Exception):
    """Um grupo lista o nome de uma tool que não está registrada no
    ``TOOL_REGISTRY`` nativo — normalmente módulo de tool não importado."""

    def __init__(self, group: str, tool_name: str) -> None:
        self.group = group
        self.tool_name = tool_name
        super().__init__(
            f"tool '{tool_name}' referenciada pelo grupo '{group}' não está "
            "registrada no TOOL_REGISTRY"
        )


@dataclass(frozen=True, slots=True)
class ToolGroupSpec:
    """Um grupo nomeado — nomes de tool próprios (``tool_names``) mais nomes
    de outros grupos a compor por união (``includes``)."""

    name: str
    description: str
    tool_names: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)


TOOL_GROUPS: dict[str, ToolGroupSpec] = {}


def register_tool_group(spec: ToolGroupSpec) -> None:
    """Registra ``spec`` em ``TOOL_GROUPS`` — levanta erro em nome
    duplicado, mesmo contrato de ``ToolRegistry.register``."""
    if spec.name in TOOL_GROUPS:
        msg = f"tool group '{spec.name}' já registrado — nome duplicado"
        raise ValueError(msg)
    TOOL_GROUPS[spec.name] = spec


def resolve_tool_group(name: str, visited: set[str] | None = None) -> list[ToolSpec]:
    """Resolve ``name`` para a união (deduplicada por nome de tool) de
    ``ToolSpec`` do grupo, incluindo recursivamente os grupos listados em
    ``includes``.

    ``visited`` é a cadeia de grupos já em resolução na pilha de chamada
    atual — reencontrar um nome nela é um ciclo real (A inclui B inclui A).
    O mesmo grupo incluído por dois ramos irmãos (não-ancestrais) é união
    legítima, não ciclo, por isso ``visited`` nunca é compartilhado entre
    chamadas irmãs — cada uma recebe a cópia do ponto em que o pai estava.
    """
    group = TOOL_GROUPS.get(name)
    if group is None:
        raise ToolGroupNotFoundError(name)

    visited = visited or set()
    if name in visited:
        raise ToolGroupCycleError([*visited, name])
    visited = visited | {name}

    resolved: dict[str, ToolSpec] = {}
    for tool_name in group.tool_names:
        spec = TOOL_REGISTRY.get(tool_name)
        if spec is None:
            raise ToolNameNotFoundError(group.name, tool_name)
        resolved[spec.name] = spec

    for included_name in group.includes:
        for spec in resolve_tool_group(included_name, visited):
            resolved[spec.name] = spec

    return list(resolved.values())


register_tool_group(
    ToolGroupSpec(
        name="rag",
        description="Ingestão e busca semântica sobre a base indexada.",
        tool_names=["vector_search", "embedding", "ingest_docs", "manage_retriever"],
    )
)

register_tool_group(
    ToolGroupSpec(
        name="search",
        description="Busca web em tempo real e fetch de URLs, mais RAG.",
        tool_names=["web_search", "fetch_url", "web_crawl", "web_map"],
        includes=["rag"],
    )
)

register_tool_group(
    ToolGroupSpec(
        name="fs_readonly",
        description="Leitura de filesystem, sem escrita.",
        tool_names=["file_read", "grep", "list_dir"],
    )
)

register_tool_group(
    ToolGroupSpec(
        name="fs_write",
        description="Leitura e escrita de filesystem, sem terminal.",
        tool_names=[
            "file_read",
            "file_edit",
            "file_write",
            "file_create_dir",
            "file_delete",
            "file_move",
            "file_search",
            "grep",
            "list_dir",
            "create_artifact",
        ],
    )
)

register_tool_group(
    ToolGroupSpec(
        name="terminal_only",
        description="Execução de comandos de shell.",
        tool_names=["terminal"],
    )
)

register_tool_group(
    ToolGroupSpec(
        name="fs",
        description="Filesystem completo: leitura, escrita, terminal e artifacts.",
        tool_names=["list_terminals", "close_terminal"],
        includes=["fs_write", "terminal_only"],
    )
)

register_tool_group(
    ToolGroupSpec(
        name="git_readonly",
        description="Inspeção de git, sem mutar histórico/working tree.",
        tool_names=[
            "git_status",
            "git_operation",
            "git_operations",
            "git_commit_suggestion",
            "git_log",
            "git_diff",
            "git_branch",
        ],
    )
)

register_tool_group(
    ToolGroupSpec(
        name="git",
        description="Git completo e GitHub CLI.",
        tool_names=[
            "git_checkout",
            "git_commit",
            "git_push",
            "git_pull",
            "git_stash",
            "git_init",
            "git_worktree",
            "git_stage",
            "git_unstage",
            "git_discard",
            "git_squash",
            "git_reorder",
            "git_cherry_pick",
            "git_fetch",
            "git_merge",
            "git_revert",
            "git_compare",
            "git_resolve_conflict",
            "git_check_hooks",
            "gh_pr_list",
            "gh_pr_create",
            "gh_pr_view",
            "gh_pr_merge",
            "gh_issue_list",
            "gh_issue_create",
            "gh_issue_view",
            "gh_issue_comment",
            "github_fetch_pr_diff",
            "github_post_pr_comment",
        ],
        includes=["git_readonly"],
    )
)

register_tool_group(
    ToolGroupSpec(
        name="browser",
        description="Navegação, automação e controle de dev server no Browser.",
        tool_names=[
            "browser_navigate",
            "browser_screenshot",
            "browser_click",
            "browser_scroll",
            "browser_fill",
            "browser_read_dom",
            "browser_wait_for",
            "browser_drag",
            "browser_upload_file",
            "browser_fill_form",
            "browser_start",
            "browser_stop",
            "browser_restart",
            "browser_logs",
            "browser_list_tabs",
            "browser_new_tab",
            "browser_close_tab",
            "browser_select_tab",
            "browser_list_console_messages",
            "browser_clear_console",
            "browser_list_network_requests",
            "browser_get_network_request",
            "browser_evaluate",
            "browser_snapshot",
            "browser_set_dialog_policy",
            "browser_emulate",
            "browser_start_trace",
            "browser_stop_trace",
            "browser_analyze_trace",
            "browser_take_heap_snapshot",
            "browser_compare_heap_snapshots",
            "browser_screencast_start",
            "browser_screencast_stop",
            "browser_lighthouse_audit",
        ],
    )
)

register_tool_group(
    ToolGroupSpec(
        name="memory",
        description="Memória persistente e loop de aprendizado (Remember).",
        tool_names=[
            "save_memory",
            "get_memory",
            "delete_memory",
            "search_memory",
            "learn_from_session",
            "install_learned_skill",
            "save_learned_fact",
            "apply_memory_consolidation",
        ],
    )
)

register_tool_group(
    ToolGroupSpec(
        name="artifact",
        description="Criação de artifacts (documento/plano renderizado).",
        tool_names=["create_artifact"],
    )
)

register_tool_group(
    ToolGroupSpec(
        name="aitl",
        description="Pergunta ao agente pai — só faz sentido dentro de uma delegação.",
        tool_names=["ask_parent_agent"],
    )
)

# Aliases compatíveis com nomes antigos ainda usados por SOULs/callers.
register_tool_group(
    ToolGroupSpec(
        name="browser-qa",
        description="Alias compatível de browser.",
        includes=["browser"],
    )
)
register_tool_group(
    ToolGroupSpec(
        name="fs-readonly",
        description="Alias compatível de fs_readonly.",
        includes=["fs_readonly"],
    )
)
register_tool_group(
    ToolGroupSpec(
        name="planner",
        description="Alias compatível de artifact.",
        includes=["artifact"],
    )
)
