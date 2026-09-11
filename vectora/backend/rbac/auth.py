"""Serviço de autenticação e identidade do Vectora.

Responsável por:
- Definição dos modelos de identidade (User, Role, Credentials)
- Gerenciamento de tabelas SQLite (users, refresh_tokens)
- Hash seguro de senhas com Argon2id (argon2-cffi)
- Geração e validação de JWT (python-jose, HS256)
- Ciclo de vida dos tokens: access (15min) + refresh (7d, opaque, rotacionado)

Banco de dados: ~/.vectora/checkpoints.db (compartilhado com outras tabelas
de sessão/thread do backend).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

from backend.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes configuráveis via env
# ---------------------------------------------------------------------------

_SECRET_KEY_PATH = settings.vectora_home / "auth.key"
_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("VECTORA_ACCESS_TOKEN_MINUTES", "15"))
_REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("VECTORA_REFRESH_TOKEN_DAYS", "7"))
_ALGORITHM = "HS256"

# ---------------------------------------------------------------------------
# Modelos Pydantic
# ---------------------------------------------------------------------------

Role = Literal["root", "admin", "member", "viewer"]


class UsernameTakenError(ValueError):
    """Username já em uso — distinto de email duplicado (mapeia pra HTTP 409)."""


class User(BaseModel):
    """Usuário autenticado — saída segura (sem password_hash)."""

    id: str
    # Identidade do app é por username; email é opcional (pertence ao
    # company/services, não ao app local).
    username: str = ""
    email: str = ""
    role: Role
    name: str = ""
    env_overrides: dict[str, str] = Field(default_factory=dict)
    created_at: str
    last_login_at: str | None = None


class UserInDB(User):
    """Usuário com hash de senha — uso interno apenas."""

    password_hash: str
    env_overrides_json: str = "{}"


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    user: User


class Credentials(BaseModel):
    email: str
    password: str


# ---------------------------------------------------------------------------
# Gerenciamento da chave secreta JWT
# ---------------------------------------------------------------------------


def _load_or_create_secret_key() -> str:
    """Carrega (ou cria) a chave secreta para assinatura JWT.

    O arquivo tem permissões 600 no Unix. No Windows, apenas o criador tem
    acesso por padrão (sem chmod explícito — NTFS herda da pasta pai).
    """
    _SECRET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _SECRET_KEY_PATH.exists():
        return _SECRET_KEY_PATH.read_text().strip()
    key = secrets.token_hex(64)  # 512 bits
    _SECRET_KEY_PATH.write_text(key)
    with contextlib.suppress(AttributeError, NotImplementedError):
        _SECRET_KEY_PATH.chmod(0o600)  # Windows — sem suporte a chmod
    logger.info("auth: nova chave JWT gerada em %s", _SECRET_KEY_PATH)
    return key


_SECRET_KEY: str | None = None


def _get_secret() -> str:
    global _SECRET_KEY
    if _SECRET_KEY is None:
        _SECRET_KEY = _load_or_create_secret_key()
    return _SECRET_KEY


# ---------------------------------------------------------------------------
# Hashing de senha (Argon2id via argon2-cffi)
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Retorna um hash Argon2id seguro para a senha fornecida."""
    from argon2 import PasswordHasher

    ph = PasswordHasher(
        time_cost=3,
        memory_cost=65536,  # 64 MiB
        parallelism=2,
        hash_len=32,
        salt_len=16,
    )
    return ph.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verifica senha. Retorna True se válida; nunca lança exceção para o caller."""
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError

    ph = PasswordHasher()
    try:
        ph.verify(hashed, plain)
        return True
    except VerifyMismatchError:
        return False
    except Exception as exc:
        logger.warning("auth: verify_password erro inesperado: %s", exc)
        return False


# ---------------------------------------------------------------------------
# JWT — access token
# ---------------------------------------------------------------------------


def create_access_token(user: User) -> str:
    """Emite um JWT de acesso com vida útil de ACCESS_TOKEN_EXPIRE_MINUTES."""
    import jwt

    now = datetime.now(UTC)
    payload = {
        "sub": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=_ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Valida e decodifica JWT. Lança JWTError/ExpiredSignatureError em falha."""
    import jwt

    return jwt.decode(token, _get_secret(), algorithms=[_ALGORITHM])


# ---------------------------------------------------------------------------
# Refresh token — opaque, hash armazenado no DB
# ---------------------------------------------------------------------------


def _hash_refresh_token(token: str) -> str:
    """SHA-256 do token opaco — armazenado no banco."""
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_refresh_token() -> str:
    """Token opaco de 64 bytes hex (512 bits de entropia)."""
    return secrets.token_hex(64)


# ---------------------------------------------------------------------------
# Conexão ao banco
# ---------------------------------------------------------------------------

_db_conn: Any = None


async def close_db() -> None:
    """Fecha a conexão compartilhada antes de substituir ``checkpoints.db``."""
    global _db_conn
    connection = _db_conn
    _db_conn = None
    if connection is not None:
        await connection.close()


async def _get_db() -> Any:
    """Retorna conexão aiosqlite compartilhada com os schemas de auth.

    Garantia de produto: usuários, sessões/refresh tokens, audit e invites
    SEMPRE residem em SQLite (~/.vectora/checkpoints.db), independente de
    ``settings.storage_mode``. Mesmo no modo "complete" (Postgres + Qdrant +
    Redis), este SQLite continua sendo a fonte de verdade — funciona como
    fallback garantido. Não trocar esta função para retornar um pool Postgres;
    ver ``PostgresAuthDB`` abaixo para o motivo de não estar em uso.
    """
    from backend.services.maintenance import wait_until_available

    await wait_until_available()
    global _db_conn
    if _db_conn is not None:
        return _db_conn

    import aiosqlite

    db_path = settings.vectora_home / "checkpoints.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _db_conn = await aiosqlite.connect(str(db_path))
    _db_conn.row_factory = aiosqlite.Row
    # Mesmos PRAGMAs de threads.py::_get_db() e do checkpointer em
    # agent_factory.py (D2) — sem busy_timeout, escritas concorrentes de
    # outra conexão pro mesmo checkpoints.db batem em "database is locked"
    # na hora em vez de esperar.
    await _db_conn.executescript(
        "PRAGMA journal_mode=WAL;PRAGMA busy_timeout=30000;PRAGMA synchronous=NORMAL;"
    )
    await _ensure_schema(_db_conn)
    return _db_conn


async def _ensure_schema(db: Any) -> None:
    """Cria tabelas de auth se não existirem."""
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id                 TEXT PRIMARY KEY,
            email              TEXT NOT NULL UNIQUE,
            password_hash      TEXT NOT NULL,
            role               TEXT NOT NULL DEFAULT 'member',
            name               TEXT NOT NULL DEFAULT '',
            env_overrides_json TEXT NOT NULL DEFAULT '{}',
            created_at         TEXT NOT NULL,
            last_login_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS refresh_tokens (
            token_hash  TEXT    PRIMARY KEY,
            user_id     TEXT    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at  TEXT    NOT NULL,
            revoked     INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit (
            id            TEXT    PRIMARY KEY,
            user_id       TEXT,
            action        TEXT    NOT NULL,
            target_type   TEXT,
            target_id     TEXT,
            timestamp     TEXT    NOT NULL,
            ip            TEXT,
            user_agent    TEXT,
            success       INTEGER NOT NULL DEFAULT 1,
            metadata_json TEXT    NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS invites (
            token_hash  TEXT    PRIMARY KEY,
            email       TEXT,
            role        TEXT    NOT NULL DEFAULT 'member',
            created_by  TEXT,
            expires_at  TEXT    NOT NULL,
            used_at     TEXT,
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS service_tokens (
            id          TEXT    PRIMARY KEY,
            name        TEXT    NOT NULL,
            token_hash  TEXT    NOT NULL UNIQUE,
            scopes_json TEXT    NOT NULL DEFAULT '[]',
            created_by  TEXT,
            created_at  TEXT    NOT NULL,
            revoked_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS password_resets (
            token_hash  TEXT    PRIMARY KEY,
            user_id     TEXT    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at  TEXT    NOT NULL,
            used_at     TEXT,
            created_at  TEXT    NOT NULL
        );
    """)
    # Migrations idempotentes: ALTER TABLE para colunas adicionadas após o
    # release inicial. SQLite não tem "ADD COLUMN IF NOT EXISTS", então
    # capturamos o erro de coluna duplicada.
    with contextlib.suppress(Exception):
        await db.execute("ALTER TABLE users ADD COLUMN name TEXT NOT NULL DEFAULT ''")
    with contextlib.suppress(Exception):
        await db.execute(
            "ALTER TABLE users ADD COLUMN username TEXT NOT NULL DEFAULT ''"
        )
    await db.commit()
    # Backfill de username para rows que ainda não têm, seguido de índice
    # único parcial (ignora '' — só existe transitoriamente antes do
    # backfill; a partir daqui todo usuário tem username preenchido).
    await _backfill_usernames(db)
    with contextlib.suppress(Exception):
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username "
            "ON users(username) WHERE username != ''"
        )
    await db.commit()


async def _backfill_usernames(db: Any) -> None:
    """Preenche ``username`` para rows que ainda não têm.

    Deriva do ``name`` (ou do local-part do email), garantindo unicidade dentro
    do próprio backfill — colisão vira ``base#NNNN``, o mesmo formato do signup.
    """
    from backend.rbac.username import unique_username

    async with db.execute("SELECT id, name, email, username FROM users") as cur:
        rows = await cur.fetchall()

    taken = {r["username"] for r in rows if r["username"]}
    changed = False
    for r in rows:
        if r["username"]:
            continue
        base = r["name"] or ((r["email"] or "").split("@")[0])
        uname = unique_username(base, lambda u: u in taken)
        taken.add(uname)
        await db.execute("UPDATE users SET username = ? WHERE id = ?", (uname, r["id"]))
        changed = True
    if changed:
        await db.commit()


# ---------------------------------------------------------------------------
# Helpers internos de banco
# ---------------------------------------------------------------------------


def _col(row: Any, key: str) -> str:
    """Lê uma coluna textual da row tolerando ausência (rows legadas/parciais)."""
    try:
        return row[key] or ""
    except (IndexError, KeyError):
        return ""


def _row_to_user(row: Any) -> UserInDB:
    import json

    env = {}
    with contextlib.suppress(Exception):
        env = json.loads(row["env_overrides_json"] or "{}")
    # Acesso tolerante à ausência da coluna: rows de banco sem o ALTER
    # aplicado (teste, ou schema desatualizado) não devem quebrar a leitura.
    try:
        name = row["name"] or ""
    except (IndexError, KeyError):
        name = ""
    from backend.rbac.username import slugify_username

    # username é coluna persistida; se o backfill ainda não rodou nesta
    # row, cai no slug do nome como fallback de leitura.
    try:
        username = row["username"] or ""
    except (IndexError, KeyError):
        username = ""
    if not username and name:
        username = slugify_username(name)

    return UserInDB(
        id=row["id"],
        username=username,
        email=row["email"],
        role=row["role"],
        name=name,
        env_overrides=env,
        env_overrides_json=row["env_overrides_json"],
        password_hash=row["password_hash"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


async def _count_users(db: Any) -> int:
    async with db.execute("SELECT COUNT(*) FROM users") as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# API pública do serviço
# ---------------------------------------------------------------------------


async def has_users() -> bool:
    """True se já existe pelo menos um usuário cadastrado."""
    db = await _get_db()
    return await _count_users(db) > 0


async def setup_complete() -> bool:
    """True quando a instância já passou pelo wizard, em qualquer modo.

    Não basta olhar a tabela `users`: o modo local (sem conta) grava o
    perfil em `app_settings` via ``runtime_settings.set_local_user`` e
    deliberadamente não cria linha em `users` (ver
    ``api/handlers/auth.py::setup_local_endpoint``). Perguntar só
    ``has_users()`` faz uma instância local configurada parecer virgem
    para sempre.
    """
    if await has_users():
        return True
    from backend.workspace.runtime_settings import runtime_settings

    return bool(runtime_settings.local_user_name.strip())


async def username_taken(username: str) -> bool:
    """True se o username (normalizado) já pertence a algum usuário."""
    from backend.rbac.username import normalize_username

    norm = normalize_username(username)
    db = await _get_db()
    async with db.execute(
        "SELECT 1 FROM users WHERE username = ? LIMIT 1", (norm,)
    ) as cur:
        return await cur.fetchone() is not None


async def _taken_usernames(db: Any) -> set[str]:
    async with db.execute("SELECT username FROM users") as cur:
        rows = await cur.fetchall()
    return {r["username"] for r in rows if r["username"]}


async def suggest_username(base: str) -> str:
    """Sugere um username livre a partir de ``base`` (nome ou username digitado).

    Devolve o slug de ``base`` se estiver livre; senão ``base#NNNN``.
    """
    from backend.rbac.username import unique_username

    db = await _get_db()
    taken = await _taken_usernames(db)
    return unique_username(base, lambda u: u in taken)


async def signup(
    email: str,
    password: str,
    *,
    role: Role | None = None,
    name: str = "",
    username: str | None = None,
) -> tuple[User, str, str]:
    """Cria um novo usuário.

    O primeiro usuário vira root automaticamente. Quando ``role`` é informado
    (signup via convite), usa a role do convite; caso contrário, member.

    ``name`` aceita qualquer caractere UTF-8 imprimível (espaços e
    acentuação inclusos); limitado a 100 caracteres para evitar abuso.

    ``username`` é a identidade do app. Quando informado, é normalizado e
    precisa estar livre (senão ``UsernameTakenError``); quando ausente, é
    derivado do nome com sufixo de colisão ``#NNNN``.

    Returns:
        (user, access_token, refresh_token)

    Raises:
        UsernameTakenError: username informado já em uso
        ValueError: email já cadastrado ou validação falhou
    """
    if len(password) < 8:
        raise ValueError("Senha deve ter no mínimo 8 caracteres.")

    # Sanitiza o nome: trim, normaliza espaços internos, limita o tamanho.
    name_clean = " ".join(name.split())[:100]

    db = await _get_db()
    count = await _count_users(db)
    if count == 0:
        role = "root"
    elif role is None:
        role = "member"

    from backend.rbac.username import normalize_username, unique_username

    taken = await _taken_usernames(db)
    if username is not None and username.strip():
        uname = normalize_username(username)
        if uname in taken:
            raise UsernameTakenError(f"Nome de usuário '{uname}' já está em uso.")
    else:
        base = name_clean or ((email or "").split("@")[0])
        uname = unique_username(base, lambda u: u in taken)

    now = datetime.now(UTC).isoformat()
    user_id = str(uuid.uuid4())
    ph = hash_password(password)

    try:
        await db.execute(
            "INSERT INTO users (id, email, username, password_hash, role, name, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, email.lower().strip(), uname, ph, role, name_clean, now),
        )
        await db.commit()
    except Exception as exc:
        msg = str(exc)
        if "users.username" in msg or "idx_users_username" in msg:
            raise UsernameTakenError("Nome de usuário já está em uso.") from exc
        if "UNIQUE constraint failed" in msg:
            raise ValueError("E-mail já cadastrado.") from exc
        raise

    user = User(
        id=user_id,
        username=uname,
        email=email.lower().strip(),
        role=role,
        name=name_clean,
        created_at=now,
    )
    access_token = create_access_token(user)
    refresh_token = await _issue_refresh_token(db, user_id)

    await _write_audit(db, user_id, "signup", success=True)
    logger.info("auth: novo usuário criado id=%s role=%s", user_id, role)
    return user, access_token, refresh_token


async def signin(email: str, password: str, *, ip: str = "") -> tuple[User, str, str]:
    """Autentica usuário com email + senha.

    Returns:
        (user, access_token, refresh_token)

    Raises:
        ValueError: credenciais inválidas
    """
    db = await _get_db()
    async with db.execute(
        "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
    ) as cur:
        row = await cur.fetchone()

    if row is None or not verify_password(password, row["password_hash"]):
        await _write_audit(
            db,
            None,
            "signin_failed",
            success=False,
            metadata={"email": email, "ip": ip},
        )
        raise ValueError("Credenciais inválidas.")

    user_in_db = _row_to_user(row)
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "UPDATE users SET last_login_at = ? WHERE id = ?", (now, user_in_db.id)
    )
    await db.commit()

    user = User(
        id=user_in_db.id,
        username=user_in_db.username,
        email=user_in_db.email,
        role=user_in_db.role,
        name=user_in_db.name,
        env_overrides=user_in_db.env_overrides,
        created_at=user_in_db.created_at,
        last_login_at=now,
    )
    access_token = create_access_token(user)
    refresh_token = await _issue_refresh_token(db, user.id)

    await _write_audit(db, user.id, "signin", success=True, metadata={"ip": ip})
    return user, access_token, refresh_token


async def provision_or_login_sso(email: str, name: str = "") -> tuple[User, str, str]:
    """Autentica via SSO/OIDC (`backend.rbac.oidc`) — sem senha, o IDP já
    verificou a identidade. Usuário existente por `email` faz login direto;
    inexistente é provisionado na hora (mesma regra de `signup`: primeiro
    usuário vira root, username derivado do nome/email).

    O `password_hash` do usuário provisionado é um segredo aleatório nunca
    exposto nem usado — login local (email+senha) continua desabilitado
    pra essa conta até uma troca de senha explícita via `change_password`.

    Returns:
        (user, access_token, refresh_token)
    """
    db = await _get_db()
    email_norm = email.lower().strip()
    async with db.execute("SELECT * FROM users WHERE email = ?", (email_norm,)) as cur:
        row = await cur.fetchone()

    if row is None:
        user, access_token, refresh_token = await signup(
            email_norm, secrets.token_hex(32), name=name
        )
        await _write_audit(db, user.id, "sso_provision", success=True)
        return user, access_token, refresh_token

    user_in_db = _row_to_user(row)
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "UPDATE users SET last_login_at = ? WHERE id = ?", (now, user_in_db.id)
    )
    await db.commit()

    user = User(
        id=user_in_db.id,
        username=user_in_db.username,
        email=user_in_db.email,
        role=user_in_db.role,
        name=user_in_db.name,
        env_overrides=user_in_db.env_overrides,
        created_at=user_in_db.created_at,
        last_login_at=now,
    )
    access_token = create_access_token(user)
    refresh_token = await _issue_refresh_token(db, user.id)

    await _write_audit(db, user.id, "sso_signin", success=True)
    return user, access_token, refresh_token


async def refresh_tokens(refresh_token: str) -> tuple[User, str, str]:
    """Valida refresh token, emite novo par de tokens (rotação).

    O token antigo é revogado imediatamente.

    Raises:
        ValueError: token inválido, expirado ou revogado
    """
    db = await _get_db()
    token_hash = _hash_refresh_token(refresh_token)
    now_str = datetime.now(UTC).isoformat()

    async with db.execute(
        "SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        raise ValueError("Refresh token inválido.")
    if row["revoked"]:
        raise ValueError("Refresh token já foi revogado.")
    if row["expires_at"] < now_str:
        raise ValueError("Refresh token expirado.")

    # Revoga o token atual
    await db.execute(
        "UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?", (token_hash,)
    )

    # Carrega usuário
    async with db.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)) as cur:
        user_row = await cur.fetchone()
    if user_row is None:
        raise ValueError("Usuário não encontrado.")

    user_in_db = _row_to_user(user_row)
    user = User(
        id=user_in_db.id,
        username=user_in_db.username,
        email=user_in_db.email,
        role=user_in_db.role,
        name=user_in_db.name,
        env_overrides=user_in_db.env_overrides,
        created_at=user_in_db.created_at,
        last_login_at=user_in_db.last_login_at,
    )
    access_token = create_access_token(user)
    new_refresh = await _issue_refresh_token(db, user.id)

    await _write_audit(db, user.id, "refresh_token_rotation", success=True)
    return user, access_token, new_refresh


async def signout(refresh_token: str) -> None:
    """Revoga refresh token (logout)."""
    db = await _get_db()
    token_hash = _hash_refresh_token(refresh_token)
    await db.execute(
        "UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?", (token_hash,)
    )
    await db.commit()
    await _write_audit(db, None, "signout", success=True)


async def get_user_by_id(user_id: str) -> User | None:
    """Retorna User pelo ID, ou None se não existir."""
    db = await _get_db()
    async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    u = _row_to_user(row)
    return User(
        id=u.id,
        username=u.username,
        email=u.email,
        role=u.role,
        name=u.name,
        env_overrides=u.env_overrides,
        created_at=u.created_at,
        last_login_at=u.last_login_at,
    )


async def update_profile(user_id: str, *, name: str) -> User:
    """Atualiza campos do perfil do usuário (atualmente: nome).

    ``name`` aceita UTF-8 livre, espaços, acentos; trim+normalização interna,
    limite de 100 caracteres.

    Raises:
        ValueError: usuário não encontrado.
    """
    name_clean = " ".join(name.split())[:100]
    db = await _get_db()
    cur = await db.execute(
        "UPDATE users SET name = ? WHERE id = ?", (name_clean, user_id)
    )
    await db.commit()
    if cur.rowcount == 0:
        raise ValueError("Usuário não encontrado.")
    updated = await get_user_by_id(user_id)
    if updated is None:
        raise ValueError("Usuário não encontrado.")
    return updated


async def change_password(user_id: str, old_password: str, new_password: str) -> None:
    """Muda senha do usuário após validar a senha atual.

    Raises:
        ValueError: senha atual incorreta ou nova senha inválida
    """
    if len(new_password) < 8:
        raise ValueError("Nova senha deve ter no mínimo 8 caracteres.")

    db = await _get_db()
    async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise ValueError("Usuário não encontrado.")
    if not verify_password(old_password, row["password_hash"]):
        raise ValueError("Senha atual incorreta.")

    new_hash = hash_password(new_password)
    await db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id)
    )
    # Revoga todos os refresh tokens existentes ao trocar senha
    await db.execute(
        "UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (user_id,)
    )
    await db.commit()
    await _write_audit(db, user_id, "change_password", success=True)


#: TTL do token de reset de senha — bem mais curto que convite (24h): a
#: janela de exposição de "alguém com acesso ao email pode entrar" deve
#: ser mínima.
_PASSWORD_RESET_TTL_HOURS = 1


async def request_password_reset(email: str) -> str | None:
    """Gera um token de reset de senha pro usuário com esse email.

    Retorna o token cru (só pra quem chama poder enviar por email — o
    handler REST nunca devolve isso na resposta HTTP, evita side-channel
    de enumeração de conta) ou `None` se o email não corresponde a
    nenhum usuário. Mesmo padrão de `create_invite` — token opaco, só o
    hash SHA-256 persistido.
    """
    db = await _get_db()
    async with db.execute(
        "SELECT id FROM users WHERE email = ?", (email.lower().strip(),)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None

    token = secrets.token_hex(32)
    now = datetime.now(UTC)
    expires_at = (now + timedelta(hours=_PASSWORD_RESET_TTL_HOURS)).isoformat()
    await db.execute(
        """INSERT INTO password_resets (token_hash, user_id, expires_at, created_at)
           VALUES (?, ?, ?, ?)""",
        (_hash_token(token), row["id"], expires_at, now.isoformat()),
    )
    await db.commit()
    await _write_audit(db, row["id"], "password_reset_request", success=True)
    return token


async def confirm_password_reset(token: str, new_password: str) -> None:
    """Consome um token de reset de senha e define a nova senha.

    Raises:
        ValueError: token inexistente, já usado, expirado, ou senha nova
            inválida (< 8 caracteres) — mesmas mensagens/regra de
            `change_password`, sem exigir a senha atual (o token já prova
            posse do email).
    """
    if len(new_password) < 8:
        raise ValueError("Nova senha deve ter no mínimo 8 caracteres.")

    db = await _get_db()
    now_str = datetime.now(UTC).isoformat()
    async with db.execute(
        "SELECT * FROM password_resets WHERE token_hash = ?", (_hash_token(token),)
    ) as cur:
        row = await cur.fetchone()

    if row is None or row["used_at"] is not None or row["expires_at"] < now_str:
        raise ValueError("Token de recuperação inválido, já usado ou expirado.")

    new_hash = hash_password(new_password)
    await db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, row["user_id"])
    )
    await db.execute(
        "UPDATE password_resets SET used_at = ? WHERE token_hash = ?",
        (now_str, _hash_token(token)),
    )
    # Mesma cautela de change_password: reset de senha revoga todas as
    # sessões existentes — se a conta foi comprometida, o reset também
    # encerra o acesso do invasor.
    await db.execute(
        "UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (row["user_id"],)
    )
    await db.commit()
    await _write_audit(db, row["user_id"], "password_reset_confirm", success=True)


# ---------------------------------------------------------------------------
# Env overrides por usuário (C10)
# ---------------------------------------------------------------------------


_LOCAL_ENV_OVERRIDES_KEY = "local_env_overrides"


def _local_env_overrides() -> dict[str, str]:
    """Lê `local_env_overrides` de `runtime_settings`, tipado — `get()`
    devolve `object` genérico (chave pode nunca ter sido setada)."""
    from backend.workspace.runtime_settings import runtime_settings

    raw = runtime_settings.get(_LOCAL_ENV_OVERRIDES_KEY, {})
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


async def get_env_overrides(user_id: str) -> dict[str, str]:
    """Retorna os env overrides do usuário.

    O usuário virtual `"local"` (modo sem conta, `AuthMiddleware`) nunca tem
    linha própria em `users` — `UPDATE`/`SELECT` contra a tabela viram
    no-op silencioso pra ele. Persiste via `runtime_settings` (SQLite
    `app_settings`, mesmo mecanismo já usado por `local_user_name`/
    `storage_mode`) em vez de inventar storage novo.
    """
    if user_id == "local":
        return _local_env_overrides()

    import json

    db = await _get_db()
    async with db.execute(
        "SELECT env_overrides_json FROM users WHERE id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return {}
    try:
        return json.loads(row[0] or "{}")
    except Exception:
        return {}


#: Prefixo de chave-sombra no mesmo blob JSON de overrides — não é um env
#: var real (nomes de env var nunca começam com `__`), então não colide.
#: Marca que o override em `key` veio do fluxo OAuth, não de um token colado
#: manualmente — só `oauth.py`'s callbacks passam `source="oauth"`; todo
#: outro caller (settings manuais, `tools/library.py`) fica no default
#: "manual". Usado por `oauth.py::list_integrations` pra distinguir
#: "tem override setado" (`connected`) de "conectou via OAuth de verdade"
#: (`oauth_connected`) — sem isso, colar um token manual pra um provider
#: `hybrid` fazia a UI mostrar "Conexão ativa (OAuth)" por engano.
_OAUTH_SOURCE_PREFIX = "__oauth_source__:"


async def set_env_override(
    user_id: str, key: str, value: str, *, source: str = "manual"
) -> None:
    """Define (ou sobrescreve) um env override para o usuário.

    Rejeita `key` iniciada por `_OAUTH_SOURCE_PREFIX` — sem isso, uma
    escrita manual (`POST /auth/envs`, que aceita qualquer nome de chave)
    poderia forjar `__oauth_source__:GITHUB_TOKEN` e, se `GITHUB_TOKEN` já
    existisse, fazer `is_oauth_sourced` mentir que veio de OAuth."""
    if key.startswith(_OAUTH_SOURCE_PREFIX):
        msg = f"chave reservada, não pode ser setada diretamente: {key!r}"
        raise ValueError(msg)
    if user_id == "local":
        from backend.workspace.runtime_settings import runtime_settings

        overrides = _local_env_overrides()
        overrides[key] = value
        _apply_oauth_source(overrides, key, source)
        runtime_settings.set(_LOCAL_ENV_OVERRIDES_KEY, overrides)
        return

    import json

    overrides = await get_env_overrides(user_id)
    overrides[key] = value
    _apply_oauth_source(overrides, key, source)
    db = await _get_db()
    await db.execute(
        "UPDATE users SET env_overrides_json = ? WHERE id = ?",
        (json.dumps(overrides), user_id),
    )
    await db.commit()


def _apply_oauth_source(overrides: dict[str, str], key: str, source: str) -> None:
    shadow_key = _OAUTH_SOURCE_PREFIX + key
    if source == "oauth":
        overrides[shadow_key] = "1"
    else:
        overrides.pop(shadow_key, None)


async def delete_env_override(user_id: str, key: str) -> None:
    """Remove um env override do usuário."""
    if user_id == "local":
        from backend.workspace.runtime_settings import runtime_settings

        overrides = _local_env_overrides()
        overrides.pop(key, None)
        overrides.pop(_OAUTH_SOURCE_PREFIX + key, None)
        runtime_settings.set(_LOCAL_ENV_OVERRIDES_KEY, overrides)
        return

    import json

    overrides = await get_env_overrides(user_id)
    overrides.pop(key, None)
    overrides.pop(_OAUTH_SOURCE_PREFIX + key, None)
    db = await _get_db()
    await db.execute(
        "UPDATE users SET env_overrides_json = ? WHERE id = ?",
        (json.dumps(overrides), user_id),
    )
    await db.commit()


def is_oauth_sourced(overrides: dict[str, str], key: str) -> bool:
    """`True` se o override de `key` veio do fluxo OAuth (não de um token
    colado manualmente) — ver `_OAUTH_SOURCE_PREFIX`."""
    return overrides.get(_OAUTH_SOURCE_PREFIX + key) == "1"


# ---------------------------------------------------------------------------
# User management (P2 — Admin Panel)
# ---------------------------------------------------------------------------


async def list_users() -> list[User]:
    """Retorna todos os usuários cadastrados."""
    db = await _get_db()
    async with db.execute("SELECT * FROM users ORDER BY created_at") as cur:
        rows = await cur.fetchall()
    return [
        User(
            id=r["id"],
            username=_col(r, "username"),
            email=r["email"],
            role=r["role"],
            name=_col(r, "name"),
            env_overrides={},
            created_at=r["created_at"],
            last_login_at=r["last_login_at"],
        )
        for r in rows
    ]


async def update_user_role(user_id: str, role: str) -> None:
    """Atualiza o role de um usuário."""
    db = await _get_db()
    await db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    await db.commit()


async def delete_user(user_id: str) -> None:
    """Remove um usuário e todos os seus refresh tokens."""
    db = await _get_db()
    await db.execute("DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,))
    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()


# ---------------------------------------------------------------------------
# Convites de signup (Q8)
# ---------------------------------------------------------------------------


def _hash_token(token: str) -> str:
    """SHA-256 de um token opaco — armazenado no banco."""
    return hashlib.sha256(token.encode()).hexdigest()


async def create_invite(
    created_by: str,
    *,
    role: Role = "member",
    email: str | None = None,
    ttl_hours: int = 24,
) -> tuple[str, str]:
    """Cria um convite de signup e retorna (token, expires_at).

    O token é opaco; apenas seu hash SHA-256 é persistido.
    """
    token = secrets.token_hex(32)
    now = datetime.now(UTC)
    expires_at = (now + timedelta(hours=ttl_hours)).isoformat()
    db = await _get_db()
    await db.execute(
        """INSERT INTO invites (token_hash, email, role, created_by, expires_at,
           created_at) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            _hash_token(token),
            (email.lower().strip() if email else None),
            role,
            created_by,
            expires_at,
            now.isoformat(),
        ),
    )
    await db.commit()
    await _write_audit(
        db, created_by, "invite_create", success=True, metadata={"role": role}
    )
    return token, expires_at


async def validate_invite(token: str) -> dict[str, Any] | None:
    """Valida um convite. Retorna {email, role, expires_at} se utilizável.

    Retorna None se o token for inexistente, já usado ou expirado.
    """
    db = await _get_db()
    now_str = datetime.now(UTC).isoformat()
    async with db.execute(
        "SELECT * FROM invites WHERE token_hash = ?", (_hash_token(token),)
    ) as cur:
        row = await cur.fetchone()
    if row is None or row["used_at"] is not None or row["expires_at"] < now_str:
        return None
    return {
        "email": row["email"],
        "role": row["role"],
        "expires_at": row["expires_at"],
    }


async def consume_invite(token: str, user_id: str) -> None:
    """Marca o convite como usado (idempotente — só consome se ainda aberto)."""
    db = await _get_db()
    now_str = datetime.now(UTC).isoformat()
    await db.execute(
        "UPDATE invites SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
        (now_str, _hash_token(token)),
    )
    await db.commit()
    await _write_audit(
        db, user_id, "invite_consume", success=True, target_type="invite"
    )


async def list_invites() -> list[dict[str, Any]]:
    """Lista convites pendentes (não usados e não expirados)."""
    db = await _get_db()
    now_str = datetime.now(UTC).isoformat()
    async with db.execute(
        """SELECT token_hash, email, role, created_by, expires_at, created_at
           FROM invites WHERE used_at IS NULL AND expires_at >= ?
           ORDER BY created_at DESC""",
        (now_str,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "token_hash": r["token_hash"],
            "email": r["email"],
            "role": r["role"],
            "created_by": r["created_by"],
            "expires_at": r["expires_at"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


async def revoke_invite(token_hash: str) -> bool:
    """Revoga um convite pendente pelo seu hash. Retorna True se removido."""
    db = await _get_db()
    async with db.execute(
        "DELETE FROM invites WHERE token_hash = ?", (token_hash,)
    ) as cur:
        deleted = cur.rowcount
    await db.commit()
    return bool(deleted)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


async def _issue_refresh_token(db: Any, user_id: str) -> str:
    """Gera, persiste e retorna um novo refresh token opaco."""
    token = _generate_refresh_token()
    token_hash = _hash_refresh_token(token)
    now = datetime.now(UTC)
    expires_at = (now + timedelta(days=_REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
    await db.execute(
        "INSERT INTO refresh_tokens (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (token_hash, user_id, expires_at, now.isoformat()),
    )
    await db.commit()
    return token


#: Nomes de campo nunca serializados em `metadata_json`, comparados
#: case-insensitive — mesmo padrão de `_REDACTED_FIELDS` do Hermes
#: (`hermes_cli/dashboard_auth/audit.py`). Nenhum call-site atual de
#: `_write_audit` passa esses campos hoje (confirmado por grep antes desta
#: mudança) — a rede de segurança é contra um call-site futuro que passe
#: por engano, não um vazamento já existente.
_REDACTED_METADATA_FIELDS = frozenset(
    {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "cookie",
        "authorization",
    }
)


def _redact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Substitui por `"[REDACTED]"` qualquer chave de `metadata` cujo nome
    (case-insensitive) bata com `_REDACTED_METADATA_FIELDS` — nunca deixa o
    valor real chegar ao audit log persistido."""
    return {
        k: ("[REDACTED]" if k.lower() in _REDACTED_METADATA_FIELDS else v)
        for k, v in metadata.items()
    }


async def _write_audit(
    db: Any,
    user_id: str | None,
    action: str,
    *,
    success: bool = True,
    metadata: dict[str, Any] | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    ip: str = "",
) -> None:
    """Registra evento no audit log (best-effort — nunca propaga exceção)."""
    import json

    try:
        await db.execute(
            """INSERT INTO audit (id, user_id, action, target_type, target_id,
               timestamp, ip, success, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                user_id,
                action,
                target_type,
                target_id,
                datetime.now(UTC).isoformat(),
                ip,
                1 if success else 0,
                json.dumps(_redact_metadata(metadata or {})),
            ),
        )
        await db.commit()
    except Exception as exc:
        logger.warning("auth: falha ao escrever audit log: %s", exc)


# Exportamos write_audit para uso externo (handlers, middleware)
write_audit = _write_audit


async def get_db_for_audit() -> Any:
    """Expõe conexão para handlers externos escreverem audit entries."""
    return await _get_db()


# ---------------------------------------------------------------------------
# Backend Postgres — AuthDB protocol (F7 — NÃO USADO)
# ---------------------------------------------------------------------------


class PostgresAuthDB:
    """Implementação Postgres do protocolo ``AuthDB`` — mantida apenas para
    referência/experimentos, **não conectada** a nenhum endpoint.

    ATENÇÃO: dados de usuários/auth são uma garantia de produto em SQLite
    (sempre disponível como fallback, mesmo no modo "complete" — ver
    ``_get_db()`` acima). Não trocar `_get_db()`/handlers para usar esta
    classe — isso violaria a garantia de "SQLite/JSON sempre, Postgres nunca"
    para users/auth/settings.

    Usa o pool asyncpg de ``storage.factory.get_pg_pool()`` para todas as
    operações. O schema deve ter sido criado via ``vectora storage migrate``
    (migration 0001_auth.sql adaptada para Postgres).

    Placeholders asyncpg: ``$1, $2, ...`` (diferente do ``?`` do SQLite).
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

    async def count_users(self) -> int:
        """Retorna o total de usuários cadastrados."""
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COUNT(*) AS n FROM users")
        return row["n"] if row else 0

    async def create_user(
        self,
        email: str,
        password_hash: str,
        role: str = "member",
        name: str = "",
        user_id: str | None = None,
    ) -> str:
        """Cria um usuário e retorna seu ID."""
        import uuid
        from datetime import UTC, datetime

        from backend.storage.factory import get_pg_pool

        uid = user_id or str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users
                    (id, email, password_hash, role, name,
                     env_overrides_json, created_at)
                VALUES ($1, $2, $3, $4, $5, '{}', $6)
                """,
                uid,
                email,
                password_hash,
                role,
                name,
                now,
            )
        return uid

    async def get_user_by_email(self, email: str) -> dict[str, object] | None:
        """Retorna o usuário pelo e-mail ou None."""
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
        return dict(row) if row else None

    async def get_user_by_id(self, user_id: str) -> dict[str, object] | None:
        """Retorna o usuário pelo ID ou None."""
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        return dict(row) if row else None

    async def upsert_refresh_token(
        self,
        token_hash: str,
        user_id: str,
        expires_at: str,
    ) -> None:
        """Insere ou substitui um refresh token."""
        from datetime import UTC, datetime

        from backend.storage.factory import get_pg_pool

        now = datetime.now(UTC).isoformat()
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO refresh_tokens
                    (token_hash, user_id, expires_at, revoked, created_at)
                VALUES ($1, $2, $3, false, $4)
                ON CONFLICT (token_hash) DO UPDATE
                    SET expires_at = EXCLUDED.expires_at, revoked = false
                """,
                token_hash,
                user_id,
                expires_at,
                now,
            )

    async def revoke_refresh_tokens(self, user_id: str) -> None:
        """Revoga todos os refresh tokens do usuário."""
        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM refresh_tokens WHERE user_id = $1", user_id)

    async def write_audit(
        self,
        user_id: str | None,
        action: str,
        target_type: str | None = None,
        target_id: str | None = None,
        metadata_json: str = "{}",
        ip: str | None = None,
    ) -> None:
        """Grava uma entrada de auditoria."""
        import uuid
        from datetime import UTC, datetime

        from backend.storage.factory import get_pg_pool

        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO audit
                        (id, user_id, action, target_type, target_id,
                         metadata_json, ip, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    str(uuid.uuid4()),
                    user_id,
                    action,
                    target_type,
                    target_id,
                    metadata_json,
                    ip,
                    datetime.now(UTC).isoformat(),
                )
            except Exception as exc:
                logger.warning("PostgresAuthDB: falha ao escrever audit: %s", exc)
