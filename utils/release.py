"""Automação de linhas de release: plano de versão, PRs de release e milestones.

Uso: `uv run python utils/release.py <comando> [args...]`. A configuração
das linhas e dos arquivos de versão fica em `.github/release-lines.json`.

Comandos:
    line                 Seleciona uma linha de release ativa para os workflows do GitHub Actions
    plan                 Build and apply a canonical release plan without generated Release Please refs
    prepare-line         Atualiza as linhas de release no próprio branch gerado pelo Release Please
    rotate               Calcula e aplica a próxima rotação das linhas de release
    select-pr            Seleciona uma PR de release sem misturar a minor e os patches em master
    pr-body              Prepara o corpo de uma pull request com apenas a release atual
    normalize-changelog  Normalize unordered list markers in the generated changelog
    validate-milestone   Valida a linha de release declarada pela milestone de uma pull request
    update-channel       Select the update channel for a Vectora package version
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import ClassVar, Literal, TypedDict

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

# ── line: Seleciona a linha de release ativa de uma branch ─────────────────


class ReleaseLine(BaseModel):
    """Metadados de branch e milestone de uma linha de release ativa."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    branch: str
    milestone: str


class ReleaseLines(BaseModel):
    """Linhas de desenvolvimento e manutenção e os arquivos que carregam a versão.

    O primeiro item de ``version_files`` é a fonte da versão atual; todos são
    reescritos a cada release.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    version_files: tuple[str, ...] = Field(min_length=1)
    development: ReleaseLine
    maintenance: ReleaseLine

    @field_validator("version_files")
    @classmethod
    def _relative_unique_paths(cls, files: tuple[str, ...]) -> tuple[str, ...]:
        for file in files:
            path = PurePosixPath(file)
            if (
                not file
                or path.is_absolute()
                or ".." in path.parts
                or file.startswith("\\")
            ):
                raise ValueError(f"version file must be a relative path: {file!r}")
        if len(set(files)) != len(files):
            raise ValueError("version files are duplicated")
        return files


CONFIG_PATH: Path = Path(__file__).parents[1] / ".github" / "release-lines.json"


def load_release_lines(path: Path = CONFIG_PATH) -> ReleaseLines:
    """Carrega e valida a configuração versionada das linhas de release."""
    try:
        config = ReleaseLines.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid release-line configuration: {exc}") from exc
    if config.development.branch == config.maintenance.branch:
        raise ValueError("development and maintenance branches must differ")
    return config


def selection_for_branch(branch: str | None, config: ReleaseLines) -> dict[str, str]:
    """Retorna as saídas do workflow para uma branch, incluindo o indicador de habilitação."""
    selected = next(
        (
            line
            for line in (config.development, config.maintenance)
            if line.branch == branch
        ),
        None,
    )
    return {
        "enabled": "true" if selected else "false",
        "maintenance_branch": config.maintenance.branch,
        "development_branch": config.development.branch,
        "development_milestone": config.development.milestone,
        "version_files": " ".join(config.version_files) if selected else "",
    }


def line_main() -> int:
    """Imprime as saídas do GitHub Actions para a branch informada."""
    if len(sys.argv) != 2:
        print("usage: release.py line BRANCH", file=sys.stderr)
        return 2
    try:
        outputs = selection_for_branch(sys.argv[1], load_release_lines())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid release-line configuration: {exc}", file=sys.stderr)
        return 1
    for key, value in outputs.items():
        print(f"{key}={value}")
    return 0


# ── plan: Build and apply a canonical release plan without generated Release Please refs


class Commit(BaseModel):
    """Conventional commit subject used to calculate a release bump."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1)
    body: str = ""


class ReleasePlan(BaseModel):
    """Version and changelog data produced by the planner."""

    model_config = ConfigDict(extra="forbid")

    current_version: str
    next_version: str
    bump: Literal["none", "patch", "minor", "major"]
    entries: list[str]
    breaking_entries: list[str] = []


VERSION_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$"
)
LOCK_PACKAGE = "vectora"
LOCK_BLOCK_RE = re.compile(r"(?m)^\[\[package\]\]\s*$")
LOCK_VERSION_RE = re.compile(r'(?m)^(version\s*=\s*)"[^"]+"')
HEADER_RE = re.compile(
    r"^(?P<kind>[a-z]+)(?:\([^)]*\))?(?P<breaking>!)?:\s+(?P<description>.+)$"
)


def _bump_kind(commit: Commit) -> str | None:
    match = HEADER_RE.fullmatch(commit.subject.strip())
    if match is None:
        return None
    if match.group("breaking") or re.search(r"(?m)^BREAKING[ -]CHANGE:\s", commit.body):
        return "major"
    kind = match.group("kind")
    if kind == "feat":
        return "minor"
    if kind in {"fix", "perf"}:
        return "patch"
    return None


def _version_tuple(version: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"invalid semantic version: {version}")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def calculate_plan(
    current_version: str,
    commits: list[Commit],
    *,
    bump_minor_pre_major: bool = True,
    bump_patch_for_minor_pre_major: bool = False,
) -> ReleasePlan:
    """Calculate the Release Please-compatible bump for one package."""
    major, minor, patch = _version_tuple(current_version)
    bumps = [_bump_kind(commit) for commit in commits]
    effective = [bump for bump in bumps if bump is not None]
    if not effective:
        return ReleasePlan(
            current_version=current_version,
            next_version=current_version,
            bump="none",
            entries=[],
        )
    bump: Literal["patch", "minor", "major"] = (
        "major"
        if "major" in effective
        else "minor"
        if "minor" in effective
        else "patch"
    )
    if major == 0 and bump == "major":
        bump = "minor" if bump_minor_pre_major else "patch"
    elif major == 0 and bump == "minor" and bump_patch_for_minor_pre_major:
        bump = "patch"
    if bump == "major":
        next_version = f"{major + 1}.0.0"
    elif bump == "minor":
        next_version = f"{major}.{minor + 1}.0"
    else:
        next_version = f"{major}.{minor}.{patch + 1}"
    entries = [
        commit.subject.strip() for commit in commits if _bump_kind(commit) is not None
    ]
    breaking_entries = [
        commit.subject.strip() for commit in commits if _bump_kind(commit) == "major"
    ]
    return ReleasePlan(
        current_version=current_version,
        next_version=next_version,
        bump=bump,
        entries=entries,
        breaking_entries=breaking_entries,
    )


def read_current_version(root: Path, files: list[str]) -> str:
    """Read the released version from the first configured version file."""
    if not files:
        raise ValueError("at least one version file is required")
    path = root / files[0]
    if path.suffix == ".json":
        version = json.loads(path.read_text(encoding="utf-8")).get("version")
    elif path.name == "pyproject.toml":
        project = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {})
        version = project.get("version")
    else:
        raise ValueError(f"unsupported version source: {path}")
    if not isinstance(version, str):
        raise ValueError(f"version field not found in {path}")
    _version_tuple(version)
    return version


def _replace_version(path: Path, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(text)
        data["version"] = version
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return
    if path.name == "pyproject.toml":
        updated, count = re.subn(
            r'(?m)^(version\s*=\s*)"[^"]+"', rf'\g<1>"{version}"', text, count=1
        )
        if count != 1:
            raise ValueError(f"version field not found in {path}")
        path.write_text(updated, encoding="utf-8")
        return
    if path.name == "uv.lock":
        path.write_text(_replace_lock_version(text, version, path), encoding="utf-8")
        return
    raise ValueError(f"unsupported version file: {path}")


def _replace_lock_version(text: str, version: str, path: Path) -> str:
    """Bump only the `version` of the project's own `[[package]]` block.

    `uv lock` rewrites the whole file and drops comments, so the block is
    located by its `name` line instead of a marker. Other packages may share
    the same version string and must stay untouched.
    """
    starts = [match.start() for match in LOCK_BLOCK_RE.finditer(text)]
    bounds = zip(starts, [*starts[1:], len(text)], strict=True)
    name_re = re.compile(rf'(?m)^name\s*=\s*"{re.escape(LOCK_PACKAGE)}"\s*$')
    matches = [
        (start, end) for start, end in bounds if name_re.search(text, start, end)
    ]
    if len(matches) != 1:
        raise ValueError(
            f'expected exactly one [[package]] named "{LOCK_PACKAGE}" in {path}, '
            f"found {len(matches)}"
        )
    start, end = matches[0]
    block, count = LOCK_VERSION_RE.subn(rf'\g<1>"{version}"', text[start:end], count=1)
    if count != 1:
        raise ValueError(f'version field not found for "{LOCK_PACKAGE}" in {path}')
    return text[:start] + block + text[end:]


def apply_plan(root: Path, plan: ReleasePlan, files: list[str]) -> None:
    """Update configured version files and prepend a generated changelog section."""
    for relative in files:
        _replace_version(root / relative, plan.next_version)
    changelog = root / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    if plan.bump == "none":
        return
    features = [entry for entry in plan.entries if entry.startswith("feat")]
    breaking = plan.breaking_entries
    fixes = [entry for entry in plan.entries if entry.startswith(("fix", "perf"))]
    lines = [
        f"## [{plan.next_version}] (" + str(datetime.now(UTC).date()) + ")",
        "",
    ]
    for title, entries in (
        ("⚠ BREAKING CHANGES", breaking),
        ("Features", features),
        ("Bug Fixes", fixes),
    ):
        if entries:
            lines.extend([f"### {title}", ""])
            lines.extend(f"- {entry}" for entry in entries)
            lines.append("")
    changelog.write_text("\n".join(lines) + "\n" + text.lstrip("\n"), encoding="utf-8")


def plan_main() -> int:
    """Read subjects, emit a JSON plan, and optionally apply it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument(
        "--current-version",
        help="defaults to the version in the first --files entry",
    )
    parser.add_argument("--commits-json", type=Path)
    parser.add_argument("--files", nargs="+", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--bump-patch-for-minor-pre-major", action="store_true")
    parser.add_argument(
        "--print-current-version",
        action="store_true",
        help="print the version in the first --files entry and exit",
    )
    args = parser.parse_args()
    if args.print_current_version:
        print(read_current_version(args.root, args.files))
        return 0
    if args.commits_json is None:
        parser.error("--commits-json is required to build a release plan")
    commits = [
        Commit.model_validate(item)
        for item in json.loads(args.commits_json.read_text(encoding="utf-8"))
    ]
    plan = calculate_plan(
        args.current_version or read_current_version(args.root, args.files),
        commits,
        bump_patch_for_minor_pre_major=args.bump_patch_for_minor_pre_major,
    )
    if args.apply:
        apply_plan(args.root, plan, args.files)
    print(plan.model_dump_json())
    return 0


# ── prepare-line: Atualiza as linhas de release no próprio branch gerado pelo Release Please


RELEASE_VERSION_HEADING = re.compile(
    r"^## \[(?P<version>\d+\.\d+\.\d+)\]", re.MULTILINE
)
MINOR_ZERO = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.0$")


def current_release_version(changelog: str) -> str | None:
    """Retorna a versão da primeira seção gerada no changelog."""
    match = RELEASE_VERSION_HEADING.search(changelog)
    return match.group("version") if match else None


def update_release_line_files(
    changelog_path: Path,
    lines_path: Path,
    target_branch: str,
) -> bool:
    """Atualiza a configuração quando o PR representa uma nova minor."""
    version = current_release_version(changelog_path.read_text(encoding="utf-8"))
    if version is None:
        return False
    match = MINOR_ZERO.fullmatch(version)
    if match is None:
        return False

    config = ReleaseLines.model_validate(
        json.loads(lines_path.read_text(encoding="utf-8"))
    )
    if target_branch != config.development.branch:
        return False
    expected = f"{match.group('major')}.{match.group('minor')}"
    if config.development.milestone != expected:
        return False

    next_minor = int(match.group("minor")) + 1
    next_development = f"release/{match.group('major')}.{next_minor}"
    config = ReleaseLines(
        version_files=config.version_files,
        development=config.development.model_copy(
            update={
                "branch": next_development,
                "milestone": f"{match.group('major')}.{next_minor}",
            }
        ),
        maintenance=config.maintenance.model_copy(
            update={
                "branch": "master",
                "milestone": f"{match.group('major')}.{match.group('minor')}.x",
            }
        ),
    )
    lines_path.write_text(
        json.dumps(config.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    return True


def prepare_line_main() -> int:
    """Executa a atualização com argumentos próprios do workflow."""
    if len(sys.argv) != 4:
        print(
            "usage: release.py prepare-line CHANGELOG LINES TARGET_BRANCH",
            file=sys.stderr,
        )
        return 2
    try:
        changed = update_release_line_files(
            Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid release-line inputs: {exc}", file=sys.stderr)
        return 1
    print(f"updated={'true' if changed else 'false'}")
    return 0


# ── rotate: Calcula e aplica a próxima rotação das linhas de release ─────────


SEMVER_TAG: re.Pattern[str] = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.0$"
)


class Rotation(TypedDict):
    """Metadados necessários para rotacionar as linhas de release ativas."""

    release_tag: str
    release_version: str
    maintenance_branch: str
    maintenance_milestone: str
    development_branch: str
    development_milestone: str
    previous_maintenance_branch: str
    previous_maintenance_milestone: str
    previous_development_branch: str
    previous_development_milestone: str


class OpenPullRequest(TypedDict):
    """Metadados relevantes de uma pull request aberta durante a rotação."""

    number: int
    base_branch: str
    milestone: str | None


class RotationOperation(TypedDict):
    """Uma operação determinística executada pelo workflow de rotação do GitHub."""

    kind: Literal[
        "create_branch",
        "ensure_milestone",
        "update_base",
        "update_milestone",
        "publish_config",
    ]
    pull_request: int | None
    value: str


def build_rotation_plan(
    rotation: Rotation,
    pull_requests: list[OpenPullRequest],
) -> list[RotationOperation]:
    """Build operations to open the next minor from the stable master line."""
    plan: list[RotationOperation] = [
        {
            "kind": "create_branch",
            "pull_request": None,
            "value": rotation["development_branch"],
        },
        {
            "kind": "ensure_milestone",
            "pull_request": None,
            "value": rotation["maintenance_milestone"],
        },
        {
            "kind": "ensure_milestone",
            "pull_request": None,
            "value": rotation["development_milestone"],
        },
    ]
    for pull_request in pull_requests:
        if pull_request["base_branch"] != rotation["previous_development_branch"]:
            continue
        plan.append(
            {
                "kind": "update_base",
                "pull_request": pull_request["number"],
                "value": rotation["development_branch"],
            }
        )
        if pull_request["milestone"] == rotation["previous_development_milestone"]:
            plan.append(
                {
                    "kind": "update_milestone",
                    "pull_request": pull_request["number"],
                    "value": rotation["development_milestone"],
                }
            )
    plan.append(
        {
            "kind": "publish_config",
            "pull_request": None,
            "value": rotation["release_tag"],
        }
    )
    return plan


def rotation_for_release(
    tag: str, config: ReleaseLines, target_branch: str | None = None
) -> Rotation | None:
    """Calculate the next minor branch after a minor is published on master."""
    match = SEMVER_TAG.fullmatch(tag)
    if match is None:
        return None
    if target_branch != config.maintenance.branch and not (
        target_branch is not None and re.fullmatch(r"[0-9a-fA-F]{7,64}", target_branch)
    ):
        return None
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    expected = f"{major}.{minor}"
    release_version = f"{expected}.0"
    configured = config.development.milestone
    configured_match = re.fullmatch(r"(?P<major>\d+)\.(?P<minor>\d+)", configured)
    if configured_match is None:
        raise ValueError(f"invalid development milestone: {configured}")
    configured_version = (
        int(configured_match.group("major")),
        int(configured_match.group("minor")),
    )
    if (major, minor) > configured_version:
        raise ValueError(
            f"published release {expected} is newer than configured development milestone {configured}; merge the pending rotation before retrying"
        )
    next_minor = minor + 1
    next_development = f"release/{major}.{next_minor}"
    already_rotated = (
        configured == f"{major}.{next_minor}"
        and config.development.branch == next_development
        and config.maintenance.milestone == f"{expected}.x"
    )
    if configured != expected and not already_rotated:
        return None
    return {
        "release_tag": tag,
        "release_version": release_version,
        "maintenance_branch": config.maintenance.branch,
        "maintenance_milestone": f"{expected}.x",
        "development_branch": next_development,
        "development_milestone": f"{major}.{next_minor}",
        "previous_maintenance_branch": config.maintenance.branch,
        "previous_maintenance_milestone": config.maintenance.milestone,
        "previous_development_branch": f"release/{expected}",
        "previous_development_milestone": expected,
    }


def rotated_config(config: ReleaseLines, rotation: Rotation) -> ReleaseLines:
    """Monta a próxima configuração sem alterar o mapeamento carregado."""
    return ReleaseLines(
        version_files=config.version_files,
        development={
            "branch": rotation["development_branch"],
            "milestone": rotation["development_milestone"],
        },
        maintenance={
            "branch": rotation["maintenance_branch"],
            "milestone": rotation["maintenance_milestone"],
        },
    )


def write_rotated_config(path: Path, config: ReleaseLines, rotation: Rotation) -> None:
    """Persiste a próxima configuração das linhas de release como JSON formatado."""
    path.write_text(
        json.dumps(rotated_config(config, rotation).model_dump(mode="json"), indent=2)
        + "\n",
        encoding="utf-8",
    )


def _event_values(event_path: Path) -> tuple[str, str | None]:
    """Valida o envelope antes de acessar os metadados da release."""
    event = json.loads(event_path.read_text(encoding="utf-8"))
    if not isinstance(event, dict):
        raise ValueError("release event must be an object")
    if event.get("inputs") is not None:
        inputs = event["inputs"]
        if not isinstance(inputs, dict):
            raise ValueError("workflow_dispatch inputs must be an object")
        tag = inputs.get("release_tag")
        target = inputs.get("target_branch", "master")
        if not isinstance(tag, str) or not tag:
            raise ValueError("workflow_dispatch is missing release_tag")
        return tag, target if isinstance(target, str) else None
    release = event.get("release")
    if not isinstance(release, dict):
        raise ValueError("release event is missing release metadata")
    tag = release.get("tag_name")
    target = release.get("target_commitish")
    if not isinstance(tag, str) or not tag:
        raise ValueError("release event is missing tag_name")
    return tag, target if isinstance(target, str) else None


def rotate_main() -> int:
    """Imprime as saídas do GitHub Actions e opcionalmente atualiza o arquivo de configuração."""
    if len(sys.argv) not in (2, 4) or (len(sys.argv) == 4 and sys.argv[2] != "--write"):
        print(
            "usage: release.py rotate EVENT_JSON [--write CONFIG_PATH]",
            file=sys.stderr,
        )
        return 2
    try:
        event_path = Path(sys.argv[1])
        config_path = Path(sys.argv[3]) if len(sys.argv) == 4 else CONFIG_PATH
        config = load_release_lines(config_path)
        tag, target = _event_values(event_path)
        rotation = rotation_for_release(tag, config, target)
        if rotation is None:
            print("rotate=false")
            return 0
        if len(sys.argv) == 4:
            write_rotated_config(config_path, config, rotation)
        for key, value in rotation.items():
            print(f"{key}={value}")
        print("rotate=true")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"invalid release event or release-line configuration: {exc}",
            file=sys.stderr,
        )
        return 1


# ── select-pr: Seleciona uma PR de release sem misturar a minor e os patches em master


class Label(BaseModel):
    """Nome do label retornado pelo GitHub CLI."""

    name: str


class Repository(BaseModel):
    """Identidade completa do repositório de origem."""

    nameWithOwner: str


class ReleasePullRequest(BaseModel):
    """Campos usados para verificar origem, versão e destino da release."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    number: int
    headRefName: str
    headRefOid: str
    baseRefName: str
    headRepository: Repository | None = None
    labels: list[Label] = Field(default_factory=list)
    title: str
    body: str = ""


def select_release_pr(
    pull_requests: list[ReleasePullRequest],
    *,
    repository: str,
    source: str,
    target: str,
    milestone: str,
) -> ReleasePullRequest | None:
    """Exige identidade exata da linha e rejeita seleções ambíguas."""
    minor = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)", milestone)
    patch = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.x", milestone)
    if minor is None and patch is None:
        raise ValueError("invalid release milestone")
    series = milestone.removesuffix(".x")
    candidates: list[ReleasePullRequest] = []
    for pr in pull_requests:
        if (
            pr.headRepository is None
            or pr.headRepository.nameWithOwner != repository
            or "autorelease: pending" not in {label.name for label in pr.labels}
        ):
            continue
        version = re.fullmatch(r"chore\([^)]*\): release (\d+\.\d+)\.(\d+)", pr.title)
        if version is None or version[1] != series:
            continue
        if (minor is not None) != (version[2] == "0"):
            continue
        suffix = series if minor is not None else f"{series}.{version[2]}"
        matches = pr.baseRefName == target and pr.headRefName == f"release/{suffix}"
        if matches:
            candidates.append(pr)
    if len(candidates) > 1:
        raise ValueError("ambiguous release PRs for the current line")
    return candidates[0] if candidates else None


def select_pr_main() -> int:
    """Filtra o JSON do GitHub CLI e emite um único objeto validado."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--milestone", required=True)
    args = parser.parse_args()
    try:
        prs = TypeAdapter(list[ReleasePullRequest]).validate_json(sys.stdin.read())
        selected = select_release_pr(
            prs,
            repository=args.repository,
            source=args.source,
            target=args.target,
            milestone=args.milestone,
        )
    except ValueError as exc:
        print(f"Invalid release PR selection: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(selected.model_dump() if selected else {}))
    return 0


# ── pr-body: Monta o corpo da release só com a seção da versão atual ──────


MAX_BODY_BYTES = 60_000
RELEASE_SECTION_HEADING = re.compile(r"^## \[[^\]]+\].*$", re.MULTILINE)
NEXT_RELEASE_SECTION_HEADING = re.compile(r"^## \[[^\]]+\].*$", re.MULTILINE)
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
PROCESS_METADATA = re.compile(
    r"\s*\([^()\n]*(?:Sprint|Bloco|Fase|CodeRabbit|Claude|Copilot|ChatGPT)[^()\n]*\)",
    re.IGNORECASE,
)


def current_release_section(notes: str) -> tuple[str, str, str]:
    """Retorna o preâmbulo, a primeira seção de release e o rodapé."""
    match = RELEASE_SECTION_HEADING.search(notes)
    if match is None:
        raise ValueError("release notes do not contain a release heading")
    next_match = NEXT_RELEASE_SECTION_HEADING.search(notes, match.end())
    end = next_match.start() if next_match else len(notes)
    # A second release heading starts the historical portion.  It must not be
    # copied into the PR body; the current section is the only release context
    # that Release Please should publish.
    prefix = notes[: match.start()].strip()
    if prefix.startswith("# Changelog"):
        prefix = ""
    return prefix, notes[match.start() : end].strip(), ""


def _compact_links(section: str) -> str:
    """Remove URLs repetitivas sem remover os títulos e referências visíveis."""
    heading, separator, remainder = section.partition("\n")
    compacted_remainder = MARKDOWN_LINK.sub(r"\1", remainder)
    return heading + separator + compacted_remainder


def _strip_process_metadata(section: str) -> str:
    """Remove planning and review provenance from generated change entries."""
    return PROCESS_METADATA.sub("", section)


def prepare_body(notes: str, max_body_bytes: int = MAX_BODY_BYTES) -> str:
    """Monta um corpo com a seção atual e mantém-o dentro do limite do GitHub."""
    prefix, section, suffix = current_release_section(notes)

    def join_parts(current_section: str) -> str:
        return (
            "\n\n".join(part for part in (prefix, current_section, suffix) if part)
            + "\n"
        )

    section = _strip_process_metadata(section)
    body = join_parts(section)
    if len(body.encode("utf-8")) <= max_body_bytes:
        return body

    compacted = join_parts(_compact_links(section))
    if len(compacted.encode("utf-8")) <= max_body_bytes:
        return compacted

    # Keep the PR body reviewable when the generated changelog contains more
    # entries than GitHub accepts. The full notes remain in the branch.
    heading = section.partition("\n")[0]
    summary = (
        f"{heading}\n\nThe complete release notes are maintained in `CHANGELOG.md`.\n"
    )
    if len(summary.encode("utf-8")) > max_body_bytes:
        raise ValueError(
            "the pull-request body limit is too small for a release heading"
        )
    return summary


def pr_body_main() -> int:
    """Lê as notas e grava o corpo pronto para ``gh pr edit --body-file``."""
    if len(sys.argv) != 3:
        print("usage: release.py pr-body NOTES OUTPUT", file=sys.stderr)
        return 2
    try:
        raw_notes = Path(sys.argv[1]).read_bytes()
        try:
            notes = raw_notes.decode("utf-8")
        except UnicodeDecodeError:
            # Older generated notes can contain Portuguese text written using
            # the Windows code page.  Normalize that input to UTF-8 on output.
            notes = raw_notes.decode("cp1252")
        Path(sys.argv[2]).write_text(prepare_body(notes), encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"invalid release notes: {exc}", file=sys.stderr)
        return 1
    return 0


# ── normalize-changelog: Normalize unordered list markers in the generated changelog


_LIST_MARKER = re.compile(r"^([ ]{0,3})\*\s+")
_FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})(.*?)(?:\r?\n)?$")
_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*(?:\r?\n)?$")


def _heading_intro(heading: str, level: int) -> str:
    """Return stable prose for a generated heading without an introduction."""
    normalized = heading.casefold()
    if level == 2:
        return "Esta seção reúne as alterações publicadas nesta versão."
    if "feature" in normalized:
        return "Os recursos incluídos nesta versão são listados abaixo."
    if "bug" in normalized or "fix" in normalized:
        return "As correções incluídas nesta versão são listadas abaixo."
    return "Os itens desta seção são listados abaixo."


def _advance_fence(
    line: str, fence_char: str | None, fence_length: int
) -> tuple[str | None, int]:
    """Update fenced-code state after consuming a changelog line."""
    fence_match = _FENCE.match(line)
    if fence_char is None and fence_match:
        delimiter = fence_match.group(1)
        info = fence_match.group(2)
        # CommonMark forbids backticks in the info string of a backtick
        # fence, but permits the opposite delimiter. Tilde fences accept
        # either character in their info string. Keep the delimiter itself
        # homogeneous while allowing valid language identifiers such as
        # ``~~~`python`` and `````~~~``.
        if delimiter[0] == "`" and "`" in info:
            return fence_char, fence_length
        return delimiter[0], len(delimiter)
    if fence_char is not None and fence_match:
        delimiter = fence_match.group(1)
        trailing = fence_match.group(2).strip()
        if (
            delimiter[0] == fence_char
            and len(delimiter) >= fence_length
            and not trailing
        ):
            return None, 0
    return fence_char, fence_length


def _ensure_latest_release_intros(lines: list[str]) -> list[str]:
    """Insert prose before generated lists in the newest release section.

    Older releases are left untouched so a normalization commit cannot rewrite
    the entire historical changelog. The newest section is the only one that
    Release Please updates on each run.
    """
    release_start: int | None = None
    release_end = len(lines)
    fence_char: str | None = None
    fence_length = 0
    for index, line in enumerate(lines):
        heading_match = _HEADING.match(line.rstrip("\r\n"))
        if fence_char is None and heading_match and len(heading_match.group(1)) == 2:
            if release_start is None:
                release_start = index
            else:
                release_end = index
                break
        fence_char, fence_length = _advance_fence(line, fence_char, fence_length)
    if release_start is None:
        return lines
    result = lines[:release_start]
    index = release_start
    fence_char = None
    fence_length = 0
    while index < release_end:
        line = lines[index]
        result.append(line)
        heading_match = _HEADING.match(line.rstrip("\r\n"))
        if fence_char is None and heading_match:
            lookahead = index + 1
            while lookahead < release_end and not lines[lookahead].strip():
                lookahead += 1
            if lookahead < release_end:
                first = lines[lookahead].lstrip()
                if first.startswith(("#", "- ", "* ")):
                    ending = "\r\n" if line.endswith("\r\n") else "\n"
                    result.extend(
                        [
                            ending,
                            _heading_intro(
                                heading_match.group(2), len(heading_match.group(1))
                            )
                            + ending,
                            ending,
                        ]
                    )
                    index = lookahead
                    fence_char, fence_length = _advance_fence(
                        line, fence_char, fence_length
                    )
                    continue
        fence_char, fence_length = _advance_fence(line, fence_char, fence_length)
        index += 1
    result.extend(lines[release_end:])
    return result


def normalize_changelog(path: Path) -> bool:
    """Replace unordered ``*`` markers with ``-`` outside fenced code blocks.

    Returns whether the file was changed. Running this function repeatedly is
    safe because the replacement only targets a single-star list marker.
    """
    original = path.read_text(encoding="utf-8")
    normalized_lines: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    for line in original.splitlines(keepends=True):
        fence_match = _FENCE.match(line)
        was_fenced = fence_char is not None
        normalized_line = line
        if not was_fenced and not fence_match:
            normalized_line = _LIST_MARKER.sub(r"\1- ", line)
        normalized_lines.append(normalized_line)
        fence_char, fence_length = _advance_fence(line, fence_char, fence_length)

    normalized = "".join(_ensure_latest_release_intros(normalized_lines))
    if normalized == original:
        return False

    with path.open("w", encoding="utf-8", newline="") as changelog:
        changelog.write(normalized)
    return True


def normalize_changelog_main() -> int:
    """Normalize the changelog at the supplied path or the repository default."""
    path = (
        Path(sys.argv[1])
        if len(sys.argv) == 2
        else Path(__file__).parents[1] / "CHANGELOG.md"
    )
    changed = normalize_changelog(path)
    print(f"{'Normalized' if changed else 'Already normalized'} {path}")
    return 0


# ── validate-milestone: Valida a milestone declarada contra a base ─────────


class LabelPayload(BaseModel):
    """Subconjunto de um label do GitHub incluído nos eventos de pull request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    name: str = ""


class RepositoryPayload(BaseModel):
    """Identidade do repositório associada ao head de uma pull request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    full_name: str | None = None


class HeadPayload(BaseModel):
    """Identidade da branch e do repositório do head de uma pull request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    ref: str = ""
    label: str = ""
    repo: RepositoryPayload | None = None


# ``from __future__ import annotations`` defers the nested model annotation.
# Rebuild this leaf model explicitly because ``main`` constructs a replacement
# head when GitHub provides the current branch through environment variables.
HeadPayload.model_rebuild()


class BasePayload(BaseModel):
    """Identidade da branch base de uma pull request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    ref: str = ""


class MilestonePayload(BaseModel):
    """Metadados da milestone de um evento de pull request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    title: str = ""


class PullRequestPayload(BaseModel):
    """Campos do evento consumidos pelo validador de linhas de release."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    base: BasePayload | None = None
    head: HeadPayload | None = None
    labels: list[LabelPayload] = Field(default_factory=list)
    milestone: MilestonePayload | None = None
    title: str = ""


class RepositoryEventPayload(BaseModel):
    """Identidade do repositório extraída do envelope do webhook."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    full_name: str = ""


class PullRequestEvent(BaseModel):
    """Subconjunto validado do envelope de webhook de pull request do GitHub."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    repository: RepositoryEventPayload | None = None
    pull_request: PullRequestPayload | None = None


PullRequestEvent.model_rebuild()


type EventPayload = PullRequestEvent | dict[str, object]

_VEXT_TOKEN = re.compile(r"(?<![A-Za-z0-9])vext(?![A-Za-z0-9])", re.IGNORECASE)
_RELEASE_PLEASE_LABEL = "autorelease: pending"
_RELEASE_PLEASE_MINOR_BRANCH = re.compile(
    r"^release-please-(?P<major>\d+)\.(?P<minor>\d+)$"
)
_RELEASE_PLEASE_PATCH_BRANCH = re.compile(
    r"^release-please-(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>[1-9]\d*)$"
)
_STACK_BASE = re.compile(r"^stack/[a-z0-9][a-z0-9-]*$")
_CONTRACTS_BASE = "feat/contratos-workbenches"
_INTERNAL_REVIEW_BASE = "feat/llm-workbench-integration"
_RELEASE_BRANCH = re.compile(r"^release/.+$")


@lru_cache(maxsize=1)
def _release_lines() -> ReleaseLines:
    """Carrega branches e milestones ativas da configuração versionada."""
    return load_release_lines(CONFIG_PATH)


def _line_for_base(base: str) -> ReleaseLine | None:
    """Retorna a linha de release configurada para a base de uma pull request."""
    config = _release_lines()
    return next(
        (
            line
            for line in (config.development, config.maintenance)
            if line.branch == base
        ),
        None,
    )


def _parse_event(event: EventPayload) -> PullRequestEvent | None:
    """Normaliza dicionários de teste e rejeita estruturas de webhook inválidas."""
    if isinstance(event, PullRequestEvent):
        return event
    try:
        return PullRequestEvent.model_validate(event)
    except ValidationError:
        return None


def _repository_name(event: PullRequestEvent) -> str | None:
    """Resolve the trusted repository name from the event or Actions env."""
    return (event.repository.full_name if event.repository else None) or os.environ.get(
        "GITHUB_REPOSITORY"
    )


def _labels(pull_request: PullRequestPayload) -> set[str]:
    """Retorna os nomes dos labels do payload validado do evento do GitHub."""
    return {label.name for label in pull_request.labels}


def _is_release_please_pr(
    event: PullRequestEvent, pull_request: PullRequestPayload
) -> bool:
    """Reconhece uma PR automática de release usando o contexto imutável do repositório."""
    head = pull_request.head
    head_repo = head.repo.full_name if head and head.repo else None
    repository_name = _repository_name(event)
    base = pull_request.base.ref if pull_request.base else ""
    head_ref = head.ref if head else ""
    if head_repo != repository_name or _RELEASE_PLEASE_LABEL not in _labels(
        pull_request
    ):
        return False
    line = _line_for_base(base)
    if line is None:
        return False

    config = _release_lines()
    match = _RELEASE_PLEASE_MINOR_BRANCH.fullmatch(head_ref)
    if match:
        return bool(
            base == config.maintenance.branch
            and f"{match['major']}.{match['minor']}" == config.development.milestone
        )

    if line is not config.maintenance:
        return False

    match = _RELEASE_PLEASE_PATCH_BRANCH.fullmatch(head_ref)
    return bool(match and f"{match['major']}.{match['minor']}.x" == line.milestone)


def _is_release_branch_pr(
    event: PullRequestEvent, pull_request: PullRequestPayload
) -> bool:
    """Aceita promoções internas de branches release/* para a base master."""
    base = pull_request.base.ref if pull_request.base else ""
    head = pull_request.head
    head_ref = head.ref if head else ""
    head_repo = head.repo.full_name if head and head.repo else None
    return bool(
        base == _release_lines().maintenance.branch
        and _RELEASE_BRANCH.fullmatch(head_ref) is not None
        and head_repo == _repository_name(event)
    )


def _is_internal_head(
    event: PullRequestEvent, pull_request: PullRequestPayload
) -> bool:
    """Confirma a identidade completa do repositório do head da PR."""
    head = pull_request.head
    repository_name = _repository_name(event)
    if head is None or repository_name is None:
        return False
    head_repo = head.repo.full_name if head.repo else None
    return head_repo == repository_name


def _is_vext_pr(pull_request: PullRequestPayload) -> bool:
    """Identifica títulos e branches VEXT para diagnósticos claros de validação."""
    head_ref = pull_request.head.ref if pull_request.head else ""
    return bool(_VEXT_TOKEN.search(pull_request.title) or _VEXT_TOKEN.search(head_ref))


def validate_pull_request(
    event: EventPayload, stack_root_base: str | None = None
) -> list[str]:
    """Retorna erros de validação acionáveis para um evento de pull request.

    `stack_root_base` é a branch da linha de release que fica na raiz de uma
    pilha de PRs (resolvida pelo workflow seguindo as bases pelas PRs abertas).
    Uma PR empilhada de head interno é validada contra a linha dessa raiz.
    """
    parsed_event = _parse_event(event)
    if parsed_event is None:
        return ["O payload do webhook de pull request é inválido."]

    pull_request = parsed_event.pull_request
    if pull_request is None:
        return []

    errors: list[str] = []
    base = pull_request.base.ref if pull_request.base else ""
    if _is_release_please_pr(parsed_event, pull_request) or _is_release_branch_pr(
        parsed_event, pull_request
    ):
        return []
    if (
        _STACK_BASE.fullmatch(base) or base in {_CONTRACTS_BASE, _INTERNAL_REVIEW_BASE}
    ) and _is_internal_head(parsed_event, pull_request):
        return []
    line = _line_for_base(base)
    if (
        line is None
        and stack_root_base
        and _is_internal_head(parsed_event, pull_request)
    ):
        line = _line_for_base(stack_root_base)
    if line is None:
        configured_bases = ", ".join(
            configured.branch
            for configured in (
                _release_lines().development,
                _release_lines().maintenance,
            )
        )
        errors.append(
            "PRs de código devem usar uma base declarada na configuração de release "
            f"({configured_bases}); "
            f"base recebida: `{base or '(vazia)'}`."
        )
    else:
        milestone = pull_request.milestone
        title = milestone.title.strip() if milestone else ""
        expected = line.milestone
        if not title:
            errors.append(
                "Atribua exatamente uma milestone de release: "
                f"`{expected}` para esta linha."
            )
        elif line is _release_lines().development and title != expected:
            stream = "VEXT" if _is_vext_pr(pull_request) else "de funcionalidade"
            errors.append(
                f"A milestone `{title}` não é compatível com a base `{base}`; "
                f"PRs {stream} da próxima minor devem usar `{expected}`."
            )
        elif title != expected:
            errors.append(
                f"A milestone `{title}` não é compatível com `{base}`; "
                f"use `{expected}`."
            )
    return errors


def validate_milestone_main() -> int:
    """Valida o arquivo de evento fornecido pelo GitHub Actions."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        print("GITHUB_EVENT_PATH não foi definido.", file=sys.stderr)
        return 2
    try:
        payload = PullRequestEvent.model_validate_json(Path(event_path).read_bytes())
    except (OSError, ValueError, ValidationError) as exc:
        print(f"Evento GitHub inválido: {exc}", file=sys.stderr)
        return 2
    current_milestone = os.environ.get("CURRENT_RELEASE_MILESTONE")
    current_base = os.environ.get("CURRENT_PR_BASE")
    current_head = os.environ.get("CURRENT_PR_HEAD_REF") or os.environ.get(
        "GITHUB_HEAD_REF"
    )
    current_head_repo = os.environ.get("CURRENT_PR_HEAD_REPO")
    if payload.pull_request is not None:
        # Um output ausente chega como string vazia; nesse caso a milestone
        # recebida no evento é preservada em vez de ser substituída por vazio.
        if current_milestone:
            payload.pull_request.milestone = MilestonePayload(title=current_milestone)
        if current_base:
            payload.pull_request.base = BasePayload(ref=current_base)
        if current_head:
            event_head_repo = (
                payload.pull_request.head.repo.full_name
                if payload.pull_request.head and payload.pull_request.head.repo
                else None
            )
            validated_head_repo = current_head_repo or event_head_repo
            payload.pull_request.head = HeadPayload(
                ref=current_head,
                repo=RepositoryPayload(full_name=validated_head_repo)
                if validated_head_repo
                else None,
            )
    errors = validate_pull_request(
        payload, stack_root_base=os.environ.get("CURRENT_STACK_ROOT_BASE") or None
    )
    if errors:
        for error in errors:
            print(f"::error::{error}")
        return 1
    print("Milestone de release compatível com a branch base.")
    return 0


# ── update-channel: Select the update channel for a Vectora package version ──


UpdateChannel = Literal["maintenance", "latest"]
_VERSION = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$"
)


def select_update_channel(version: str | None) -> UpdateChannel:
    """Return the release stream for a strict ``major.minor.patch`` version."""
    if version is None:
        raise ValueError("Versão do pacote inválida: None")
    match = _VERSION.fullmatch(version.strip())
    if match is None:
        raise ValueError(f"Versão do pacote inválida: {version!r}")
    if match.group("major") == "0" and match.group("minor") == "1":
        return "maintenance"
    return "latest"


def update_channel_main(argv: Sequence[str] | None = None) -> int:
    """Print the selected channel for the workflow and return a shell status."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: release.py update-channel MAJOR.MINOR.PATCH", file=sys.stderr)
        return 2
    try:
        print(select_update_channel(args[0]))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────────


_COMMANDS: dict[str, tuple[Callable[..., int], bool]] = {
    "line": (line_main, False),
    "plan": (plan_main, False),
    "prepare-line": (prepare_line_main, False),
    "rotate": (rotate_main, False),
    "select-pr": (select_pr_main, False),
    "pr-body": (pr_body_main, False),
    "normalize-changelog": (normalize_changelog_main, False),
    "validate-milestone": (validate_milestone_main, False),
    "update-channel": (update_channel_main, True),
}


def main(argv: Sequence[str] | None = None) -> int:
    """Despacha `release.py <comando> [args...]` para o subcomando.

    Subcomandos sem argv explícito leem `sys.argv` como se fossem o script
    original: `sys.argv[0]` vira `"release.py <comando>"` e o resto segue igual.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in _COMMANDS:
        print(f"usage: release.py {{{'|'.join(_COMMANDS)}}} [args...]", file=sys.stderr)
        return 2
    command, rest = args[0], args[1:]
    handler, takes_argv = _COMMANDS[command]
    if takes_argv:
        return handler(rest)
    sys.argv = [f"release.py {command}", *rest]
    return handler()


if __name__ == "__main__":
    raise SystemExit(main())
