"""Valida a linha de release declarada pela milestone de uma pull request."""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError


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
    repo: RepositoryPayload | None = None


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


class ReleaseLine(BaseModel):
    """Branch e milestone de uma linha de release ativa."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    branch: str
    milestone: str


class ReleaseLineConfig(BaseModel):
    """Fonte versionada da verdade para as linhas de release ativas."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    development: ReleaseLine
    maintenance: ReleaseLine


ReleaseLineConfig.model_rebuild()


@lru_cache(maxsize=1)
def _release_lines() -> ReleaseLineConfig:
    """Carrega branches e milestones ativas da configuração versionada."""
    config_path = Path(__file__).parents[1] / ".github" / "release-lines.json"
    return ReleaseLineConfig.model_validate_json(config_path.read_bytes())


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


def _labels(pull_request: PullRequestPayload) -> set[str]:
    """Retorna os nomes dos labels do payload validado do evento do GitHub."""
    return {label.name for label in pull_request.labels}


def _is_release_please_pr(
    event: PullRequestEvent, pull_request: PullRequestPayload
) -> bool:
    """Reconhece uma PR automática de release usando o contexto imutável do repositório."""
    head = pull_request.head
    head_repo = head.repo.full_name if head and head.repo else None
    repository = event.repository
    base = pull_request.base.ref if pull_request.base else ""
    expected_head = f"release-please--branches--{base}--components--vectora"
    return (
        head_repo == (repository.full_name if repository else None)
        and (head.ref if head else "") == expected_head
        and _RELEASE_PLEASE_LABEL in _labels(pull_request)
    )


def _is_vext_pr(pull_request: PullRequestPayload) -> bool:
    """Identifica títulos e branches VEXT para diagnósticos claros de validação."""
    head_ref = pull_request.head.ref if pull_request.head else ""
    return bool(_VEXT_TOKEN.search(pull_request.title) or _VEXT_TOKEN.search(head_ref))


def validate_pull_request(event: EventPayload) -> list[str]:
    """Retorna erros de validação acionáveis para um evento de pull request."""
    parsed_event = _parse_event(event)
    if parsed_event is None:
        return ["O payload do webhook de pull request é inválido."]

    pull_request = parsed_event.pull_request
    if pull_request is None:
        return []

    errors: list[str] = []
    base = pull_request.base.ref if pull_request.base else ""
    if _is_release_please_pr(parsed_event, pull_request):
        return []
    line = _line_for_base(base)
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


def main() -> int:
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
    if payload.pull_request is not None:
        if current_milestone is not None:
            payload.pull_request.milestone = MilestonePayload(title=current_milestone)
        if current_base:
            payload.pull_request.base = BasePayload(ref=current_base)
    errors = validate_pull_request(payload)
    if errors:
        for error in errors:
            print(f"::error::{error}")
        return 1
    print("Milestone de release compatível com a branch base.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
