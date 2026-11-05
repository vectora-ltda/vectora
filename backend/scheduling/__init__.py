"""Execução assíncrona agendada/disparada por trigger.

``background_tasks.py`` (tarefas cron/webhook/manual/subagente + histórico de
execuções, consumido pela aba Tarefas do workbench), ``memory_consolidation.py``
(síntese periódica de threads em AGENTS.md), ``mq.py`` (fila de mensagens
entre processos) e ``file_watcher.py`` (observador de mudanças de arquivo).

Lazy imports (``__getattr__``) — mesmo padrão de ``backend.mcp`` — evitam
puxar croniter/Redis no import do pacote quando só um submódulo específico
é necessário.
"""

from __future__ import annotations

__all__ = [
    "create_task",
    "get_mq",
    "run_task",
]


def __getattr__(name: str) -> object:
    if name in ("create_task", "run_task"):
        from backend.scheduling import background_tasks

        return getattr(background_tasks, name)
    if name == "get_mq":
        from backend.scheduling.mq import get_mq

        return get_mq
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
