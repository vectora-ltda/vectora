"""Vectora Tools Package.

Agrupa todas as ferramentas do agente em módulos temáticos (web, rag, fs,
memory, mcp, browser, native, …). Cada tool é registrada no ``TOOL_REGISTRY``
nativo (``backend/tools/registry.py``) via ``@vtool`` na importação do seu
módulo — este pacote **não** re-exporta mais adaptadores ``BaseTool``: o
dispatch de produção consome ``ToolSpec`` direto, e quem precisa registrar
todas as tools importa ``backend.nodes.tools`` (que importa os módulos um a
um).
"""
