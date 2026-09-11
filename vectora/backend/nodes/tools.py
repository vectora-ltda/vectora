"""Catálogo de tools do agente, agrupadas por categoria.

Todos os agentes recebem ALL_TOOLS — a diferença entre eles é o treinamento
(system prompt) e o contexto, não as ferramentas disponíveis.

As listas parciais (SEARCH_TOOLS, FS_TOOLS, etc.) são mantidas como referência
semântica pra montar `SOUL_CATALOG` (backend/agents/souls.py) mas ALL_TOOLS é
a lista canônica consumida pelo agente principal.
"""

from __future__ import annotations

from backend.tools import (
    background as _background_module,
)
from backend.tools import (
    browser as _browser_module,
)
from backend.tools import (
    browser_devtools as _browser_devtools_module,
)
from backend.tools import (
    computer_use as _computer_use_module,
)
from backend.tools import (
    context_graph as _context_graph_module,
)
from backend.tools import (
    fs as _fs_module,
)
from backend.tools import (
    gdrive as _gdrive_module,
)
from backend.tools import (
    gh as _gh_module,
)
from backend.tools import (
    git as _git_module,
)
from backend.tools import (
    gmail as _gmail_module,
)
from backend.tools import (
    homeassistant as _homeassistant_module,
)
from backend.tools import (
    jira as _jira_module,
)
from backend.tools import (
    kanban as _kanban_module,
)
from backend.tools import (
    learning as _learning_module,
)
from backend.tools import (
    library as _library_module,
)
from backend.tools import (
    linear as _linear_module,
)
from backend.tools import (
    mcp as _mcp_module,
)
from backend.tools import (
    media as _media_module,
)
from backend.tools import (
    media_native as _media_native_module,
)
from backend.tools import (
    memory as _memory_module,
)
from backend.tools import (
    native as _native_module,
)
from backend.tools import (
    notion as _notion_module,
)
from backend.tools import (
    planning as _planning_module,
)
from backend.tools import questions as _questions_module
from backend.tools import (
    rag as _rag_module,
)
from backend.tools import (
    slack as _slack_module,
)
from backend.tools import (
    terminal_sessions as _terminal_sessions_module,
)
from backend.tools import (
    thinking as _thinking_module,
)
from backend.tools import (
    web as _web_module,
)
from backend.tools import (
    workspace as _workspace_module,
)
from backend.tools import (
    youtube as _youtube_module,
)
from backend.tools.registry import TOOL_REGISTRY, ToolSpec


def _bridge(name: str) -> ToolSpec:
    """Resolve ``name`` no ``TOOL_REGISTRY`` e devolve o ``ToolSpec`` nativo.

    As listas que este módulo expõe (``ALL_TOOLS``, ``CHAT_TOOLS``,
    ``FS_TOOLS`` …) são ``list[ToolSpec]`` — o dispatch de produção já
    consome ``ToolSpec`` direto (``agent_factory._native_tool_registry``).
    """
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        msg = f"tool nativa '{name}' não registrada — módulo não importado?"
        raise RuntimeError(msg)
    return spec


sequential_thinking = _bridge("sequential_thinking")
computer_use = _bridge("computer_use")
build_knowledge_graph = _bridge("build_knowledge_graph")
graph_update = _bridge("graph_update")
graph_cancel_build = _bridge("graph_cancel_build")
graph_query = _bridge("graph_query")
graph_explain = _bridge("graph_explain")
graph_path = _bridge("graph_path")
graph_affected = _bridge("graph_affected")
file_read = _bridge("file_read")
file_edit = _bridge("file_edit")
file_write = _bridge("file_write")
grep = _bridge("grep")
list_dir = _bridge("list_dir")
terminal = _bridge("terminal")
create_artifact = _bridge("create_artifact")
git_status = _bridge("git_status")
git_log = _bridge("git_log")
git_diff = _bridge("git_diff")
git_branch = _bridge("git_branch")
git_checkout = _bridge("git_checkout")
git_commit = _bridge("git_commit")
git_squash = _bridge("git_squash")
git_reorder = _bridge("git_reorder")
git_cherry_pick = _bridge("git_cherry_pick")
git_fetch = _bridge("git_fetch")
git_merge = _bridge("git_merge")
git_revert = _bridge("git_revert")
git_compare = _bridge("git_compare")
git_resolve_conflict = _bridge("git_resolve_conflict")
git_check_hooks = _bridge("git_check_hooks")
git_push = _bridge("git_push")
git_pull = _bridge("git_pull")
git_stash = _bridge("git_stash")
git_init = _bridge("git_init")
git_worktree = _bridge("git_worktree")
git_stage = _bridge("git_stage")
git_unstage = _bridge("git_unstage")
git_discard = _bridge("git_discard")
call_mcp_tool = _bridge("call_mcp_tool")
kanban_list = _bridge("kanban_list")
kanban_create = _bridge("kanban_create")
kanban_update_status = _bridge("kanban_update_status")
kanban_decompose = _bridge("kanban_decompose")
ha_list_entities = _bridge("ha_list_entities")
ha_get_state = _bridge("ha_get_state")
ha_list_services = _bridge("ha_list_services")
ha_call_service = _bridge("ha_call_service")
learn_from_session = _bridge("learn_from_session")
install_learned_skill = _bridge("install_learned_skill")
save_learned_fact = _bridge("save_learned_fact")
apply_memory_consolidation = _bridge("apply_memory_consolidation")
install_mcp_from_registry = _bridge("install_mcp_from_registry")
install_skill_from_catalog = _bridge("install_skill_from_catalog")
install_memory_bucket = _bridge("install_memory_bucket")
uninstall_mcp = _bridge("uninstall_mcp")
delete_skill = _bridge("delete_skill")
verify_skill = _bridge("verify_skill")
publish_memory_bucket_tool = _bridge("publish_memory_bucket_tool")
publish_skill_tool = _bridge("publish_skill_tool")
save_mcp_env_var = _bridge("save_mcp_env_var")
list_mcp_catalog = _bridge("list_mcp_catalog")
list_skills_catalog = _bridge("list_skills_catalog")
list_memory_bucket_catalog = _bridge("list_memory_bucket_catalog")
create_background_task = _bridge("create_background_task")
schedule_task = _bridge("schedule_task")
schedule_subagent_task = _bridge("schedule_subagent_task")
list_background_tasks = _bridge("list_background_tasks")
get_task_status = _bridge("get_task_status")
get_task_result = _bridge("get_task_result")
approve_task_action = _bridge("approve_task_action")
toggle_background_task = _bridge("toggle_background_task")
delete_background_task = _bridge("delete_background_task")
run_background_task_now = _bridge("run_background_task_now")
browser_navigate = _bridge("browser_navigate")
browser_screenshot = _bridge("browser_screenshot")
browser_click = _bridge("browser_click")
browser_scroll = _bridge("browser_scroll")
browser_fill = _bridge("browser_fill")
browser_read_dom = _bridge("browser_read_dom")
browser_wait_for = _bridge("browser_wait_for")
browser_drag = _bridge("browser_drag")
browser_upload_file = _bridge("browser_upload_file")
browser_fill_form = _bridge("browser_fill_form")
browser_start = _bridge("browser_start")
browser_stop = _bridge("browser_stop")
browser_restart = _bridge("browser_restart")
browser_logs = _bridge("browser_logs")
browser_list_tabs = _bridge("browser_list_tabs")
browser_new_tab = _bridge("browser_new_tab")
browser_close_tab = _bridge("browser_close_tab")
browser_select_tab = _bridge("browser_select_tab")
browser_list_console_messages = _bridge("browser_list_console_messages")
browser_clear_console = _bridge("browser_clear_console")
browser_list_network_requests = _bridge("browser_list_network_requests")
browser_get_network_request = _bridge("browser_get_network_request")
browser_snapshot = _bridge("browser_snapshot")
browser_evaluate = _bridge("browser_evaluate")
browser_set_dialog_policy = _bridge("browser_set_dialog_policy")
browser_emulate = _bridge("browser_emulate")
browser_start_trace = _bridge("browser_start_trace")
browser_stop_trace = _bridge("browser_stop_trace")
browser_analyze_trace = _bridge("browser_analyze_trace")
browser_take_heap_snapshot = _bridge("browser_take_heap_snapshot")
browser_compare_heap_snapshots = _bridge("browser_compare_heap_snapshots")
browser_lighthouse_audit = _bridge("browser_lighthouse_audit")
browser_screencast_start = _bridge("browser_screencast_start")
browser_screencast_stop = _bridge("browser_screencast_stop")
list_terminals = _bridge("list_terminals")
close_terminal = _bridge("close_terminal")
list_desktop_windows = _bridge("list_desktop_windows")
select_desktop_window = _bridge("select_desktop_window")
focus_desktop_window = _bridge("focus_desktop_window")
read_terminal = _bridge("read_terminal")
write_terminal = _bridge("write_terminal")
gmail_list = _bridge("gmail_list")
gmail_read = _bridge("gmail_read")
slack_send = _bridge("slack_send")
slack_list_channels = _bridge("slack_list_channels")
slack_read = _bridge("slack_read")
notion_search = _bridge("notion_search")
notion_read_page = _bridge("notion_read_page")
notion_create_page = _bridge("notion_create_page")
linear_list_issues = _bridge("linear_list_issues")
linear_create_issue = _bridge("linear_create_issue")
linear_update_issue = _bridge("linear_update_issue")
jira_list_issues = _bridge("jira_list_issues")
jira_create_issue = _bridge("jira_create_issue")
jira_transition = _bridge("jira_transition")
google_drive_list = _bridge("google_drive_list")
google_drive_read = _bridge("google_drive_read")
google_drive_search = _bridge("google_drive_search")
gh_pr_list = _bridge("gh_pr_list")
gh_pr_create = _bridge("gh_pr_create")
gh_pr_view = _bridge("gh_pr_view")
gh_pr_merge = _bridge("gh_pr_merge")
gh_issue_list = _bridge("gh_issue_list")
gh_issue_create = _bridge("gh_issue_create")
gh_issue_view = _bridge("gh_issue_view")
gh_issue_comment = _bridge("gh_issue_comment")
workspace_describe = _bridge("workspace_describe")
workspace_list = _bridge("workspace_list")
time_now = _bridge("time_now")
time_parse = _bridge("time_parse")
hash_text = _bridge("hash_text")
base64_encode = _bridge("base64_encode")
base64_decode = _bridge("base64_decode")
regex_test = _bridge("regex_test")
json_query = _bridge("json_query")
jwt_decode = _bridge("jwt_decode")
http_request = _bridge("http_request")
bucket_summary = _bridge("bucket_summary")
get_workbench_context = _bridge("get_workbench_context")
web_search = _bridge("web_search")
fetch_url = _bridge("fetch_url")
web_crawl = _bridge("web_crawl")
web_map = _bridge("web_map")
get_transcript = _bridge("get_transcript")
youtube_frame_at = _bridge("youtube_frame_at")
embedding = _bridge("embedding")
vector_search = _bridge("vector_search")
ingest_docs = _bridge("ingest_docs")
manage_retriever = _bridge("manage_retriever")
save_memory = _bridge("save_memory")
get_memory = _bridge("get_memory")
delete_memory = _bridge("delete_memory")
search_memory = _bridge("search_memory")
generate_image = _bridge("generate_image")
text_to_speech = _bridge("text_to_speech")
generate_video = _bridge("generate_video")
analyze_video = _bridge("analyze_video")
audio_transcribe = _bridge("audio_transcribe")
probe_media = _bridge("probe_media")
extract_frame = _bridge("extract_frame")
extract_audio = _bridge("extract_audio")
transcribe_local = _bridge("transcribe_local")
write_todos = _bridge("write_todos")
ask_structured_question = _bridge("ask_structured_question")

# ---------------------------------------------------------------------------
# Grupos semânticos (referência — não são usados diretamente pelos agentes)
# ---------------------------------------------------------------------------

#: Ferramentas de busca e pesquisa
SEARCH_TOOLS: list[ToolSpec] = [
    web_search,
    fetch_url,
    web_crawl,
    web_map,
    get_transcript,
    youtube_frame_at,
    vector_search,
    embedding,
    ingest_docs,
    manage_retriever,
]

#: Ferramentas de filesystem, terminal e artifacts
FS_TOOLS: list[ToolSpec] = [
    file_read,
    file_edit,
    file_write,
    grep,
    list_dir,
    terminal,
    create_artifact,
    list_terminals,
    close_terminal,
    list_desktop_windows,
    select_desktop_window,
    focus_desktop_window,
    read_terminal,
    write_terminal,
]

#: Ferramentas de browser: navegação livre + automação + dev server
BROWSER_TOOLS: list[ToolSpec] = [
    browser_navigate,
    browser_screenshot,
    browser_click,
    browser_scroll,
    browser_fill,
    browser_read_dom,
    browser_wait_for,
    browser_drag,
    browser_upload_file,
    browser_fill_form,
    browser_start,
    browser_stop,
    browser_restart,
    browser_logs,
    browser_list_tabs,
    browser_new_tab,
    browser_close_tab,
    browser_select_tab,
    browser_list_console_messages,
    browser_clear_console,
    browser_list_network_requests,
    browser_get_network_request,
    browser_evaluate,
    browser_snapshot,
    browser_set_dialog_policy,
    browser_emulate,
    browser_start_trace,
    browser_stop_trace,
    browser_analyze_trace,
    browser_take_heap_snapshot,
    browser_compare_heap_snapshots,
    browser_screencast_start,
    browser_screencast_stop,
    browser_lighthouse_audit,
]

#: Ferramentas de memória — inclui busca semântica e o loop de aprendizado (Remember)
MEMORY_TOOLS: list[ToolSpec] = [
    save_memory,
    get_memory,
    delete_memory,
    search_memory,
    learn_from_session,
    install_learned_skill,
    save_learned_fact,
    apply_memory_consolidation,
]

#: Ferramentas da Library: auto-instalar MCP/Skills/Memory Library, invocar
#: MCP externo já conectado
LIBRARY_TOOLS: list[ToolSpec] = [
    call_mcp_tool,
    list_mcp_catalog,
    list_skills_catalog,
    list_memory_bucket_catalog,
    install_mcp_from_registry,
    install_skill_from_catalog,
    install_memory_bucket,
    uninstall_mcp,
    delete_skill,
    verify_skill,
    publish_memory_bucket_tool,
    publish_skill_tool,
    save_mcp_env_var,
]

#: Ferramentas de workspace e manifests
WORKSPACE_TOOLS: list[ToolSpec] = [
    workspace_describe,
    workspace_list,
    bucket_summary,
    get_workbench_context,
    create_background_task,
    schedule_task,
    schedule_subagent_task,
    list_background_tasks,
    get_task_status,
    get_task_result,
    approve_task_action,
    toggle_background_task,
    delete_background_task,
    run_background_task_now,
    kanban_list,
    kanban_create,
    kanban_update_status,
    kanban_decompose,
]

#: Ferramentas do Context Graph (grafo de conhecimento do workspace)
GRAPH_TOOLS: list[ToolSpec] = [
    build_knowledge_graph,
    graph_query,
    graph_explain,
    graph_path,
    graph_affected,
    graph_update,
    graph_cancel_build,
]

#: Ferramentas RAG de ingestão e gestão
RAG_TOOLS: list[ToolSpec] = [vector_search, embedding, ingest_docs, manage_retriever]

#: Planejamento — quebra de tarefa complexa em passos, exposta a todo agente
PLANNING_TOOLS: list[ToolSpec] = [write_todos]

#: Utilitários nativos (sem API externa, exceto http_request)
NATIVE_TOOLS: list[ToolSpec] = [
    time_now,
    time_parse,
    hash_text,
    base64_encode,
    base64_decode,
    regex_test,
    json_query,
    jwt_decode,
    http_request,
    ask_structured_question,
]

#: Ferramentas git e GitHub CLI
GIT_TOOLS: list[ToolSpec] = [
    git_status,
    git_log,
    git_diff,
    git_branch,
    git_checkout,
    git_commit,
    git_push,
    git_pull,
    git_stash,
    git_init,
    git_worktree,
    git_stage,
    git_unstage,
    git_discard,
    git_squash,
    git_reorder,
    git_cherry_pick,
    git_fetch,
    git_merge,
    git_revert,
    git_compare,
    git_resolve_conflict,
    git_check_hooks,
    gh_pr_list,
    gh_pr_create,
    gh_pr_view,
    gh_pr_merge,
    gh_issue_list,
    gh_issue_create,
    gh_issue_view,
    gh_issue_comment,
]

# ---------------------------------------------------------------------------
# ALL_TOOLS — lista canônica usada por TODOS os agentes
# ---------------------------------------------------------------------------
# Todos os agentes recebem o mesmo conjunto de ferramentas.
# O comportamento diferenciado vem do system prompt e do contexto, não
# da restrição de acesso às ferramentas.

_all: dict[str, ToolSpec] = {}
for _t in [
    web_search,
    fetch_url,
    web_crawl,
    web_map,
    get_transcript,
    youtube_frame_at,
    vector_search,
    embedding,
    ingest_docs,
    manage_retriever,
    file_read,
    file_edit,
    file_write,
    grep,
    list_dir,
    terminal,
    create_artifact,
    list_terminals,
    close_terminal,
    list_desktop_windows,
    select_desktop_window,
    focus_desktop_window,
    read_terminal,
    write_terminal,
    browser_navigate,
    browser_screenshot,
    browser_click,
    browser_scroll,
    browser_fill,
    browser_read_dom,
    browser_wait_for,
    browser_drag,
    browser_upload_file,
    browser_fill_form,
    browser_start,
    browser_stop,
    browser_restart,
    browser_logs,
    browser_list_tabs,
    browser_new_tab,
    browser_close_tab,
    browser_select_tab,
    browser_list_console_messages,
    browser_clear_console,
    browser_list_network_requests,
    browser_get_network_request,
    browser_evaluate,
    browser_snapshot,
    browser_set_dialog_policy,
    browser_emulate,
    browser_start_trace,
    browser_stop_trace,
    browser_analyze_trace,
    browser_take_heap_snapshot,
    browser_compare_heap_snapshots,
    browser_screencast_start,
    browser_screencast_stop,
    browser_lighthouse_audit,
    save_memory,
    get_memory,
    delete_memory,
    search_memory,
    learn_from_session,
    install_learned_skill,
    save_learned_fact,
    apply_memory_consolidation,
    call_mcp_tool,
    list_mcp_catalog,
    list_skills_catalog,
    list_memory_bucket_catalog,
    install_mcp_from_registry,
    install_skill_from_catalog,
    install_memory_bucket,
    uninstall_mcp,
    delete_skill,
    verify_skill,
    publish_memory_bucket_tool,
    publish_skill_tool,
    save_mcp_env_var,
    workspace_describe,
    workspace_list,
    bucket_summary,
    get_workbench_context,
    create_background_task,
    schedule_task,
    schedule_subagent_task,
    list_background_tasks,
    get_task_status,
    get_task_result,
    approve_task_action,
    toggle_background_task,
    delete_background_task,
    run_background_task_now,
    kanban_list,
    kanban_create,
    kanban_update_status,
    kanban_decompose,
    # Git + GitHub CLI
    git_status,
    git_log,
    git_diff,
    git_branch,
    git_checkout,
    git_commit,
    git_push,
    git_pull,
    git_stash,
    git_init,
    git_worktree,
    git_stage,
    git_unstage,
    git_discard,
    git_squash,
    git_reorder,
    git_cherry_pick,
    git_fetch,
    git_merge,
    git_revert,
    git_compare,
    git_resolve_conflict,
    git_check_hooks,
    gh_pr_list,
    gh_pr_create,
    gh_pr_view,
    gh_pr_merge,
    gh_issue_list,
    gh_issue_create,
    gh_issue_view,
    gh_issue_comment,
    # Integrações externas (configuradas via OAuth/API key; cada tool degrada
    # para erro tipado quando o provider não está conectado).
    google_drive_list,
    google_drive_read,
    google_drive_search,
    gmail_list,
    gmail_read,
    slack_send,
    slack_list_channels,
    slack_read,
    linear_list_issues,
    linear_create_issue,
    linear_update_issue,
    jira_list_issues,
    jira_create_issue,
    jira_transition,
    notion_search,
    notion_read_page,
    notion_create_page,
    build_knowledge_graph,
    graph_query,
    graph_explain,
    graph_path,
    graph_affected,
    graph_update,
    graph_cancel_build,
    sequential_thinking,
    # Mídia (imagem/voz pelo provider ativo)
    generate_image,
    text_to_speech,
    generate_video,
    analyze_video,
    audio_transcribe,
    # Mídia LOCAL (ffmpeg/ffprobe embutido, sem custo de API)
    probe_media,
    extract_frame,
    extract_audio,
    transcribe_local,
    # Casa conectada (Home Assistant)
    ha_list_entities,
    ha_get_state,
    ha_list_services,
    ha_call_service,
    # Controle de tela do desktop (opt-in, sempre HITL)
    computer_use,
    # Utilitários nativos
    time_now,
    time_parse,
    hash_text,
    base64_encode,
    base64_decode,
    regex_test,
    json_query,
    jwt_decode,
    http_request,
    # Planejamento (checklist de progresso, todo agente)
    write_todos,
    ask_structured_question,
]:
    _all[_t.name] = _t

ALL_TOOLS: list[ToolSpec] = list(_all.values())

# ── Vistas nativas do toolset principal (mesmos nomes de ALL_TOOLS) ──────
# Consumidores que só precisam de `name`/`description`/`extras` (validação,
# discovery, metadados de UI) usam estas.
ALL_TOOL_NAMES: frozenset[str] = frozenset(_all)
ALL_TOOL_SPECS: list[ToolSpec] = [
    spec for spec in TOOL_REGISTRY.all() if spec.name in ALL_TOOL_NAMES
]

# ---------------------------------------------------------------------------
# CHAT_TOOLS — modo Chat (conversacional puro, sem workspace/folders)
# ---------------------------------------------------------------------------
# Sem filesystem/git/terminal/workspace: só conversa, web, RAG (retrieval),
# memória e integrações externas. Usado quando chat_mode=True no agent_factory.

CHAT_TOOLS: list[ToolSpec] = [
    web_search,
    fetch_url,
    web_crawl,
    web_map,
    get_transcript,
    youtube_frame_at,
    vector_search,
    save_memory,
    get_memory,
    delete_memory,
    search_memory,
    google_drive_list,
    google_drive_read,
    google_drive_search,
    gmail_list,
    gmail_read,
    slack_send,
    slack_list_channels,
    slack_read,
    linear_list_issues,
    linear_create_issue,
    linear_update_issue,
    jira_list_issues,
    jira_create_issue,
    jira_transition,
    notion_search,
    notion_read_page,
    notion_create_page,
    time_now,
    time_parse,
    hash_text,
    base64_encode,
    base64_decode,
    regex_test,
    json_query,
    jwt_decode,
    http_request,
    write_todos,
]

# Vista nativa dos nomes do modo Chat (mesmos nomes de CHAT_TOOLS, sem a
# ponte BaseTool) — usada por `agent_factory._native_tool_registry`.
CHAT_TOOL_NAMES: frozenset[str] = frozenset(t.name for t in CHAT_TOOLS)

__all__ = [
    "ALL_TOOLS",
    "ALL_TOOL_NAMES",
    "ALL_TOOL_SPECS",
    "BROWSER_TOOLS",
    "CHAT_TOOLS",
    "CHAT_TOOL_NAMES",
    "FS_TOOLS",
    "GIT_TOOLS",
    "GRAPH_TOOLS",
    "MEMORY_TOOLS",
    "PLANNING_TOOLS",
    "RAG_TOOLS",
    "SEARCH_TOOLS",
    "WORKSPACE_TOOLS",
]
