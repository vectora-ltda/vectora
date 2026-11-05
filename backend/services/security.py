"""Módulo de segurança para execução de ferramentas com whitelisting."""

import re
import unicodedata
from pathlib import Path

#: `$IFS`/`${IFS}` é separador de campo shell válido, usado pra escapar
#: filtros ingênuos que procuram um espaço literal (`rm${IFS}-rf${IFS}/`
#: roda igual a `rm -rf /`). Expandir pra espaço antes de comparar fecha
#: essa ofuscação sem afetar o resto do comando (achado da comparação de
#: guardrails com o Hermes Agent, que já normaliza isso).
_IFS_PATTERN = re.compile(r"\$\{IFS\}|\$IFS(?![A-Za-z0-9_])")

#: Backslash antes de uma letra ou hífen comuns não tem efeito real além
#: de escapar o char seguinte (`r\m` roda como `rm`) — normaliza removendo.
_BACKSLASH_ESCAPE_PATTERN = re.compile(r"\\([A-Za-z-])")

#: `rm` com qualquer combinação de flags que contenha `r` e `f` (em
#: qualquer ordem/posição: `-rf`, `-fr`, `-rfv`, `-vrf`, `-fvr`, ...) —
#: a blacklist literal só cobria `rm -rf`/`rm -fr`.
_RM_FLAG_PATTERN = re.compile(r"\brm\s+-[a-z]+")


def _normalize_command(command: str) -> str:
    """Normaliza um comando shell antes da comparação com a blacklist.

    Colapsa espaços múltiplos/tabs, expande `$IFS`, remove
    backslash-escape trivial e normaliza unicode (NFKC) — fecha variações
    de ofuscação que uma comparação `in` ingênua deixaria passar.
    """
    normalized = unicodedata.normalize("NFKC", command)
    normalized = _IFS_PATTERN.sub(" ", normalized)
    normalized = _BACKSLASH_ESCAPE_PATTERN.sub(r"\1", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip().lower()


def _has_rm_recursive_force(cmd: str) -> bool:
    """`True` se algum `rm -X` no comando tem flags contendo `r` e `f`."""
    for match in _RM_FLAG_PATTERN.finditer(cmd):
        flags = match.group(0).split("-", 1)[-1]
        if "r" in flags and "f" in flags:
            return True
    return False


#: Diretórios que nunca devem ser lidos/escritos por uma tool, mesmo que
#: caiam dentro do workspace confiável (ex.: usuário versionou `.ssh/` por
#: engano) — mesmas categorias do mask de sandbox nativo
#: (`backend.sandbox.policy._DEFAULT_MASK`), mas checado independentemente
#: dele: essa é a segunda camada de defesa que continua ativa mesmo quando
#: o sandbox está desabilitado (ex. Windows sem WSL2, `DISABLED_POLICY`).
_SENSITIVE_DIR_NAMES = frozenset(
    {".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure"}
)
_SENSITIVE_FILE_SUFFIXES = (".pem",)
_SENSITIVE_FILENAMES = frozenset({".env"})


def is_sensitive_path(path: Path) -> bool:
    """``True`` se ``path`` é um arquivo/diretório de credencial sensível
    (chave SSH, credencial cloud, `.env`, `.pem`) — checagem fail-closed:
    erro ao resolver o path conta como sensível."""
    try:
        resolved = path.resolve()
    except OSError:
        return True
    if resolved.name in _SENSITIVE_FILENAMES:
        return True
    if resolved.suffix in _SENSITIVE_FILE_SUFFIXES:
        return True
    return any(part in _SENSITIVE_DIR_NAMES for part in resolved.parts)


def resolve_within_workspace(path: str, workspace_root: str | Path) -> Path | None:
    """Resolve ``path`` garantindo que ele fique dentro de ``workspace_root``.

    Caminhos relativos são resolvidos a partir da raiz do workspace; caminhos
    absolutos são aceitos apenas se estiverem dentro dela. Bloqueia ``..``,
    symlinks que apontam para fora e caminhos absolutos externos.

    Returns:
        Path absoluto resolvido quando dentro do workspace; ``None`` se escapar.
    """
    try:
        root = Path(workspace_root).resolve()
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
    except (ValueError, OSError):
        return None

    if resolved == root:
        return resolved
    if root in resolved.parents:
        return resolved
    return None


def is_safe_file_path(path: str, allowed_dirs: list[str] | None = None) -> bool:
    """Verifica se um caminho de arquivo é seguro para leitura/edição.

    Rejeita:
    - Caminhos absolutoscom /../
    - Arquivos fora de allowed_dirs (se especificado)
    - Extensões perigosas

    Args:
        path: Caminho do arquivo
        allowed_dirs: Diretórios permitidos (ex: ["./vectora", "./data"])

    Returns:
        True se caminho é seguro
    """
    try:
        file_path = Path(path).resolve()

        if ".." in path:
            return False

        dangerous_extensions = {".exe", ".sh", ".bat", ".cmd", ".com", ".pif"}
        if file_path.suffix.lower() in dangerous_extensions:
            return False

        if allowed_dirs:
            for allowed_dir in allowed_dirs:
                allowed_path = Path(allowed_dir).resolve()
                try:
                    file_path.relative_to(allowed_path)
                    return True
                except ValueError:
                    pass
            return False

        return True
    except (ValueError, OSError):
        return False


def is_safe_regex_pattern(pattern: str) -> bool:
    """Valida se um padrão regex é seguro (evita ReDoS).

    Args:
        pattern: Padrão regex

    Returns:
        True se padrão é válido
    """
    dangerous_patterns = [
        r"(.*)*",
        r"(.*)+",
        r"(.+)*",
        r"(.+)+",
        r"(a*)*",
        r"(a+)*",
        r"(a*)+",
        r"(a+)+",
        r"(a|a)*",
        r"(a|a)+",
    ]

    # Remove anchors for pattern matching to catch variations like (a+)+$ and (a|a)*$
    pattern_without_anchors = pattern.lstrip("^").rstrip("$")

    if any(dangerous in pattern_without_anchors for dangerous in dangerous_patterns):
        return False

    try:
        re.compile(pattern)
        return True
    except re.error:
        return False


def is_safe_shell_command(command: str) -> bool:
    """Valida se um comando shell é seguro usando modelo blacklist-only.

    Política: PERMITIR tudo, exceto comandos explicitamente destrutivos.

    BLACKLIST (sempre bloqueados):
    - Deleção recursiva / irrecuperável: rm -rf, rm -fr, rmdir /s
    - Formatação de disco: mkfs, format c:, dd if=/dev/zero
    - Escalada de privilégios: sudo su, runas
    - Ataque fork bomb: :(){:|:&};:
    - Wipe de dados: shred, wipe, secure-delete

    Todos os outros comandos — incluindo git add, git commit, git push,
    npm install, python scripts, curl, etc. — são permitidos sem restrição.

    Args:
        command: Comando shell a validar

    Returns:
        True se o comando NÃO está na blacklist (pode ser executado)
        False se o comando está na blacklist (bloqueado)
    """
    cmd = _normalize_command(command)

    if _has_rm_recursive_force(cmd):
        return False

    # Blacklist: padrões destrutivos e irrecuperáveis
    blacklist: list[str] = [
        # Deleção recursiva/forçada
        "rm -rf",
        "rm -fr",
        "rm --no-preserve-root",
        "rmdir /s",
        "rd /s",
        # Formatação de disco
        "mkfs",
        "format c:",
        "format d:",
        "format e:",
        "dd if=/dev/zero",
        "dd if=/dev/urandom",
        # Wipe de dados
        "shred ",
        "wipe ",
        "secure-delete",
        # Fork bomb
        ":(){:|:&};:",
        # Escalada de privilégios perigosa
        "sudo rm",
        "sudo mkfs",
        "sudo dd",
        "sudo shred",
    ]

    return not any(blocked in cmd for blocked in blacklist)
