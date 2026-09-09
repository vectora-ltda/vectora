"""Tests for src/nodes/tools.py"""

from __future__ import annotations

from backend.nodes.tools import (
    ALL_TOOL_NAMES,
    ALL_TOOL_SPECS,
    ALL_TOOLS,
    FS_TOOLS,
    MEMORY_TOOLS,
    SEARCH_TOOLS,
)


def test_fs_tools_not_empty():
    assert len(FS_TOOLS) > 0


def test_memory_tools_not_empty():
    assert len(MEMORY_TOOLS) > 0


def test_search_tools_not_empty():
    assert len(SEARCH_TOOLS) > 0


def test_all_tools_is_union():
    assert len(ALL_TOOLS) >= len(SEARCH_TOOLS)
    assert len(ALL_TOOLS) >= len(FS_TOOLS)
    assert len(ALL_TOOLS) >= len(MEMORY_TOOLS)


def test_native_views_espelham_all_tools():
    """ALL_TOOL_NAMES/ALL_TOOL_SPECS são a visão nativa do MESMO toolset de
    ALL_TOOLS — nomes idênticos. Garante que a migração dos consumidores
    leves não muda o conjunto exposto."""
    legacy_names = {t.name for t in ALL_TOOLS}
    assert legacy_names == ALL_TOOL_NAMES
    assert {spec.name for spec in ALL_TOOL_SPECS} == ALL_TOOL_NAMES


def test_tools_have_names():
    for tool in ALL_TOOLS:
        assert hasattr(tool, "name")
        assert isinstance(tool.name, str)
        assert len(tool.name) > 0


def test_search_tools_include_web_search():
    names = [t.name for t in SEARCH_TOOLS]
    assert "web_search" in names


def test_fs_tools_include_file_read():
    names = [t.name for t in FS_TOOLS]
    assert "file_read" in names


def test_memory_tools_include_save_memory():
    names = [t.name for t in MEMORY_TOOLS]
    assert "save_memory" in names


def test_manage_retriever_registered():
    # A tool de gestão do RAG deve estar disponível aos agentes.
    names = [t.name for t in ALL_TOOLS]
    assert "manage_retriever" in names


def test_workspace_tools_registered():
    # Ferramentas de workspace expostas aos agentes.
    names = [t.name for t in ALL_TOOLS]
    assert "workspace_describe" in names
    assert "workspace_list" in names
    assert "bucket_summary" in names


def test_search_memory_registered():
    # Busca semântica em memórias deve estar disponível aos agentes.
    names = [t.name for t in ALL_TOOLS]
    assert "search_memory" in names


def test_all_tools_count():
    # Guarda contra perda acidental de registro de ferramentas — atualize ao
    # adicionar/remover tool em backend/nodes/tools.py.
    #
    # A mensagem lista os nomes: só o número não diz *qual* tool entrou ou
    # sumiu, e a contagem já ficou defasada em silêncio uma vez por isso.
    nomes = sorted(t.name for t in ALL_TOOLS)
    assert len(ALL_TOOLS) == 173, f"tools registradas: {nomes}"


def test_all_tools_sem_nome_duplicado():
    # Erro/borda: nome repetido faz a segunda registrar por cima da primeira
    # no bind_tools — a tool some sem a contagem mudar.
    from collections import Counter

    nomes = [t.name for t in ALL_TOOLS]
    repetidos = [n for n, c in Counter(nomes).items() if c > 1]
    assert not repetidos, f"tools com nome duplicado: {repetidos}"


def test_media_tools_registered():
    # Geração de imagem/voz pelo provider ativo — sem elas o agente não tem
    # como atender "gere uma imagem" nem "leia isso em voz alta".
    names = [t.name for t in ALL_TOOLS]
    assert "generate_image" in names
    assert "text_to_speech" in names


def test_background_task_tools_registered():
    # O orquestrador lista/consulta E intervém em tasks/runs em background.
    names = {t.name for t in ALL_TOOLS}
    for expected in (
        "create_background_task",
        "list_background_tasks",
        "get_task_status",
        "get_task_result",
        "approve_task_action",
    ):
        assert expected in names, f"Tool de background ausente: {expected}"


def test_browser_tools_registered():
    # Automação de browser sobre o preview do workspace (Playwright).
    names = {t.name for t in ALL_TOOLS}
    for expected in (
        "browser_screenshot",
        "browser_click",
        "browser_scroll",
        "browser_fill",
        "browser_read_dom",
    ):
        assert expected in names, f"Browser tool ausente: {expected}"


def test_native_tools_registered():
    # Utilitários nativos (backend/tools/native/) devem estar registrados em
    # ALL_TOOLS para chegar ao agente real.
    names = {t.name for t in ALL_TOOLS}
    for expected in (
        "time_now",
        "time_parse",
        "hash_text",
        "base64_encode",
        "base64_decode",
        "regex_test",
        "json_query",
        "jwt_decode",
        "http_request",
    ):
        assert expected in names, f"Native tool ausente: {expected}"


def test_native_tools_registered_in_chat_mode():
    from backend.nodes.tools import CHAT_TOOLS

    names = {t.name for t in CHAT_TOOLS}
    assert "time_now" in names
    assert "hash_text" in names


def test_graph_tools_registered():
    # Context graph tools devem estar disponíveis aos agentes.
    names = [t.name for t in ALL_TOOLS]
    for expected in (
        "build_knowledge_graph",
        "graph_query",
        "graph_explain",
        "graph_path",
    ):
        assert expected in names, f"Tool ausente: {expected}"
    # Erro: tools que não existem não devem estar presentes
    assert "context_graph_build" not in names
