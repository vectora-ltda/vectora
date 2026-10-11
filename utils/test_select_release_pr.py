"""Valida isolamento de releases concorrentes e seleção de PRs confiáveis."""

from __future__ import annotations

import io
import sys

import pytest
import select_release_pr as selector
from select_release_pr import ReleasePullRequest, select_release_pr


def _pr(version: str) -> ReleasePullRequest:
    """Cria uma resposta do CLI para a minor ou o patch em master."""
    minor = version.endswith(".0")
    suffix = "0.3" if minor else version
    return ReleasePullRequest.model_validate(
        {
            "number": 3 if minor else 20,
            "baseRefName": "master",
            "headRefName": f"release/{suffix}",
            "headRefOid": "a" * 40,
            "headRepository": {"nameWithOwner": "vectora-ltda/vectora"},
            "labels": [{"name": "autorelease: pending"}],
            "title": f"chore(master): release {version}",
        }
    )


@pytest.mark.parametrize("minor", [False, True])
def test_parallel_lines_are_isolated(minor: bool) -> None:
    """A PR mais recente do patch não é selecionada durante a minor e vice-versa."""
    prs = [
        _pr("0.3.0"),
        _pr("0.2.1"),
    ]
    result = select_release_pr(
        prs,
        repository="vectora-ltda/vectora",
        source="release/0.3" if minor else "master",
        target="master",
        milestone="0.3" if minor else "0.2.x",
    )
    assert result == prs[0 if minor else 1]


@pytest.mark.parametrize(
    "field,value",
    [
        ("baseRefName", "release/0.2"),
        ("headRefName", "release-0.3-lookalike"),
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
        )


def test_noncanonical_release_head_is_not_a_versioned_release_pr() -> None:
    """Somente a branch canônica da linha pode virar a PR oficial."""
    pr = _pr("0.3.0").model_copy(update={"headRefName": "release-0.3"})
    assert (
        select_release_pr(
            [pr],
            repository="vectora-ltda/vectora",
            source="release/0.3",
            target="master",
            milestone="0.3",
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
        ],
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert selector.main() == 1
    assert "Invalid release PR selection" in capsys.readouterr().err
