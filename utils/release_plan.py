"""Build and apply a canonical release plan without generated Release Please refs."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


def apply_plan(
    root: Path, plan: ReleasePlan, files: list[str], manifest: str | None = None
) -> None:
    """Update configured version files and prepend a generated changelog section."""
    for relative in files:
        _replace_version(root / relative, plan.next_version)
    if manifest is not None:
        manifest_path = root / manifest
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["."] = plan.next_version
        manifest_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    changelog = root / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    if plan.bump == "none":
        return
    features = [entry for entry in plan.entries if entry.startswith("feat")]
    breaking = plan.breaking_entries
    fixes = [entry for entry in plan.entries if entry.startswith(("fix", "perf"))]
    lines = [
        f"## [{plan.next_version}] (" + str(date.today()) + ")",
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


def main() -> int:
    """Read subjects, emit a JSON plan, and optionally apply it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--commits-json", type=Path, required=True)
    parser.add_argument("--files", nargs="+", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--bump-patch-for-minor-pre-major", action="store_true")
    args = parser.parse_args()
    commits = [
        Commit.model_validate(item)
        for item in json.loads(args.commits_json.read_text(encoding="utf-8"))
    ]
    plan = calculate_plan(
        args.current_version,
        commits,
        bump_patch_for_minor_pre_major=args.bump_patch_for_minor_pre_major,
    )
    if args.apply:
        apply_plan(args.root, plan, args.files, args.manifest)
    print(plan.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
