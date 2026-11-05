"""Definição dos recursos de coleção expostos pelo schema declarativo
(``backend.config.collections``). Importar este módulo é o que popula o
registro de coleções — feito em ``backend/config/__init__.py``.

Categorias de coleção: ``provider_routing`` (modelos registrados por gateway —
tabelas SQLite ad hoc do handler). Outras coleções (memórias, perfil de conta)
continuam com CRUD especializado próprio; este contrato cobre os recursos cujo
acesso vale a pena uniformizar.
"""

from __future__ import annotations

from backend.config.adapters import (
    MemoryAdapter,
    RegisteredModelsTableAdapter,
    UserProfileAdapter,
)
from backend.config.collections import collection_field, user_scoped_field

collection_field(
    "ollama_registered_models",
    category="provider_routing",
    description="Modelos registrados do gateway Ollama.",
    adapter=RegisteredModelsTableAdapter("ollama_registered_models"),
)
collection_field(
    "llamacpp_registered_models",
    category="provider_routing",
    description="Modelos registrados do gateway llama.cpp.",
    adapter=RegisteredModelsTableAdapter("llamacpp_registered_models"),
)
collection_field(
    "openrouter_registered_models",
    category="provider_routing",
    description="Modelos registrados do gateway OpenRouter.",
    adapter=RegisteredModelsTableAdapter("openrouter_registered_models"),
)
collection_field(
    "nine_router_registered_models",
    category="provider_routing",
    description="Modelos registrados do gateway 9Router.",
    adapter=RegisteredModelsTableAdapter("nine_router_registered_models"),
)

# Recursos per-usuário: cada "item" é escopado por user_id (o adapter recebe
# o dono em cada operação). Não são campos escalares globais.
user_scoped_field(
    "user_profiles",
    category="account",
    description="Perfis de conta por usuário (nome, e-mail, role).",
    adapter=UserProfileAdapter(),
)
user_scoped_field(
    "user_memories",
    category="memory",
    description="Fatos de memória por usuário (namespace do store do agente).",
    adapter=MemoryAdapter(),
)
