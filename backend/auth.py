"""Subcomando `vectora auth` — gerenciamento de autenticação no CLI.

Comandos:
    vectora auth signup     — interativo: email + senha → cria conta no servidor
    vectora auth login      — interativo: email + senha → guarda tokens
    vectora auth logout     — invalida refresh token + limpa storage local
    vectora auth whoami     — mostra usuário ativo + role + servidor configurado
    vectora auth refresh    — força rotação de tokens (debug)

Storage local (em ordem de preferência):
    1. OS keyring (Windows Credential Manager / macOS Keychain / GNOME Keyring)
    2. ~/.vectora/auth.json com permissão 0600

O CLI em modo local (sem ter feito login) opera sempre como root local —
não requer autenticação para acessar o filesystem do servidor.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

from backend.settings import settings

logger = logging.getLogger(__name__)

_AUTH_FILE = settings.vectora_home / "auth.json"
_KEYRING_SERVICE = "vectora-cli"
_KEYRING_USER = "session"


# ---------------------------------------------------------------------------
# Helpers de storage
# ---------------------------------------------------------------------------


def _save_session(session: dict) -> None:
    """Persiste a sessão autenticada (tokens + info do usuário)."""
    try:
        import keyring

        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, json.dumps(session))
        return
    except Exception:
        pass

    # Fallback: arquivo JSON com permissão restrita
    _AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    _AUTH_FILE.write_text(json.dumps(session, indent=2))
    with contextlib.suppress(AttributeError, NotImplementedError):
        _AUTH_FILE.chmod(0o600)


def _load_session() -> dict | None:
    """Carrega a sessão autenticada, ou None se não houver."""
    try:
        import keyring

        raw = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
        if raw:
            return json.loads(raw)
    except Exception:
        pass

    if _AUTH_FILE.exists():
        try:
            return json.loads(_AUTH_FILE.read_text())
        except Exception:
            pass

    return None


def _clear_session() -> None:
    """Remove a sessão autenticada do storage local."""
    try:
        import keyring

        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USER)
    except Exception:
        pass

    if _AUTH_FILE.exists():
        _AUTH_FILE.unlink(missing_ok=True)


def get_active_session() -> dict | None:
    """Retorna a sessão ativa, ou None se não estiver logado."""
    return _load_session()


def get_bearer_token() -> str | None:
    """Retorna o access_token da sessão ativa, se disponível.

    Usado por comandos CLI que precisam autenticar requests ao servidor.
    """
    session = _load_session()
    return session.get("access_token") if session else None


# ---------------------------------------------------------------------------
# Comandos interativos
# ---------------------------------------------------------------------------


def _get_server_url() -> str:
    """Lê a URL do servidor do storage ou usa o padrão local."""
    session = _load_session()
    if session and session.get("server_url"):
        return session["server_url"]
    # Padrão local
    return "http://localhost:8080"


def _prompt_server_url() -> str:
    """Pede a URL do servidor ao usuário."""
    default = "http://localhost:8080"
    raw = input(f"URL do servidor Vectora [{default}]: ").strip()
    return raw or default


def cmd_signup(args: Any) -> int:
    """Cria uma nova conta no servidor Vectora configurado."""
    import getpass

    import requests

    server = _prompt_server_url()

    email = input("E-mail: ").strip()
    if not email:
        print("❌ E-mail não pode ser vazio.")
        return 1

    password = getpass.getpass("Senha (min 8 chars): ")
    confirm = getpass.getpass("Confirme a senha: ")
    if password != confirm:
        print("❌ Senhas não conferem.")
        return 1

    try:
        resp = requests.post(
            f"{server}/auth/signup",
            json={"email": email, "password": password},
            timeout=15,
        )
    except Exception as exc:
        print(f"❌ Erro de conexão: {exc}")
        return 1

    if resp.status_code not in (200, 201):
        detail = resp.json().get("detail", resp.text)
        print(f"❌ Erro: {detail}")
        return 1

    data = resp.json()
    _save_session(
        {
            "server_url": server,
            "user_id": data["user"]["id"],
            "email": data["user"]["email"],
            "role": data["user"]["role"],
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
        }
    )
    print(
        f"✅ Conta criada com sucesso! Logado como {email} (role: {data['user']['role']})"
    )
    return 0


def cmd_login(args: Any) -> int:
    """Autentica com email e senha no servidor Vectora."""
    import getpass

    import requests

    server = _prompt_server_url()
    email = input("E-mail: ").strip()
    password = getpass.getpass("Senha: ")

    try:
        resp = requests.post(
            f"{server}/auth/signin",
            json={"email": email, "password": password},
            timeout=15,
        )
    except Exception as exc:
        print(f"❌ Erro de conexão: {exc}")
        return 1

    if resp.status_code != 200:
        detail = resp.json().get("detail", resp.text)
        print(f"❌ Autenticação falhou: {detail}")
        return 1

    data = resp.json()
    _save_session(
        {
            "server_url": server,
            "user_id": data["user"]["id"],
            "email": data["user"]["email"],
            "role": data["user"]["role"],
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
        }
    )
    print(f"✅ Login realizado! {email} (role: {data['user']['role']})")
    return 0


def cmd_logout(args: Any) -> int:
    """Invalida o refresh token no servidor e limpa a sessão local."""
    import requests

    session = _load_session()
    if not session:
        print("ℹ️  Nenhuma sessão ativa. Você já está deslogado.")
        return 0

    server = session.get("server_url", "http://localhost:8080")
    refresh_token = session.get("refresh_token", "")

    if refresh_token:
        with contextlib.suppress(Exception):
            requests.post(
                f"{server}/auth/signout",
                json={"refresh_token": refresh_token},
                timeout=10,
            )

    _clear_session()
    print("✅ Logout realizado.")
    return 0


def cmd_whoami(args: Any) -> int:
    """Mostra o usuário autenticado atual."""
    session = _load_session()
    if not session:
        print("👤 Não autenticado — operando como root local (acesso via filesystem).")
        return 0

    print(f"👤 {session.get('email')}  (role: {session.get('role')})")
    print(f"   Servidor: {session.get('server_url')}")
    return 0


def cmd_refresh(args: Any) -> int:
    """Força rotação de tokens (útil para debug)."""
    import requests

    session = _load_session()
    if not session:
        print("❌ Nenhuma sessão ativa. Faça login primeiro.")
        return 1

    server = session.get("server_url", "http://localhost:8080")
    refresh_token = session.get("refresh_token", "")

    try:
        resp = requests.post(
            f"{server}/auth/refresh",
            json={"refresh_token": refresh_token},
            timeout=15,
        )
    except Exception as exc:
        print(f"❌ Erro de conexão: {exc}")
        return 1

    if resp.status_code != 200:
        print(f"❌ Erro: {resp.json().get('detail', resp.text)}")
        return 1

    data = resp.json()
    session.update(
        {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
        }
    )
    _save_session(session)
    print("✅ Tokens rotacionados com sucesso.")
    return 0
