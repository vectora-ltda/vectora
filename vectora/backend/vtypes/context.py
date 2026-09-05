"""Context de execução do agente Vectora (runtime, imutável por turno).

``VectoraContext`` (usado como ``ToolContext`` pelo tool registry nativo,
``backend/tools/context.py``) carrega os dados de sessão que toda tool
precisa: identidade do usuário, workspace ativo, modo de permissão.

``ctx_from_config`` constrói um ``VectoraContext`` a partir de um dict
``{"configurable": {...}}`` — usado por código que ainda recebe esse shape
(handlers legados, adapters de compatibilidade) em vez do context já
tipado.

Campos intencionalmente simples (str/None) para permanecerem serializáveis
sem transformação extra.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VectoraContext:
    """Contexto imutável de execução, passado a toda tool do turno.

    Todos os campos são opcionais para compatibilidade retroativa com
    chamadores que não populam algum deles.
    """

    user_id: str = "local"
    """ID do usuário autenticado. 'local' em modo CLI/anônimo."""

    workspace_id: str = ""
    """ID do workspace ativo na sessão."""

    permission_mode: str = "ask"
    """Modo de permissão: 'ask' | 'accept_edits' | 'auto' | 'bypass' | 'plan'."""

    org_id: str = ""
    """ID da organização (multi-tenant). Vazio em modo single-user."""

    locale: str = ""
    """Locale do usuário (ex: 'pt_BR', 'en_US'). Vazio se não detectado."""

    model: str = ""
    """Provider:model ativo (ex: 'anthropic:claude-sonnet-4-6'). Vazio se padrão."""

    thread_id: str = ""
    """ID da thread/conversa corrente."""

    background_task_id: str = ""
    """ID do card do Kanban cuja run está executando este turno.

    Vazio em turnos de chat síncrono. Populado por
    ``backend.scheduling.background_tasks.run_task``/``resume_background_run``
    — permite ao HITL nativo (`backend/engine/hitl.py`) distinguir
    uma task se auto-atualizando (`kanban_update_status` no próprio id, sem
    aprovação) de uma task tentando mudar o status de OUTRA."""

    store: Any = None
    """Store persistente injetado pelo motor de execução (mesma instância
    de ``backend.services.agent_factory.get_store()``). ``Any`` porque o
    shape depende do backend ativo (``InMemoryStore``, Postgres/Qdrant via
    ``CompositeBackend``) — todo consumidor chama só ``aget``/``aput``/
    ``asearch``/``adelete``, contrato comum a qualquer implementação.
    ``None`` só quando o caller não populou (ex.: contexto de teste sem
    store configurado)."""

    _extra: dict = field(default_factory=dict, repr=False, compare=False)
    """Campos adicionais não cobertos pelos atributos tipados acima."""

    tool_call_id: str = ""
    """ID da chamada de tool que está executando este contexto.

    É efêmero e serve para correlacionar eventos de uma delegação interna
    com o card da chamada no stream da thread pai. Fica após os campos
    posicionais existentes para preservar a compatibilidade dos chamadores.
    """


def ctx_from_config(config: dict | None) -> VectoraContext:
    """Cria ``VectoraContext`` a partir de um dict ``{"configurable": {...}}``.

    Args:
        config: dict com chave ``configurable``. Aceita ``None`` (retorna context padrão).

    Returns:
        ``VectoraContext`` com campos populados do ``configurable``, ou defaults.
    """
    c = (config or {}).get("configurable") or {}
    return VectoraContext(
        user_id=str(c.get("user_id") or "local"),
        workspace_id=str(c.get("workspace_id") or ""),
        permission_mode=str(c.get("permission_mode") or "ask"),
        org_id=str(c.get("org_id") or ""),
        locale=str(c.get("language") or c.get("locale") or ""),
        model=str(c.get("model") or ""),
        thread_id=str(c.get("thread_id") or ""),
        tool_call_id=str(c.get("tool_call_id") or ""),
        background_task_id=str(c.get("background_task_id") or ""),
    )
