"""Valida isolamento de releases concorrentes e seleção de PRs confiáveis."""

from __future__ import annotations

import io
import sys
from typing import Literal

import pytest
import select_release_pr as selector
from select_release_pr import ReleasePullRequest, select_release_pr


def _pr(version: str, *, generated: bool = False) -> ReleasePullRequest:
    """Cria uma resposta do CLI para a minor ou o patch em master."""
    minor = version.endswith(".0")
    source = "release/0.3" if minor else "master"
    suffix = "0.3" if minor else version
    return ReleasePullRequest.model_validate(
        {
            "number": 3 if minor else 20,
            "baseRefName": source if generated else "master",
            "headRefName": f"release-please--branches--{source}--components--vectora"
            if generated
            else f"release-please-{suffix}",
            "headRefOid": "a" * 40,
            "headRepository": {"nameWithOwner": "vectora-ltda/vectora"},
            "labels": [{"name": "autorelease: pending"}],
            "title": f"chore(master): release {version}",
        }
    )


@pytest.mark.parametrize("phase", ["generated", "versioned"])
@pytest.mark.parametrize("minor", [False, True])
def test_parallel_lines_are_isolated(
    phase: Literal["generated", "versioned"], minor: bool
) -> None:
    """A PR mais recente do patch não é selecionada durante a minor e vice-versa."""
    prs = [
        _pr("0.3.0", generated=phase == "generated"),
        _pr("0.2.1", generated=phase == "generated"),
    ]
    result = select_release_pr(
        prs,
        repository="vectora-ltda/vectora",
        source="release/0.3" if minor else "master",
        target="master",
        milestone="0.3" if minor else "0.2.x",
        phase=phase,
    )
    assert result == prs[0 if minor else 1]


@pytest.mark.parametrize(
    "field,value",
    [
        ("baseRefName", "release/0.2"),
        ("headRefName", "release-please-0.3-lookalike"),
        ("headRepository", {"nameWithOwner": "attacker/vectora"}),
        ("headRepository", None),
        ("labels", []),
        ("title", "chore(master): release 0.4.0"),
    ],
)
def test_untrusted_pr_is_ignored(field: str, value: object) -> None:
    """Nome parecido, fork ou metadados divergentes nunca autorizam mutações."""
    data = _pr("0.3.0").model_dump()
    data[field] = value
    pr = ReleasePullRequest.model_validate(data)
    assert (
        select_release_pr(
            [pr],
            repository="vectora-ltda/vectora",
            source="release/0.3",
            target="master",
            milestone="0.3",
            phase="versioned",
        )
        is None
    )


def test_duplicate_release_prs_fail_closed() -> None:
    """Duas PRs válidas da mesma linha exigem resolver a ambiguidade."""
    with pytest.raises(ValueError, match="ambiguous"):
        select_release_pr(
            [_pr("0.2.1"), _pr("0.2.2")],
            repository="vectora-ltda/vectora",
            source="master",
            target="master",
            milestone="0.2.x",
            phase="versioned",
        )


def test_generated_head_must_match_source() -> None:
    """A base correta não autoriza uma branch gerada para outra linha."""
    pr = _pr("0.3.0", generated=True).model_copy(
        update={"headRefName": "release-please--branches--master--components--vectora"}
    )
    assert (
        select_release_pr(
            [pr],
            repository="vectora-ltda/vectora",
            source="release/0.3",
            target="master",
            milestone="0.3",
            phase="generated",
        )
        is None
    )


def test_cli_rejects_malformed_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """JSON inválido interrompe o pipeline antes de alterar branches."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "select_release_pr.py",
            "--repository",
            "vectora-ltda/vectora",
            "--source",
            "master",
            "--target",
            "master",
            "--milestone",
            "0.2.x",
            "--phase",
            "versioned",
        ],
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert selector.main() == 1
    assert "Invalid release PR selection" in capsys.readouterr().err
