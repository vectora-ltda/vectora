"""Seleciona uma PR de release sem misturar a minor e os patches em master."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


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
    phase: Literal["generated", "versioned"],
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
        if phase == "generated":
            legacy_heads = {
                f"release-please--branches--{branch}--components--vectora"
                for branch in (source, source.replace("/", "%2F"))
            }
            matches = pr.baseRefName == source and pr.headRefName in legacy_heads
        else:
            suffix = series if minor is not None else f"{series}.{version[2]}"
            matches = (
                pr.baseRefName == target
                and pr.headRefName == f"release-please-{suffix}"
            )
        if matches:
            candidates.append(pr)
    if len(candidates) > 1:
        raise ValueError("ambiguous release PRs for the current line")
    return candidates[0] if candidates else None


def main() -> int:
    """Filtra o JSON do GitHub CLI e emite um único objeto validado."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--milestone", required=True)
    parser.add_argument("--phase", choices=("generated", "versioned"), required=True)
    args = parser.parse_args()
    try:
        prs = TypeAdapter(list[ReleasePullRequest]).validate_json(sys.stdin.read())
        selected = select_release_pr(
            prs,
            repository=args.repository,
            source=args.source,
            target=args.target,
            milestone=args.milestone,
            phase=args.phase,
        )
    except ValueError as exc:
        print(f"Invalid release PR selection: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(selected.model_dump() if selected else {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
