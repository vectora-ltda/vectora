"""Tokens de serviço (máquina-a-máquina) — não é um usuário, é uma
credencial que uma rota específica opta por aceitar além do JWT normal.

Mesmo padrão de token opaco hasheado que `invites`/`refresh_tokens` já
usam em `backend.rbac.auth` (SHA-256, nunca o token cru persistido).
Primeiro consumidor real: automação de webhook (feature Pro) —
hoje não existe credencial própria de máquina pra disparar essas rotas
fora do fluxo de login humano.

Tabela `service_tokens` vive no mesmo banco de `users`/`invites`
(``~/.vectora/checkpoints.db``, bootstrapped por
``backend.rbac.auth._ensure_schema``) — reusa a mesma conexão, não abre
banco novo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ServiceToken(BaseModel):
    """Token de serviço — saída segura (sem hash nem token cru)."""

    id: str
    name: str
    scopes: list[str]
    created_at: str
    revoked_at: str | None = None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_token() -> str:
    """Token opaco com prefixo `vst_` (Vectora Service Token) — mesma
    convenção de prefixo legível de provedores reais (ex. `sk_`/`ghp_`),
    facilita reconhecer o tipo de credencial num log/diff sem decodificar
    nada."""
    return f"vst_{secrets.token_hex(32)}"


async def create_service_token(
    db: Any, name: str, scopes: list[str], *, created_by: str | None = None
) -> tuple[ServiceToken, str]:
    """Cria um token de serviço novo. Retorna `(ServiceToken, token_cru)`
    — o token cru só existe neste retorno, nunca é recuperável depois
    (mesmo padrão de refresh token: só o hash fica persistido)."""
    token_id = str(uuid.uuid4())
    raw_token = _generate_token()
    now = datetime.now(UTC).isoformat()

    await db.execute(
        """INSERT INTO service_tokens (id, name, token_hash, scopes_json,
           created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
        (token_id, name, _hash_token(raw_token), json.dumps(scopes), created_by, now),
    )
    await db.commit()

    return (
        ServiceToken(id=token_id, name=name, scopes=scopes, created_at=now),
        raw_token,
    )


async def verify_service_token(db: Any, raw_token: str) -> ServiceToken | None:
    """Valida um token de serviço. `None` se inválido, revogado ou
    inexistente — nunca levanta exceção, nunca vaza qual dessas três
    coisas aconteceu (evita side-channel de enumeração)."""
    if not raw_token:
        return None

    async with db.execute(
        "SELECT * FROM service_tokens WHERE token_hash = ?",
        (_hash_token(raw_token),),
    ) as cur:
        row = await cur.fetchone()

    if row is None or row["revoked_at"] is not None:
        return None

    return ServiceToken(
        id=row["id"],
        name=row["name"],
        scopes=json.loads(row["scopes_json"]),
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )


async def revoke_service_token(db: Any, token_id: str) -> bool:
    """Revoga um token de serviço. `True` se um token foi de fato revogado
    (existia e ainda não estava revogado); `False` caso contrário —
    idempotente, revogar duas vezes não é erro, só a segunda vez retorna
    `False`."""
    now = datetime.now(UTC).isoformat()
    cur = await db.execute(
        "UPDATE service_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
        (now, token_id),
    )
    await db.commit()
    return cur.rowcount > 0


async def list_service_tokens(db: Any) -> list[ServiceToken]:
    """Lista todos os tokens de serviço (revogados inclusos, pra
    auditoria) — nunca o token cru, só metadados."""
    async with db.execute(
        "SELECT * FROM service_tokens ORDER BY created_at DESC"
    ) as cur:
        rows = await cur.fetchall()
    return [
        ServiceToken(
            id=r["id"],
            name=r["name"],
            scopes=json.loads(r["scopes_json"]),
            created_at=r["created_at"],
            revoked_at=r["revoked_at"],
        )
        for r in rows
    ]


def has_scope(token: ServiceToken, required_scope: str) -> bool:
    """`True` se `token` tem `required_scope` explicitamente, ou o
    coringa `"*"` (acesso total — só pra tokens administrativos, nunca o
    default de `create_service_token`)."""
    return required_scope in token.scopes or "*" in token.scopes
