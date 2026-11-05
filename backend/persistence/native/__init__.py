"""Persistência nativa do motor de conversa: ``session_store.py``/
``postgres_session_store.py`` (fonte única de verdade de threads/mensagens/
aprovações pendentes, consumida por ``backend/engine/conversation_loop.py``).

``store.py``/``postgres_store.py`` implementam ``StoreBackend``
(``backend/storage/protocols.py``) via ``aiosqlite``/``asyncpg`` direto —
usados pelas tools de memória do agente.
"""

from __future__ import annotations
