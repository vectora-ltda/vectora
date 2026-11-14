"""Repositórios git dentro de um workspace e a escolha gravada no ``vectora.toml``.

Um workspace pode ser uma pasta com vários repositórios, ou um repositório com
submódulos. O Git workbench escolhe qual deles mostrar e operar; a escolha fica na
seção ``[git]`` do ``vectora.toml`` do workspace (``repo = "caminho/relativo"``),
que é seguro commitar, e vale para todas as rotas git.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from backend.workspace.workspace_config import WORKSPACE_CONFIG_FILENAME

RepoKind = Literal["root", "repo", "submodule", "worktree"]

#: Quantos níveis abaixo da raiz a descoberta percorre.
MAX_DEPTH: Final = 4
#: Teto de repositórios devolvidos e de pastas visitadas, para uma raiz enorme não travar.
MAX_REPOS: Final = 200
MAX_DIRECTORIES: Final = 20_000
#: Pastas que não contêm projetos do usuário e custam caro para percorrer.
_SKIP_DIRECTORIES: Final = frozenset(
    {
        "node_modules",
        "__pycache__",
        "venv",
        "env",
        "dist",
        "build",
        "target",
        "site-packages",
        "bower_components",
        "coverage",
    }
)


@dataclass(frozen=True)
class GitRepoEntry:
    """Um repositório do workspace. ``path`` é relativo à raiz; vazio é o padrão."""

    path: str
    name: str
    kind: RepoKind


def repo_kind(directory: Path) -> RepoKind | None:
    """Tipo do repositório em ``directory`` (``None`` se não há ``.git`` nele).

    ``.git`` pasta é um repositório; ``.git`` arquivo aponta o ``gitdir`` de um
    submódulo (``…/modules/…``) ou de um worktree (``…/worktrees/…``).
    """
    git = directory / ".git"
    try:
        if git.is_dir():
            return "repo"
        if not git.is_file():
            return None
        first_line = git.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    target = first_line.removeprefix("gitdir:").strip().replace("\\", "/")
    if "/modules/" in target:
        return "submodule"
    if "/worktrees/" in target:
        return "worktree"
    return "repo"


def enclosing_repo(root: Path) -> Path | None:
    """Repositório que contém ``root`` (workspace dentro de um repositório), se houver."""
    for parent in root.resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def discover_repos(root: Path) -> list[GitRepoEntry]:
    """Repositórios do workspace: a raiz (ou o que a contém) e os de dentro dela.

    O primeiro item, com ``path`` vazio, é o padrão do workspace: a raiz se ela é um
    repositório, ou o repositório que a contém. Os demais vêm de pastas com ``.git``
    até ``MAX_DEPTH`` níveis, sem seguir links simbólicos, sem entrar em pastas
    ocultas nem em ``node_modules`` e semelhantes, e continuam dentro de repositórios
    para achar submódulos e repositórios aninhados.
    """
    base = root.resolve()
    entries: list[GitRepoEntry] = []
    if repo_kind(base):
        entries.append(GitRepoEntry("", base.name, "root"))
    else:
        parent = enclosing_repo(base)
        if parent is not None:
            entries.append(GitRepoEntry("", parent.name, "root"))

    found: list[GitRepoEntry] = []
    visited = 0
    for current, directories, _files in os.walk(base, followlinks=False):
        visited += 1
        if visited > MAX_DIRECTORIES or len(found) >= MAX_REPOS:
            break
        current_path = Path(current)
        relative = current_path.relative_to(base)
        depth = len(relative.parts)
        directories[:] = sorted(
            name
            for name in directories
            if not name.startswith(".")
            and name not in _SKIP_DIRECTORIES
            and depth < MAX_DEPTH
        )
        if depth == 0:
            continue
        kind = repo_kind(current_path)
        if kind:
            found.append(GitRepoEntry(relative.as_posix(), current_path.name, kind))
    return entries + sorted(found, key=lambda entry: entry.path)


def normalize_repo_path(value: object) -> str | None:
    """Caminho relativo e seguro, em ``/``; ``""`` é o padrão; ``None`` se inválido.

    Recusa absoluto, unidade do Windows, ``..`` e caracteres nulos: a escolha nunca
    pode apontar para fora do workspace.
    """
    if not isinstance(value, str) or "\0" in value:
        return None
    text = value.strip().replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return None
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if ".." in parts:
        return None
    return "/".join(parts)


def resolve_repo_dir(root: Path, repo: str) -> Path | None:
    """Pasta do repositório escolhido, ou ``None`` se é inválida ou deixou de ser um.

    Resolve links simbólicos antes de conferir que a pasta está dentro do workspace.
    """
    normalized = normalize_repo_path(repo)
    if not normalized:
        return None
    base = root.resolve()
    candidate = (base / normalized).resolve()
    if not candidate.is_relative_to(base) or candidate == base:
        return None
    return candidate if repo_kind(candidate) else None


# ── vectora.toml ────────────────────────────────────────────────────────────


def read_git_repo_setting(root: Path) -> str:
    """``[git] repo`` do ``vectora.toml``; vazio se não há arquivo, seção ou valor válido."""
    path = root / WORKSPACE_CONFIG_FILENAME
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    section = data.get("git")
    value = section.get("repo") if isinstance(section, dict) else None
    return normalize_repo_path(value) or ""


_TABLE_HEADER: Final = re.compile(r"^\s*\[\[?\s*([^\[\]]+?)\s*\]\]?\s*(#.*)?$")
_REPO_LINE: Final = re.compile(
    r"""^(?P<key>\s*repo\s*=\s*)(?P<value>"(?:[^"\\]|\\.)*"|'[^']*')(?P<tail>\s*#.*)?\s*$"""
)
_ANY_REPO_LINE: Final = re.compile(r"^\s*repo\s*=")


def _toml_string(value: str) -> str:
    # Uma string básica do TOML usa os mesmos escapes do JSON (\" \\ \n \uXXXX).
    return json.dumps(value, ensure_ascii=False)


def set_git_repo_in_toml(text: str, repo: str) -> str:
    """Devolve ``text`` com ``[git] repo`` definido (ou removido, se ``repo`` é vazio).

    Edita só a linha do ``repo``: o resto do arquivo, os comentários e a ordem das
    seções ficam como estão. Cria a seção ``[git]`` quando falta.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        header = _TABLE_HEADER.match(line.rstrip("\r\n"))
        if header is None:
            continue
        if start is None:
            if header.group(1) == "git" and not line.lstrip().startswith("[["):
                start = index
        else:
            end = index
            break

    if start is None:
        if not repo:
            return text
        body = text if not text or text.endswith(("\n", "\r\n")) else text + newline
        gap = newline if body.strip() else ""
        return f"{body}{gap}[git]{newline}repo = {_toml_string(repo)}{newline}"

    for index in range(start + 1, end):
        line = lines[index]
        if not _ANY_REPO_LINE.match(line):
            continue
        if not repo:
            del lines[index]
            break
        match = _REPO_LINE.match(line.rstrip("\r\n"))
        tail = match.group("tail") or "" if match else ""
        key = match.group("key") if match else "repo = "
        lines[index] = f"{key}{_toml_string(repo)}{tail}{newline}"
        break
    else:
        if repo:
            lines.insert(start + 1, f"repo = {_toml_string(repo)}{newline}")
    return "".join(lines)


def write_git_repo_setting(root: Path, repo: str) -> None:
    """Grava ``[git] repo`` no ``vectora.toml`` do workspace, de forma atômica."""
    path = root / WORKSPACE_CONFIG_FILENAME
    try:
        current = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    updated = set_git_repo_in_toml(current, repo)
    if updated == current and path.exists():
        return
    # Grava num arquivo temporário da mesma pasta e troca: uma falha no meio
    # não deixa o vectora.toml pela metade.
    handle, temporary = tempfile.mkstemp(dir=root, prefix=".vectora-toml-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(updated)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
