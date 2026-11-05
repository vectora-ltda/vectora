"""Services Layer: Domain-specific business logic without UI dependencies.

Each service encapsulates one responsibility domain and can be tested in isolation.
All services depend only on Settings and standard library/third-party packages.

Services:
- Security (module): Security validation utilities (is_safe_* functions)

Nota: Embeddings são gerenciados por BackgroundEmbeddingWorker (services/background.py),
iniciado via async_lifespan() em services/utils.py. Não há mais EmbeddingService aqui.
Telemetria real e ativa vive em backend/persistence/telemetry.py::VectoraTelemetry,
ligada no lifespan de backend/api/server.py.
"""

from __future__ import annotations
