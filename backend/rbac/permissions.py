"""Serviço de permissões e RBAC do Vectora.

Define a hierarquia de roles e as regras de acesso por recurso/ação.
Todas as verificações são fail-closed (negam por padrão).

Hierarquia de roles (maior índice = mais permissões):
    viewer  (0)  — leitura em workspaces compartilhados
    member  (1)  — threads/workspace próprios, RAG, terminal restrito
    admin   (2)  — threads de terceiros, audit log, workspaces compartilhados
    root    (3)  — tudo (igual admin + mudar roles, deletar root)

FilesystemPermission
--------------------
Regras declarativas first-match-wins para acesso ao filesystem pelas tools
nativas. Substitui ``resolve_within_workspace()`` em ferramentas já
migradas. Ferramentas artesanais legadas ainda usam
``src/services/security.py::resolve_within_workspace``.

Cada ``FsRule`` tem:
    pattern   — glob simples (`fnmatch`-compatible) ou prefixo ``path:``
    action    — ``"allow"`` | ``"deny"`` | ``"interrupt"``
    ops       — conjunto de operações afetadas: ``{"read","write","delete","list"}``
                Se vazio, aplica a todas as ops.

Avaliação: percorre ``FilesystemPermission.rules`` em ordem; retorna
``FsDecision`` da primeira regra que bate. Sem match → ``"deny"`` (fail-closed).
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_ROLE_LEVEL: dict[str, int] = {
    "viewer": 0,
    "member": 1,
    "admin": 2,
    "root": 3,
}


def role_level(role: str) -> int:
    """Retorna o nível numérico do role (maior = mais permissões)."""
    return _ROLE_LEVEL.get(role, -1)


def has_min_role(user: Any, min_role: str) -> bool:
    """True se o usuário tem pelo menos o role mínimo exigido."""
    if user is None:
        return False
    return role_level(getattr(user, "role", "")) >= role_level(min_role)


def require_min_role(user: Any, min_role: str) -> None:
    """Lança HTTPException 403 se o usuário não tiver o role mínimo."""
    if not has_min_role(user, min_role):
        raise HTTPException(
            status_code=403,
            detail=f"Requer role '{min_role}' ou superior.",
        )


def can_access_thread(user: Any, thread_owner_id: str | None) -> bool:
    """True se o usuário pode ler/escrever na thread.

    - root e admin: qualquer thread
    - member/viewer: apenas threads próprias
    """
    if user is None:
        return False
    if has_min_role(user, "admin"):
        return True
    return thread_owner_id is None or thread_owner_id == user.id


def can_delete_thread(user: Any, thread_owner_id: str | None) -> bool:
    """True se o usuário pode deletar a thread."""
    if user is None:
        return False
    if has_min_role(user, "admin"):
        return True
    return thread_owner_id == user.id


def can_run_terminal(user: Any, workspace_owner_id: str | None) -> bool:
    """True se o usuário pode executar comandos de terminal no workspace.

    - root/admin: qualquer workspace
    - member: apenas workspace próprio
    - viewer: nunca
    """
    if user is None:
        return False
    if has_min_role(user, "admin"):
        return True
    if user.role == "member":
        return workspace_owner_id is None or workspace_owner_id == user.id
    return False


def can_read_audit(user: Any) -> bool:
    """True se o usuário pode ver o audit log completo."""
    return has_min_role(user, "admin")


def can_manage_users(user: Any) -> bool:
    """True se o usuário pode criar/deletar/modificar outros usuários."""
    return has_min_role(user, "root")


# ---------------------------------------------------------------------------
# FilesystemPermission — regras declarativas first-match-wins
# ---------------------------------------------------------------------------

FsDecision = Literal["allow", "deny", "interrupt"]
FsOp = Literal["read", "write", "delete", "list"]


@dataclass
class FsRule:
    """Regra individual de permissão de filesystem.

    Args:
        pattern: Glob (`fnmatch`) ou prefixo de path. Exemplos:
            - ``"**/.env"`` — qualquer .env em qualquer subdir
            - ``"/etc/*"`` — qualquer arquivo sob /etc/
            - ``"**/id_rsa"`` — chave privada SSH
        action: Decisão quando a regra bate.
        ops: Subconjunto de operações. Vazio = todas as operações.
        description: Nota humana (opcional, para logging/debugging).
    """

    pattern: str
    action: FsDecision
    ops: frozenset[FsOp] = field(default_factory=frozenset)
    description: str = ""

    def matches(self, path: str, op: FsOp) -> bool:
        """True se esta regra cobre o path + operação dados.

        Lógica de matching (em ordem, primeira que bate vence):
        1. ``fnmatch`` contra o path completo normalizado.
        2. ``fnmatch`` contra o basename do path (ex: ``.env`` bate ``**/.env``).
        3. ``fnmatch`` do basename contra o basename do padrão (ex: ``.env``
           bate ``**/.env`` extraindo ``.env`` do padrão).

        O ``fnmatch`` não trata ``**`` como globstar real — para padrões
        como ``"**/.env"``, o passo 3 extrai ``".env"`` do padrão e verifica
        contra o basename do path. Isso cobre tanto ``".env"`` nu quanto
        ``"dir/sub/.env"``.
        """
        if self.ops and op not in self.ops:
            return False
        # Normaliza para forward-slashes para compatibilidade cross-platform
        norm = path.replace("\\", "/")
        # Passo 1: match completo
        if fnmatch.fnmatch(norm, self.pattern):
            return True
        # Passo 2: basename do path contra o padrão completo
        basename = norm.rsplit("/", 1)[-1]
        if fnmatch.fnmatch(basename, self.pattern):
            return True
        # Passo 3: basename do path contra o basename do padrão.
        # Trata padrões como ``**/.env`` → ``.env``, ``**/id_rsa`` → ``id_rsa``.
        # Exclui basenames puramente wildcard (``*``, ``**``, ``*.*``) para
        # evitar que regras como ``/etc/*`` batam em todo arquivo.
        pattern_basename = self.pattern.rsplit("/", 1)[-1]
        _pure_wildcard = {"*", "**", "*.*"}
        if (
            pattern_basename != self.pattern  # padrão tem componente de dir
            and pattern_basename not in _pure_wildcard
            and pattern_basename  # não-vazio
        ):
            return fnmatch.fnmatch(basename, pattern_basename)
        return False


@dataclass
class FilesystemPermission:
    """Conjunto ordenado de regras de permissão de filesystem.

    Regras avaliadas em ordem; primeira que bater é usada (first-match-wins).
    Se nenhuma regra bater, retorna ``"deny"`` (fail-closed).

    Uso no harness (futuro):
        ``create_deep_agent(..., filesystem_permission=build_fs_permission())``

    Uso em tools artesanais (atual):
        ``FS_PERMISSION.check(path, "write")``
    """

    rules: list[FsRule] = field(default_factory=list)

    def check(self, path: str, op: FsOp) -> FsDecision:
        """Avalia o path+op contra as regras e retorna a decisão.

        Returns:
            ``"allow"`` — prosseguir sem interrupção.
            ``"deny"``  — bloquear operação (lança ou retorna erro).
            ``"interrupt"`` — pausar e perguntar ao usuário (HITL).
        """
        for rule in self.rules:
            if rule.matches(path, op):
                logger.debug(
                    "fs_permission: path=%r op=%s → %s (rule: %r)",
                    path,
                    op,
                    rule.action,
                    rule.description or rule.pattern,
                )
                return rule.action
        # Fail-closed: sem regra matching = negar
        logger.debug("fs_permission: path=%r op=%s → deny (sem match)", path, op)
        return "deny"

    def is_allowed(self, path: str, op: FsOp) -> bool:
        """Atalho: True se ``check()`` retornar ``"allow"``."""
        return self.check(path, op) == "allow"

    def requires_interrupt(self, path: str, op: FsOp) -> bool:
        """True se a operação precisa de aprovação do usuário."""
        return self.check(path, op) == "interrupt"


# ---------------------------------------------------------------------------
# Regras canônicas do Vectora
# ---------------------------------------------------------------------------

#: Padrões de paths sensíveis do sistema — sempre DENY (qualquer op).
_SENSITIVE_DENY: list[FsRule] = [
    FsRule("**/.env", "deny", description="variáveis de ambiente com segredos"),
    FsRule("**/.env.*", "deny", description="variáveis de ambiente com segredos"),
    FsRule("**/id_rsa", "deny", description="chave privada SSH RSA"),
    FsRule("**/id_ed25519", "deny", description="chave privada SSH Ed25519"),
    FsRule("**/id_ecdsa", "deny", description="chave privada SSH ECDSA"),
    FsRule("**/*.pem", "deny", description="certificado/chave PEM"),
    FsRule("**/*.key", "deny", description="arquivo de chave privada"),
    FsRule("**/*.p12", "deny", description="keystore PKCS#12"),
    FsRule("**/*.pfx", "deny", description="keystore PKCS#12 Windows"),
    FsRule("/etc/*", "deny", description="configurações do sistema"),
    FsRule("/proc/*", "deny", description="pseudo-filesystem do kernel"),
    FsRule("/sys/*", "deny", description="pseudo-filesystem do kernel"),
    FsRule("**/master.kek", "deny", description="chave mestra do Vectora"),
    FsRule("**/jwt_keys/*", "deny", description="chaves JWT do Vectora"),
]

#: Skills — leitura OK, escrita/deleção DENY (skills são somente-leitura pelo agente).
_SKILLS_RULES: list[FsRule] = [
    FsRule(
        "/memories/skills/**",
        "deny",
        ops=frozenset({"write", "delete"}),
        description="skills do usuário — somente-leitura pelo agente",
    ),
    FsRule(
        "/memories/skills/**",
        "allow",
        ops=frozenset({"read", "list"}),
        description="skills do usuário — leitura permitida",
    ),
]

#: Memories — leitura e escrita OK (o agente gerencia memórias).
_MEMORIES_RULES: list[FsRule] = [
    FsRule(
        "/memories/**",
        "allow",
        description="memórias do usuário — leitura e escrita permitidas",
    ),
]

#: Workspace — paths não-sensíveis ALLOW; escrita fora do workspace → INTERRUPT.
_WORKSPACE_RULES: list[FsRule] = [
    FsRule(
        "/workspace/**",
        "allow",
        description="workspace ativo — acesso completo",
    ),
]

#: Paths fora do workspace ativo — escrita pede confirmação.
_EXTERNAL_WRITE_RULES: list[FsRule] = [
    FsRule(
        "**",
        "interrupt",
        ops=frozenset({"write", "delete"}),
        description="escrita fora do workspace — requer aprovação HITL",
    ),
    FsRule(
        "**",
        "allow",
        ops=frozenset({"read", "list"}),
        description="leitura fora do workspace — permitida",
    ),
]


def build_fs_permission() -> FilesystemPermission:
    """Constrói o ``FilesystemPermission`` canônico do Vectora.

    Ordem das regras (first-match-wins):
    1. DENY paths sensíveis (segredos, chaves, sistema)
    2. DENY escrita em skills
    3. ALLOW leitura em skills
    4. ALLOW tudo em memories
    5. ALLOW tudo em workspace
    6. INTERRUPT escrita fora do workspace
    7. ALLOW leitura fora do workspace

    Returns:
        ``FilesystemPermission`` pronto para passar ao harness ou às tools.
    """
    rules = (
        _SENSITIVE_DENY
        + _SKILLS_RULES
        + _MEMORIES_RULES
        + _WORKSPACE_RULES
        + _EXTERNAL_WRITE_RULES
    )
    return FilesystemPermission(rules=rules)


#: Instância singleton — use em tools artesanais via ``FS_PERMISSION.check(path, op)``.
FS_PERMISSION: FilesystemPermission = build_fs_permission()
