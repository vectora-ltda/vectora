"""Registry de skills por usuário.

Cada usuário tem uma pasta ``~/.vectora/skills/<user_id>/`` com:

- ``index.json`` — lista das skills instaladas (metadados).
- ``<skill_id>/`` — uma subpasta por skill, contendo o ``SKILL.md`` extraído
  + corpo (recursos, scripts, etc.).

Fontes suportadas:

- **git URL** (``https://...`` ou ``git@...``): clone shallow via ``git`` CLI.
- **path local** (absoluto, na máquina do servidor): cópia recursiva.

Validação: a raiz da skill deve ter ``SKILL.md`` com frontmatter declarando
``name`` e ``description``. Sem isso, a instalação é rejeitada.

O resolver do agente consulta ``list_skill_paths(user_id)`` para montar o
``skills=[...]`` do Deep Agent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess  # nosec B404 — git clone controlado, sem shell=True
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import yaml
import yaml.resolver
from pydantic import BaseModel

from backend.services import extension_trust
from backend.vtypes.skill import Skill
from backend.workspace import skills_lock

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.+?)\s*$")
_PINNED_SOURCE_RE = re.compile(
    r"^(?P<url>[^#]+)#(?P<revision>[0-9a-f]{40}):(?P<path>.+)$"
)


def _strict_mapping(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    """Rejeita chaves YAML duplicadas antes de construir o mapa."""
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"chave duplicada no frontmatter: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


yaml.SafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_mapping
)

#: Versão por usuário — bumpada em add/remove para invalidar caches downstream
#: (resolver de skills do agent_factory).
_versions: dict[str, int] = {}
SkillScope = Literal["user", "workspace", "project", "runtime"]
_runtime_skills: dict[str, list[Skill]] = {}
_runtime_skill_dirs: dict[str, Path] = {}
_mutation_lock = RLock()


def skills_version(user_id: str) -> int:
    """Versão atual do conjunto de skills do usuário."""
    return _versions.get(user_id, 0)


def _bump_version(user_id: str) -> None:
    _versions[user_id] = _versions.get(user_id, 0) + 1


# ---------------------------------------------------------------------------
# Layout em disco
# ---------------------------------------------------------------------------


def _skills_dir(
    user_id: str, scope: SkillScope = "user", target: str | None = None
) -> Path:
    safe = user_id.replace("/", "_").replace("\\", "_") or "local"
    if scope == "user":
        return Path.home() / ".vectora" / "skills" / safe
    if scope == "workspace":
        if not target:
            raise ValueError("target obrigatório para escopo workspace")
        workspace = Path(target).expanduser().resolve()
        if not workspace.is_dir() or workspace.is_symlink():
            raise ValueError("workspace deve ser um diretório autorizado")
        return workspace / ".vectora" / "skills"
    if scope == "project":
        if not target:
            raise ValueError("target obrigatório para escopo project")
        raw_root = Path(target).expanduser()
        root = raw_root.resolve()
        if raw_root.is_symlink() or not raw_root.is_dir():
            raise ValueError("project deve ser um diretório real autorizado")
        vectora_dir = root / ".vectora"
        if vectora_dir.is_symlink():
            raise ValueError(".vectora não pode ser um symlink")
        return vectora_dir / "skills"
    if not target:
        raise ValueError("target obrigatório para escopo runtime")
    key = f"{user_id}:{target}"
    path = _runtime_skill_dirs.get(key)
    if path is None:
        path = Path(tempfile.mkdtemp(prefix="vectora-runtime-skills-"))
        _runtime_skill_dirs[key] = path
    return path


def _index_file(
    user_id: str, scope: SkillScope = "user", target: str | None = None
) -> Path:
    return _skills_dir(user_id, scope, target) / "index.json"


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower())
    return s.strip("-") or "skill"


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------


def _load_index(
    user_id: str, scope: SkillScope = "user", target: str | None = None
) -> list[Skill]:
    if scope == "runtime":
        if not target:
            raise ValueError("target obrigatório para escopo runtime")
        return list(_runtime_skills.get(f"{user_id}:{target}", []))
    path = _index_file(user_id, scope, target)
    if path.is_symlink():
        raise ValueError("índice de skills não pode ser um symlink")
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("skills: index corrompido para %s", user_id)
        return []
    out: list[Skill] = []
    for item in raw.get("skills", []):
        try:
            out.append(Skill(**item))
        except Exception:
            logger.debug("skills: entry inválida ignorada: %s", item)
    return out


def _save_index(
    user_id: str,
    skills: list[Skill],
    scope: SkillScope = "user",
    target: str | None = None,
) -> None:
    path = _index_file(user_id, scope, target)
    if path.exists() and path.is_symlink():
        raise ValueError("índice de skills não pode ser um symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"skills": [s.model_dump() for s in skills]}
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Frontmatter parsing (subset de YAML — chave: valor por linha)
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict[str, object]:
    """Lê o frontmatter YAML do SKILL.md.

    Implementa apenas o subset ``key: value`` por linha (suficiente para
    ``name``/``description``). Valores entre aspas são desempacotados.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    loaded = yaml.safe_load(match.group(1))
    if not isinstance(loaded, dict):
        raise ValueError("frontmatter YAML inválido")
    allowed = {"name", "description", "version", "category", "tags", "requires_skills"}
    if any(not isinstance(key, str) or key not in allowed for key in loaded):
        raise ValueError("campo desconhecido no frontmatter")
    for key in ("name", "description", "version"):
        if key not in loaded or not isinstance(loaded[key], str):
            raise ValueError(f"{key} deve ser uma string no frontmatter")
    requirements = loaded.get("requires_skills")
    if isinstance(requirements, str):
        raise ValueError("requires_skills deve ser um mapa ou lista YAML")
    return dict(loaded)


def _read_skill_metadata(skill_root: Path) -> tuple[str, str]:
    """Lê ``name`` e ``description`` do ``SKILL.md`` da pasta.

    Falha → ``ValueError`` com mensagem útil para o handler.
    """
    md = skill_root / "SKILL.md"
    if not md.is_file():
        raise ValueError("SKILL.md ausente na raiz da skill.")
    try:
        text = md.read_text(encoding="utf-8")
    except Exception as exc:
        raise ValueError(f"Falha ao ler SKILL.md: {exc}") from exc
    fm = _parse_frontmatter(text)
    name = str(fm.get("name", "")).strip()
    description = str(fm.get("description", "")).strip()
    if not name:
        raise ValueError("Frontmatter do SKILL.md não declara 'name'.")
    if not description:
        raise ValueError("Frontmatter do SKILL.md não declara 'description'.")
    return name, description


def _skill_lock_entry(skill: Skill) -> dict[str, object]:
    """Converte uma skill instalada em uma entrada sem conteúdo executável."""
    frontmatter = _parse_frontmatter(
        (Path(skill.path) / "SKILL.md").read_text(encoding="utf-8")
    )
    version = str(frontmatter.get("version", "")).strip()
    if not version:
        raise ValueError(f"frontmatter sem version para skill {skill.id}")
    requirements: dict[str, str] = {}
    raw_requirements = frontmatter.get("requires_skills", {})
    if raw_requirements is None:
        raw_requirements = {}
    if isinstance(raw_requirements, dict):
        for dependency, constraint in raw_requirements.items():
            if not isinstance(dependency, str) or not isinstance(constraint, str):
                raise ValueError(f"requires_skills inválido para skill {skill.id}")
            requirements[dependency.strip()] = constraint.strip()
    elif isinstance(raw_requirements, list):
        for item in raw_requirements:
            if not isinstance(item, dict) or set(item) - {
                "id",
                "version",
                "constraint",
            }:
                raise ValueError(f"requires_skills inválido para skill {skill.id}")
            dependency = item.get("id")
            constraint = item.get("version", item.get("constraint"))
            if not isinstance(dependency, str) or not isinstance(constraint, str):
                raise ValueError(f"requires_skills inválido para skill {skill.id}")
            if dependency in requirements:
                raise ValueError(f"dependência duplicada para skill {skill.id}")
            requirements[dependency.strip()] = constraint.strip()
    else:
        raise ValueError(f"requires_skills inválido para skill {skill.id}")
    source = skill.source
    parsed = urlsplit(source)
    if parsed.username or parsed.password:
        source = urlunsplit(
            (
                parsed.scheme,
                parsed.hostname or "",
                parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )
    return {
        "version": version,
        "source": source,
        "revision": skill.revision
        or skill.trust.digest
        or hashlib.sha256((Path(skill.path) / "SKILL.md").read_bytes()).hexdigest(),
        "integrity": skill.trust.digest
        or hashlib.sha256((Path(skill.path) / "SKILL.md").read_bytes()).hexdigest(),
        "requires_skills": requirements,
    }


def _write_scope_lock(
    user_id: str, scope: SkillScope, target: str | None, skills: list[Skill]
) -> None:
    """Valida e grava o lock da composição instalada do escopo."""
    # Escopos ligados a um workspace mantêm um único lock na raiz do
    # workspace; isso permite que o lock seja versionado junto do projeto e
    # evita criar um lock interno ao diretório de cada skill.
    lock_path = _scope_lock_path(user_id, scope, target)
    if lock_path.exists():
        skills_lock.read_lockfile(lock_path)
    entries = {skill.id: _skill_lock_entry(skill) for skill in skills}
    candidates: dict[str, object] = {
        skill_id: (str(entry["version"]), entry["requires_skills"])
        for skill_id, entry in entries.items()
    }
    skills_lock.resolve_dependencies(candidates)
    selected_versions = {
        skill_id: str(entry["version"]) for skill_id, entry in entries.items()
    }
    for entry in entries.values():
        requirements = entry["requires_skills"]
        if isinstance(requirements, dict):
            entry["requires_skills"] = {
                dependency: selected_versions.get(dependency, constraint)
                for dependency, constraint in requirements.items()
            }
    skills_lock.write_lockfile(lock_path, entries)


def _scope_lock_path(user_id: str, scope: SkillScope, target: str | None) -> Path:
    """Retorna o caminho canônico do lockfile do escopo."""
    skills_dir = _skills_dir(user_id, scope, target)
    return (
        skills_dir.parent / "skills.lock.json"
        if scope in ("workspace", "project")
        else skills_dir / "skills.lock.json"
    )


def _file_snapshot(path: Path) -> bytes | None:
    """Captura um arquivo para permitir rollback de uma publicação parcial."""
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def _restore_file(path: Path, snapshot: bytes | None) -> None:
    """Restaura ou remove um arquivo durante rollback transacional."""
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(snapshot)


def _validate_scope_lock(skills: list[Skill]) -> None:
    """Valida uma composição antes de persistir índice ou lockfile."""
    entries = {skill.id: _skill_lock_entry(skill) for skill in skills}
    candidates: dict[str, object] = {
        skill_id: (str(entry["version"]), entry["requires_skills"])
        for skill_id, entry in entries.items()
    }
    skills_lock.resolve_dependencies(candidates)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def list_skills(
    user_id: str, scope: SkillScope = "user", target: str | None = None
) -> list[Skill]:
    """Lista as skills instaladas para o usuário."""
    return _load_index(user_id, scope, target)


def list_skill_paths(
    user_id: str,
    scope: SkillScope = "user",
    target: str | None = None,
    *,
    workspace_id: str = "",
    project_root: str | None = None,
    runtime_id: str | None = None,
) -> list[Path]:
    """Paths agregados por precedência runtime > project > workspace > user."""
    scopes: list[tuple[SkillScope, str | None]] = [("user", None)]
    if workspace_id:
        scopes.append(("workspace", workspace_id))
    if project_root:
        scopes.append(("project", project_root))
    if runtime_id:
        scopes.append(("runtime", runtime_id))
    by_id: dict[str, Path] = {}
    for current_scope, current_target in scopes:
        for skill in _load_index(user_id, current_scope, current_target):
            path = Path(skill.path)
            digest = ""
            try:
                digest = hashlib.sha256((path / "SKILL.md").read_bytes()).hexdigest()
            except OSError:
                continue
            if (
                path.is_dir()
                and skill.trust.state != "invalid"
                and (not skill.trust.digest or skill.trust.digest == digest)
            ):
                by_id[skill.id] = path
    return list(by_id.values())


# ---------------------------------------------------------------------------
# Catálogo well-known — segunda fonte de discovery, local e sem rede
# ---------------------------------------------------------------------------


def _wellknown_dir() -> Path:
    override = os.getenv("VECTORA_SKILLS_WELLKNOWN_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".vectora" / "skills-wellknown"


def list_wellknown_catalog(directory: Path | None = None) -> list[dict]:
    """Catálogo local de skills — segunda fonte de discovery do agregador em
    ``backend/api/handlers/skills.py::get_skills_catalog``, independente do
    registry remoto (D1). Cada subpasta de ``directory`` (default
    ``~/.vectora/skills-wellknown/``, configurável via
    ``VECTORA_SKILLS_WELLKNOWN_DIR``) segue o mesmo layout de uma skill
    instalada — ``SKILL.md`` com frontmatter ``name``/``description``.

    Nunca levanta exceção: pasta ausente devolve lista vazia; subpasta sem
    ``SKILL.md`` válido é pulada e logada, sem derrubar as demais entradas.
    """
    root = directory if directory is not None else _wellknown_dir()
    if not root.is_dir():
        return []
    entries: list[dict] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            name, description = _read_skill_metadata(child)
            fm = _parse_frontmatter((child / "SKILL.md").read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("skills: entrada well-known ignorada (%s): %s", child, exc)
            continue
        tags_raw = str(fm.get("tags", ""))
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
        entries.append(
            {
                "id": _slugify(name),
                "name": name,
                "description": description,
                "source": str(child),
                "category": fm.get("category", "local"),
                "tags": tags,
            }
        )
    return entries


def _is_git_url(source: str) -> bool:
    return source.startswith(("http://", "https://", "git@", "git://", "ssh://"))


def _git_source_parts(source: str) -> tuple[str, str | None, str | None]:
    """Retorna URL, revisão e subdiretório de uma fonte Git opcionalmente fixada."""
    match = _PINNED_SOURCE_RE.fullmatch(source)
    if match is None:
        return source, None, None
    subpath = Path(match.group("path"))
    if subpath.is_absolute() or ".." in subpath.parts:
        raise ValueError("subdiretório da fonte Git inválido")
    return match.group("url"), match.group("revision"), subpath.as_posix()


class InstallSkillRequest(BaseModel):
    source: str
    """URL git ou path absoluto."""
    scope: SkillScope = "user"
    target: str | None = None
    workspace_id: str | None = None
    confirm_unverified: bool = False


def _install_skill_unlocked(
    user_id: str,
    source: str,
    scope: SkillScope = "user",
    target: str | None = None,
    *,
    confirm_unverified: bool = False,
) -> Skill:
    """Instala uma skill a partir de URL git ou path local.

    - URL git: ``git clone --depth 1`` em diretório temporário, depois move.
    - Path local: cópia recursiva.

    A pasta de destino é o slug do ``name`` do frontmatter — se já existir uma
    skill com o mesmo slug, a operação é rejeitada (use remove + install).
    """
    source = source.strip()
    if not source:
        raise ValueError("source vazio.")

    base = _skills_dir(user_id, scope, target)
    base.mkdir(parents=True, exist_ok=True)

    # Pasta de staging: clonamos / copiamos em <base>/.staging antes de
    # mover para o slug final (que só conhecemos após ler o SKILL.md).
    staging = base / ".staging"
    trust = extension_trust.unsigned_record(source)
    installed_revision: str | None = None
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    try:
        if _is_git_url(source):
            git_source, revision, subpath = _git_source_parts(source)
            # `shutil.which("git")` devolve path absoluto — boot do binário
            # Nuitka inicializa com PATH minimizado, sem isso o spawn falha.
            git_exe = shutil.which("git")
            if git_exe is None:
                raise ValueError(
                    "git não encontrado no PATH. Instale o git para usar "
                    "URLs como fonte de skills."
                )
            try:
                # `source` é prefix-validado por `_is_git_url`; `staging` vem
                # de Path interno (não-usuário). `--` impede source = "-X".
                subprocess.run(  # noqa: S603  # nosec B603
                    [
                        git_exe,
                        "clone",
                        "--depth",
                        "1",
                        *(["--no-single-branch"] if revision else []),
                        "--",
                        git_source,
                        str(staging),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.decode("utf-8", errors="replace")
                raise ValueError(f"git clone falhou: {stderr}") from exc
            except FileNotFoundError as exc:
                raise ValueError(
                    "git CLI não encontrado — instale git para usar URLs."
                ) from exc
            if revision:
                try:
                    subprocess.run(  # noqa: S603  # nosec B603 — git/revision validados
                        [git_exe, "-C", str(staging), "checkout", "--quiet", revision],
                        check=True,
                        capture_output=True,
                        timeout=60,
                    )
                except subprocess.CalledProcessError as exc:
                    stderr = exc.stderr.decode("utf-8", errors="replace")
                    raise ValueError(f"git checkout falhou: {stderr}") from exc
            try:
                revision_result = subprocess.run(  # noqa: S603  # nosec B603
                    [git_exe, "-C", str(staging), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                installed_revision = revision_result.stdout.decode("ascii").strip()
            except (subprocess.CalledProcessError, UnicodeDecodeError) as exc:
                raise ValueError("não foi possível determinar a revisão Git") from exc
            source_root = staging / subpath if subpath else staging
            if not source_root.is_dir() or source_root.is_symlink():
                raise ValueError("subdiretório da fonte Git não é válido")
            resolved_staging = staging.resolve()
            if not source_root.resolve().is_relative_to(resolved_staging):
                raise ValueError("subdiretório da fonte Git escapa do staging")
            for current_root, directories, files in os.walk(
                source_root, followlinks=False
            ):
                if any(
                    (Path(current_root) / name).is_symlink()
                    for name in (*directories, *files)
                ):
                    raise ValueError("a fonte Git não pode conter symlinks")
            if source_root != staging:
                extracted = staging.with_name(f"{staging.name}-extracted")
                shutil.copytree(source_root, extracted)
                shutil.rmtree(staging, ignore_errors=True)
                extracted.rename(staging)
            shutil.rmtree(staging / ".git", ignore_errors=True)
        else:
            src = Path(source).expanduser().resolve()
            if not src.is_dir():
                raise ValueError(f"Path local não é uma pasta: {src}")
            shutil.copytree(src, staging)

        name, description = _read_skill_metadata(staging)
        trust = extension_trust.content_record(
            source, (staging / "SKILL.md").read_bytes()
        )
        extension_trust.validate_record(trust, confirmed=confirm_unverified)
        skill_id = _slugify(name)
        target_dir = base / skill_id
        if target_dir.exists():
            raise ValueError(
                f"Skill '{skill_id}' já instalada — remova antes de reinstalar."
            )
        candidate = Skill(
            id=skill_id,
            name=name,
            description=description,
            source=source,
            path=str(staging),
            installed_at=datetime.now(UTC).isoformat(),
            installed_by=user_id,
            trust=trust,
            trust_confirmed=confirm_unverified,
            revision=installed_revision,
        )
        current = [s for s in _load_index(user_id, scope, target) if s.id != skill_id]
        _validate_scope_lock([*current, candidate])
        shutil.move(str(staging), str(target_dir))
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    skill = Skill(
        id=skill_id,
        name=name,
        description=description,
        source=source,
        path=str(target_dir),
        installed_at=datetime.now(UTC).isoformat(),
        installed_by=user_id,
        trust=trust,
        trust_confirmed=confirm_unverified,
        revision=installed_revision,
    )
    skills = [s for s in _load_index(user_id, scope, target) if s.id != skill_id]
    skills.append(skill)
    _validate_scope_lock(skills)
    index_path = _index_file(user_id, scope, target)
    lock_path = _scope_lock_path(user_id, scope, target)
    index_snapshot = _file_snapshot(index_path)
    lock_snapshot = _file_snapshot(lock_path)
    published = False
    try:
        if scope == "runtime":
            _runtime_skills[f"{user_id}:{target}"] = skills
        else:
            _save_index(user_id, skills, scope, target)
        _write_scope_lock(user_id, scope, target, skills)
        published = True
    finally:
        if not published:
            _restore_file(index_path, index_snapshot)
            _restore_file(lock_path, lock_snapshot)
            if scope == "runtime":
                key = f"{user_id}:{target}"
                if index_snapshot is None:
                    _runtime_skills.pop(key, None)
                else:
                    _runtime_skills[key] = _load_index(user_id, scope, target)
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
    _bump_version(user_id)
    return skill


def install_skill(
    user_id: str,
    source: str,
    scope: SkillScope = "user",
    target: str | None = None,
    *,
    confirm_unverified: bool = False,
) -> Skill:
    """Instala uma skill com exclusão mútua das mutações de skills."""
    with _mutation_lock:
        return _install_skill_unlocked(
            user_id,
            source,
            scope,
            target,
            confirm_unverified=confirm_unverified,
        )


def _install_skill_from_content_unlocked(
    user_id: str, name: str, description: str, content: str
) -> Skill:
    """Instala uma skill a partir de conteúdo gerado em memória pelo loop de
    aprendizado, sem passar por git/cópia de path.

    Monta o ``SKILL.md`` (frontmatter + ``content``) direto no destino.
    Mesma regra de slug/duplicidade de ``install_skill``: já existe com o
    mesmo slug → rejeitado (remova antes de reinstalar)."""
    name = name.strip()
    description = description.strip()
    if not name:
        raise ValueError("name vazio.")
    if not description:
        raise ValueError("description vazio.")

    base = _skills_dir(user_id)
    skill_id = _slugify(name)
    target = base / skill_id
    if target.exists():
        raise ValueError(
            f"Skill '{skill_id}' já instalada — remova antes de reinstalar."
        )

    staging = base / ".staging-learning"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    skill_md = (
        f'---\nname: "{name}"\ndescription: "{description}"\nversion: "1.0.0"\n'
        f"---\n\n{content}\n"
    )
    (staging / "SKILL.md").write_text(skill_md, encoding="utf-8")

    skill = Skill(
        id=skill_id,
        name=name,
        description=description,
        source="learning-loop",
        path=str(staging),
        installed_at=datetime.now(UTC).isoformat(),
        installed_by=user_id,
    )
    skills = [s for s in _load_index(user_id) if s.id != skill_id]
    _validate_scope_lock([*skills, skill])
    index_path = _index_file(user_id)
    lock_path = _scope_lock_path(user_id, "user", None)
    index_snapshot = _file_snapshot(index_path)
    lock_snapshot = _file_snapshot(lock_path)
    published = False
    staging.rename(target)
    try:
        skill = skill.model_copy(update={"path": str(target)})
        skills.append(skill)
        _save_index(user_id, skills)
        _write_scope_lock(user_id, "user", None, skills)
        published = True
    finally:
        if not published:
            _restore_file(index_path, index_snapshot)
            _restore_file(lock_path, lock_snapshot)
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
    _bump_version(user_id)
    return skill


def install_skill_from_content(
    user_id: str, name: str, description: str, content: str
) -> Skill:
    """Instala conteúdo gerado com exclusão mútua das mutações de skills."""
    with _mutation_lock:
        return _install_skill_from_content_unlocked(user_id, name, description, content)


def _remove_skill_unlocked(
    user_id: str,
    skill_id: str,
    scope: SkillScope = "user",
    target: str | None = None,
) -> bool:
    """Remove uma skill instalada. Retorna True se existia."""
    skills = _load_index(user_id, scope, target)
    entry = next((s for s in skills if s.id == skill_id), None)
    if entry is None:
        return False
    p = Path(entry.path)
    if p.is_symlink():
        return False
    remaining = [s for s in skills if s.id != skill_id]
    _validate_scope_lock(remaining)
    index_path = _index_file(user_id, scope, target)
    lock_path = _scope_lock_path(user_id, scope, target)
    index_snapshot = _file_snapshot(index_path)
    lock_snapshot = _file_snapshot(lock_path)
    backup = p.with_name(f".{p.name}.rollback") if p.is_dir() else None
    if backup is not None and backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    published = False
    try:
        if backup is not None:
            p.rename(backup)
        if scope == "runtime":
            _runtime_skills[f"{user_id}:{target}"] = remaining
        else:
            _save_index(user_id, remaining, scope, target)
        _write_scope_lock(user_id, scope, target, remaining)
        published = True
    finally:
        if not published:
            _restore_file(index_path, index_snapshot)
            _restore_file(lock_path, lock_snapshot)
            if scope == "runtime":
                _runtime_skills[f"{user_id}:{target}"] = skills
            if backup is not None and backup.exists() and not p.exists():
                backup.rename(p)
        elif backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    _bump_version(user_id)
    return True


def remove_skill(
    user_id: str,
    skill_id: str,
    scope: SkillScope = "user",
    target: str | None = None,
) -> bool:
    """Remove uma skill com exclusão mútua das mutações de skills."""
    with _mutation_lock:
        return _remove_skill_unlocked(user_id, skill_id, scope, target)


def verify_skill(
    user_id: str,
    skill_id: str,
    scope: SkillScope = "user",
    target: str | None = None,
) -> dict:
    """Revalida o ``SKILL.md`` — útil quando o usuário editou a skill no disco."""
    entry = next(
        (s for s in _load_index(user_id, scope, target) if s.id == skill_id), None
    )
    if entry is None:
        return {"ok": False, "error": "skill não encontrada"}
    root = Path(entry.path)
    if not root.is_dir():
        return {"ok": False, "error": "pasta da skill ausente"}
    try:
        name, description = _read_skill_metadata(root)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    digest = hashlib.sha256((root / "SKILL.md").read_bytes()).hexdigest()
    if entry.trust.digest and digest != entry.trust.digest:
        return {"ok": False, "code": "invalid", "error": "digest da skill mudou"}
    # Atualiza name/description se mudaram.
    if name != entry.name or description != entry.description:
        skills = _load_index(user_id, scope, target)
        for i, s in enumerate(skills):
            if s.id == skill_id:
                skills[i] = entry.model_copy(
                    update={"name": name, "description": description}
                )
                break
        if scope == "runtime":
            _runtime_skills[f"{user_id}:{target}"] = skills
        else:
            _save_index(user_id, skills, scope, target)
        _bump_version(user_id)
    return {"ok": True, "name": name, "description": description}
