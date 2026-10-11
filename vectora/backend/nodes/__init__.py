"""Nodes Package — toolset canônico (ALL_TOOLS) do agente, como ``ToolSpec``
nativos (``backend/tools/registry.py``)."""

from __future__ import annotations

from backend.tools.registry import TOOL_REGISTRY, ToolSpec

_FS_NAMES = frozenset(
    {
        "file_read",
        "file_edit",
        "file_write",
        "grep",
        "list_dir",
        "terminal",
        "create_artifact",
        "list_terminals",
        "attach_terminal",
        "read_terminal",
        "write_terminal",
        "close_terminal",
    }
)

_GIT_NAMES = frozenset(
    {
        "git_status",
        "git_log",
        "git_diff",
        "git_branch",
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
    }
)

_MEMORY_NAMES = frozenset(
    {
        "save_memory",
        "get_memory",
        "delete_memory",
        "search_memory",
        "learn_from_session",
        "install_learned_skill",
        "save_learned_fact",
        "apply_memory_consolidation",
    }
)

_RAG_NAMES = frozenset(
    {"vector_search", "embedding", "ingest_docs", "manage_retriever"}
)

_SEARCH_NAMES = frozenset(
    {
        "web_search",
        "fetch_url",
        "web_crawl",
        "web_map",
        "vector_search",
        "embedding",
        "ingest_docs",
        "manage_retriever",
    }
)

_WORKSPACE_NAMES = frozenset(
    {
        "workspace_describe",
        "workspace_list",
        "bucket_summary",
        "get_workbench_context",
        "create_background_task",
        "schedule_task",
        "schedule_subagent_task",
        "list_background_tasks",
        "get_task_status",
        "get_task_result",
        "approve_task_action",
        "toggle_background_task",
        "delete_background_task",
        "run_background_task_now",
        "kanban_list",
        "kanban_create",
        "kanban_update_status",
        "kanban_decompose",
    }
)


def _by_names(names: frozenset[str]) -> list[ToolSpec]:
    return [spec for spec in TOOL_REGISTRY.all() if spec.name in names]


ALL_TOOLS: list[ToolSpec] = TOOL_REGISTRY.all()
FS_TOOLS: list[ToolSpec] = _by_names(_FS_NAMES)
GIT_TOOLS: list[ToolSpec] = _by_names(_GIT_NAMES)
MEMORY_TOOLS: list[ToolSpec] = _by_names(_MEMORY_NAMES)
RAG_TOOLS: list[ToolSpec] = _by_names(_RAG_NAMES)
SEARCH_TOOLS: list[ToolSpec] = _by_names(_SEARCH_NAMES)
WORKSPACE_TOOLS: list[ToolSpec] = _by_names(_WORKSPACE_NAMES)

__all__ = [
    "ALL_TOOLS",
    "FS_TOOLS",
    "GIT_TOOLS",
    "MEMORY_TOOLS",
    "RAG_TOOLS",
    "SEARCH_TOOLS",
    "WORKSPACE_TOOLS",
]
