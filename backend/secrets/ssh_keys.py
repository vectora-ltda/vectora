"""Armazenamento de chaves SSH por usuário.

Cada usuário tem um diretório ``~/.vectora/ssh-keys/<user_id>/`` (modo
0700) onde chaves privadas ficam como arquivos individuais nomeados
pelo ``key_id`` (sha256[:12] do conteúdo). Permissões de arquivo são
0600.

Esta é uma implementação baseada em arquivos, sem integração com o
vault KeePassXC (mesmo storage de secrets usado em outras partes do
sistema) — essa integração exigiria unlock por sessão, que ainda não
está disponível para processos não-interativos como
``get_transport()``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _user_dir(user_id: str) -> Path:
    base = Path.home() / ".vectora" / "ssh-keys" / user_id
    base.mkdir(parents=True, exist_ok=True)
    # POSIX-only: trava o diretório do user. Em Windows o ACL é
    # gerenciado pelo sistema; chmod vira no-op silencioso.
    with contextlib.suppress(OSError):
        base.chmod(0o700)
    return base


def derive_key_id(content: bytes) -> str:
    """ID determinístico do conteúdo da chave (sha256[:12])."""
    return hashlib.sha256(content).hexdigest()[:12]


def add_ssh_key(user_id: str, content: bytes) -> str:
    """Armazena uma chave SSH; retorna o ``key_id``."""
    key_id = derive_key_id(content)
    path = _user_dir(user_id) / key_id
    path.write_bytes(content)
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    logger.info("ssh_keys: chave adicionada user=%s key_id=%s", user_id, key_id)
    return key_id


def list_ssh_keys(user_id: str) -> list[str]:
    """Lista os ``key_id``s armazenados para o usuário."""
    return sorted(p.name for p in _user_dir(user_id).iterdir() if p.is_file())


def remove_ssh_key(user_id: str, key_id: str) -> bool:
    """Remove a chave. True se existia, False caso contrário."""
    path = _user_dir(user_id) / key_id
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


async def get_ssh_key_bytes(user_id: str, key_id: str) -> bytes | None:
    """Lê a chave do disco; usado pelo SshTransport."""
    path = _user_dir(user_id) / key_id

    def _read() -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    return await asyncio.to_thread(_read)
