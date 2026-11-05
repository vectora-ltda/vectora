"""Provider de secrets usando KeePassXC .kdbx via pykeepass.

Cada usuário tem um arquivo .kdbx isolado em:
    ~/.vectora/secrets/users/<user_id>.kdbx

O master password do .kdbx é derivado da senha de login do usuário
via PBKDF2-SHA256 — sem senha extra para o usuário memorizar.

O arquivo é aberto no login e fechado no logout; em repouso, os secrets
ficam protegidos pela criptografia AES-256 + Argon2id do formato KDBX4.

Compatibilidade: o .kdbx pode ser aberto no KeePassXC desktop para auditoria
manual sem precisar de nenhuma ferramenta especial.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SECRETS_DIR = Path.home() / ".vectora" / "secrets" / "users"

# Handles abertos em memória: user_id → pykeepass.PyKeePass
_open_vaults: dict[str, object] = {}


def _derive_master_password(user_id: str, login_password: str) -> str:
    """Deriva a master password do .kdbx a partir da senha de login.

    Usa PBKDF2-SHA256 com o user_id como salt — determinístico, sem precisar
    armazenar a master password em lugar nenhum.
    """
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        login_password.encode(),
        user_id.encode(),
        iterations=200_000,
    )
    return dk.hex()


def _db_path(user_id: str) -> Path:
    _SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    return _SECRETS_DIR / f"{user_id}.kdbx"


def _get_vault(user_id: str) -> Any:
    """Retorna o vault aberto para o usuário, ou levanta RuntimeError."""
    vault = _open_vaults.get(user_id)
    if vault is None:
        raise RuntimeError(
            f"Vault do usuário {user_id!r} não está aberto. "
            "Chame unlock() após o login."
        )
    return vault


class KeePassSecretsProvider:
    """Provider de secrets usando KeePassXC .kdbx (pykeepass)."""

    async def unlock(self, user_id: str, master_password: str) -> None:
        """Abre (ou cria) o vault .kdbx do usuário.

        Se o arquivo ainda não existir, é criado com o master password derivado.
        """
        import asyncio

        from pykeepass import PyKeePass, create_database

        mp = _derive_master_password(user_id, master_password)
        path = _db_path(user_id)

        def _open() -> object:
            if not path.exists():
                logger.info("secrets/keepass: criando vault para user_id=%s", user_id)
                kp = create_database(str(path), password=mp)
                # Garante que o grupo raiz existe
                if not kp.root_group:
                    kp.add_group(kp.root_group, "Vectora")
                kp.save()
                return kp
            return PyKeePass(str(path), password=mp)

        try:
            vault = await asyncio.get_event_loop().run_in_executor(None, _open)
            _open_vaults[user_id] = vault
            logger.debug("secrets/keepass: vault aberto para user_id=%s", user_id)
        except Exception as exc:
            logger.exception("secrets/keepass: falha ao abrir vault: %s", exc)
            raise

    async def lock(self, user_id: str) -> None:
        """Fecha o vault e remove o handle em memória."""
        _open_vaults.pop(user_id, None)
        logger.debug("secrets/keepass: vault fechado para user_id=%s", user_id)

    async def get(self, user_id: str, key: str) -> str | None:
        import asyncio

        vault = _get_vault(user_id)

        def _read() -> str | None:
            entries = vault.find_entries(title=key, first=True)
            if entries is None:
                return None
            return entries.password

        return await asyncio.get_event_loop().run_in_executor(None, _read)

    async def set(self, user_id: str, key: str, value: str) -> None:
        import asyncio

        vault = _get_vault(user_id)

        def _write() -> None:
            entry = vault.find_entries(title=key, first=True)
            if entry is None:
                vault.add_entry(
                    vault.root_group, title=key, username="", password=value
                )
            else:
                entry.password = value
            vault.save()

        await asyncio.get_event_loop().run_in_executor(None, _write)

    async def delete(self, user_id: str, key: str) -> None:
        import asyncio

        vault = _get_vault(user_id)

        def _del() -> None:
            entry = vault.find_entries(title=key, first=True)
            if entry is not None:
                vault.delete_entry(entry)
                vault.save()

        await asyncio.get_event_loop().run_in_executor(None, _del)

    async def list_keys(self, user_id: str) -> list[str]:
        import asyncio

        vault = _get_vault(user_id)

        def _list() -> list[str]:
            return [e.title for e in vault.entries if e.title]

        return await asyncio.get_event_loop().run_in_executor(None, _list)


# Singleton — mesmo padrão do checkpointer e LanceDB
_provider: KeePassSecretsProvider | None = None


def get_provider() -> KeePassSecretsProvider:
    global _provider
    if _provider is None:
        _provider = KeePassSecretsProvider()
    return _provider
