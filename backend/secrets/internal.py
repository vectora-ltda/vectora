"""Fallback provider de secrets usando SQLite + PyNaCl (SecretBox).

Usado em testes unitários e em ambientes sem pykeepass disponível.
Os secrets são criptografados com XSalsa20-Poly1305 (NaCl SecretBox).

A chave de criptografia é derivada da senha de login via scrypt — mesma
abordagem do provider KeePass, porém persiste no SQLite local em vez de .kdbx.

Banco: ~/.vectora/secrets/internal.db
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DB_PATH = Path.home() / ".vectora" / "secrets" / "internal.db"
_db_conn: Any = None
_open_keys: dict[str, bytes] = {}  # user_id → chave NaCl em memória


async def _get_db() -> Any:
    global _db_conn
    if _db_conn is not None:
        return _db_conn

    import aiosqlite

    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _db_conn = await aiosqlite.connect(str(_DB_PATH))
    await _db_conn.execute("PRAGMA journal_mode=WAL")
    await _db_conn.execute("""
        CREATE TABLE IF NOT EXISTS secrets (
            user_id TEXT NOT NULL,
            key     TEXT NOT NULL,
            ciphertext BLOB NOT NULL,
            nonce      BLOB NOT NULL,
            PRIMARY KEY (user_id, key)
        )
    """)
    await _db_conn.commit()
    return _db_conn


def _derive_key(user_id: str, password: str) -> bytes:
    """Deriva uma chave NaCl de 32 bytes via scrypt."""
    salt = hashlib.sha256(user_id.encode()).digest()
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, dklen=32)


class InternalSecretsProvider:
    """Fallback provider SQLite + PyNaCl SecretBox."""

    async def unlock(self, user_id: str, master_password: str) -> None:
        _open_keys[user_id] = _derive_key(user_id, master_password)
        await _get_db()

    async def lock(self, user_id: str) -> None:
        _open_keys.pop(user_id, None)

    def _get_key(self, user_id: str) -> bytes:
        key = _open_keys.get(user_id)
        if key is None:
            raise RuntimeError(f"Vault do usuário {user_id!r} não está aberto.")
        return key

    async def get(self, user_id: str, key: str) -> str | None:
        from nacl.secret import SecretBox

        db = await _get_db()
        async with db.execute(
            "SELECT ciphertext, nonce FROM secrets WHERE user_id = ? AND key = ?",
            (user_id, key),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        box = SecretBox(self._get_key(user_id))
        return box.decrypt(bytes(row[0]), bytes(row[1])).decode()

    async def set(self, user_id: str, key: str, value: str) -> None:
        import os

        from nacl.secret import SecretBox

        box = SecretBox(self._get_key(user_id))
        nonce = os.urandom(SecretBox.NONCE_SIZE)
        ciphertext = box.encrypt(value.encode(), nonce).ciphertext

        db = await _get_db()
        await db.execute(
            "INSERT OR REPLACE INTO secrets (user_id, key, ciphertext, nonce) VALUES (?, ?, ?, ?)",
            (user_id, key, ciphertext, nonce),
        )
        await db.commit()

    async def delete(self, user_id: str, key: str) -> None:
        db = await _get_db()
        await db.execute(
            "DELETE FROM secrets WHERE user_id = ? AND key = ?", (user_id, key)
        )
        await db.commit()

    async def list_keys(self, user_id: str) -> list[str]:
        db = await _get_db()
        async with db.execute(
            "SELECT key FROM secrets WHERE user_id = ?", (user_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Backend Postgres — SecretsDB protocol (F7 — complete mode)
# ---------------------------------------------------------------------------


class PostgresSecretsDB:
    """Implementação Postgres do protocolo ``SecretsDB``.

    Armazena ``ciphertext`` e ``nonce`` em bytes nativos (Postgres BYTEA).
    A criptografia (NaCl SecretBox) é responsabilidade da camada de serviço —
    este backend apenas persiste bytes opacos, sem conhecimento da chave.

    Usa o pool asyncpg de ``storage.factory.get_pg_pool()``.
    """

    async def health(self) -> dict[str, object]:
        """Verifica se a conexão Postgres está acessível."""
        try:
            from backend.storage.factory import get_pg_pool

            pool = await get_pg_pool()
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return {"ok": True, "error": None}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def get(self, user_id: str, key: str) -> bytes | None:
        """Retorna o ciphertext cifrado ou None se não existir."""
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT ciphertext FROM secrets WHERE user_id = $1 AND key = $2",
                user_id,
                key,
            )
        return bytes(row["ciphertext"]) if row else None

    async def set(
        self, user_id: str, key: str, ciphertext: bytes, nonce: bytes
    ) -> None:
        """Grava ou substitui o segredo cifrado."""
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO secrets (user_id, key, ciphertext, nonce)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, key) DO UPDATE
                    SET ciphertext = EXCLUDED.ciphertext,
                        nonce      = EXCLUDED.nonce
                """,
                user_id,
                key,
                ciphertext,
                nonce,
            )

    async def delete(self, user_id: str, key: str) -> None:
        """Remove o segredo (sem erro se não existir)."""
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM secrets WHERE user_id = $1 AND key = $2",
                user_id,
                key,
            )
